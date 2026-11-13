"""
hardware_recommend.py — Motor de recomendaciones (M6 / A2, diseño §4).

Convierte una HardwareSnapshot (+ la ficha opcional de un modelo) en un draft
de parámetros compatible con LaunchConfig: el frontend lo mergea sobre los
defaults y el usuario lo guarda como template normal.

Principios duros (del diseño aprobado):
- Solo se emiten parámetros que el launcher REALMENTE puede emitir. Nada de
  --moe-cache-mib ni flags que LaunchConfig no expone.
- El draft se valida contra LaunchConfig antes de devolverse; si algo no
  valida, se descarta entero y se reporta en `warnings` (nunca un draft roto).
- Los umbrales viven aquí como constantes con nombre, no regados por el código.

El fit se verifica con el mismo estimador del semáforo del Launcher
(vram_estimate.estimate_vram_usage), así recomendación y semáforo nunca
discrepan en el encaje base (pesos + KV + compute). A2F-1 añade además la
cuenta de los componentes auxiliares que la ficha del modelo trae de hecho
(draft MTP y mmproj de visión): se presupuestan con su tamaño REAL de archivo
o, si no hay archivo que medir, con la aproximación del propio estimador
(vram_estimate.estimate_draft_aux_gb). LL-4 U3: el presupuesto del draft es ya
compartido para cualquier modo draft, pero la PROPUESTA automática de fit
sigue siendo MTP-única en v1 — hoy MTP es el único draft auto-descubrible con
confianza; dflash/simple/eagle3 se presupuestan en /vram-estimate y se eligen
a mano. El semáforo sigue mostrando solo la estimación base con su aviso
explícito `mtp_available` (EST-SWA); aquí el draft directamente no propone el
componente si no cabe.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from launcher import LaunchConfig
from vram_estimate import estimate_draft_aux_gb, estimate_vram_usage

from hardware_detection import GpuInfo, HardwareSnapshot

logger = logging.getLogger("glyvex.hardware.recommend")

# Clase media para drafts sin model_id: ~20B Q4_K_M con arquitectura densa
# típica. El draft se marca con la advertencia generic_draft (no es un fit real).
GENERIC_WEIGHTS_GB = 12.0
GENERIC_META: dict[str, Any] = {
    "n_layer": 48,
    "n_head": 32,
    "n_embd": 4096,
    "n_head_kv": 8,
}

# Escala de contextos sugeribles (subconjunto de LEGEND_CTXS del estimador).
# Es el techo del draft SIN contexto de entrenamiento conocido (clase genérica).
CTX_LADDER: tuple[int, ...] = (32768, 16384, 8192, 4096)

# Pasos extendidos (A2F-1): solo entran si la ficha del modelo declara
# context_length >= el paso Y la estimación completa cabe bajo FIT_RATIO. El
# caso que los motivó: gemma-4-26B-A4B (n_ctx_train 262144) con la escalera
# vieja NUNCA habría propuesto 131K aunque cupiera al 75% (medido en vivo).
# Tope duro 131072 aunque context_length sea mayor: 256K no está verificado
# en vivo y a esa altura llama.cpp añade riesgos de memoria propios.
CTX_LADDER_EXTENDED: tuple[int, ...] = (131072, 65536)

# Con menos VRAM que esto, el draft no sube de 16k aunque el cálculo numérico
# permita 32k: en 8-12 GB el colchón de drivers/desktop come bastante (diseño §4.2).
LOW_VRAM_CAP_CTX = 12.0   # GB
LOW_VRAM_MAX_CTX = 16384

# Fit completo: pesos + KV + compute deben quedar bajo este % de la VRAM total.
FIT_RATIO = 0.95
# El coste del draft MTP ya no es una constante aquí: se presupuesta con el
# helper compartido vram_estimate.estimate_draft_aux_gb (LL-4 U3), que usa el
# buffer FA del propio estimador y los datos reales del archivo cuando los hay
# (los valores medidos en vivo con b11827 quedaron documentados ahí).
# Con GPU pero sin fit completo: se intenta partial offload por capas mientras
# el KV + compute no supere esta fracción de la VRAM (el resto es para pesos).
HYBRID_KV_BUDGET_RATIO = 0.5
# CPU-only: el peso estimado no debe pasar este fraction de la RAM disponible.
CPU_WEIGHT_RATIO = 0.75
# CPU-only: total estimado (pesos + KV) máximo como fraction de la RAM disponible.
CPU_TOTAL_RATIO = 0.80
# Con GPU, MoE o denso: con menos de este ratio weights/VRAM hay chances de
# partial offload útil; por encima, el draft directamente baja a cpu_only.
MIN_USEFUL_OFFLOAD_RATIO = 1.15


def best_gpu(snapshot: HardwareSnapshot) -> GpuInfo | None:
    """La GPU con más VRAM CONOCIDA. GPUs sin vram_total (Intel WDDM, registro
    sin NVML) quedan fuera del motor por diseño."""
    known = [g for g in snapshot.gpus if g.vram_total_mb]
    if not known:
        return None
    return max(known, key=lambda g: g.vram_total_mb)


def _model_inputs(
    model: dict[str, Any] | None,
) -> tuple[dict[str, Any], float, bool, bool]:
    """(meta, weights_gb, is_moe, generic_arch).

    generic_arch = True cuando hay ficha pero su metadata GGUF no es utilizable:
    el fit usa la arquitectura genérica pero conserva el peso real (es el dato
    que sí tenemos). Debe quedar visible en warnings, no silencioso."""
    if not model:
        return dict(GENERIC_META), GENERIC_WEIGHTS_GB, False, False
    meta = dict(model.get("metadata") or {})
    weights = float(model.get("size_gb") or 0.0) or GENERIC_WEIGHTS_GB
    generic_arch = False
    if not (meta.get("n_layer") and meta.get("n_head") and meta.get("n_embd")):
        meta = dict(GENERIC_META)
        generic_arch = True
    return meta, weights, bool(meta.get("is_moe")), generic_arch


def _model_ladder(ctx_train: int | None) -> tuple[int, ...]:
    """Escalera que la ficha del modelo habilita, en orden descendente. SIN
    context_length declarado (draft genérico o metadata inutilizable) queda en
    el techo clásico de 32k: no se inventa un entrenamiento que no se conoce.
    CON contexto declarado, la escalera se corta ahí en TODOS los peldaños —
    proponer más contexto que el entrenamiento del modelo degrada la calidad,
    y el motor no hace rope scaling para compensarlo. Si el entrenamiento
    declarado es más chico que el peldaño mínimo, se conserva ese mínimo
    (4096): es el suelo razonable del draft y llama.cpp lo gestiona."""
    if not ctx_train:
        return CTX_LADDER
    capped = tuple(c for c in CTX_LADDER_EXTENDED + CTX_LADDER if c <= int(ctx_train))
    return capped or (CTX_LADDER[-1],)


def _ctx_ladder(vram_gb: float, ctx_train: int | None = None) -> tuple[int, ...]:
    # Ojo con el <=: una placa de exactamente 12 GB (12288 MB) es parte de la
    # clase "8–12 GB" del diseño y también queda cortada en 16k. El recorte
    # aplica también a los pasos extendidos (A2F-1): en 8–12 GB el colchón de
    # drivers/desktop no cambia por mucho que el modelo soporte 128K.
    ladder = _model_ladder(ctx_train)
    if vram_gb <= LOW_VRAM_CAP_CTX:
        return tuple(c for c in ladder if c <= LOW_VRAM_MAX_CTX)
    return ladder


def _estimate(
    meta: dict[str, Any], weights_gb: float, ctx: int, cache_type: str,
    vram_gb: float,
) -> dict[str, Any] | None:
    est = estimate_vram_usage(
        meta, weights_gb, ctx,
        cache_type_k=cache_type, cache_type_v=cache_type,
        n_parallel=1, flash_attn=True, gpu_vram_gb=vram_gb,
    )
    return est if est.get("available") else None


def _model_aux(
    model: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(mtp, vision) — los componentes auxiliares que la ficha trae de hecho.

    Solo se cuentan componentes reales: sidecar con archivo y tamaño medidos
    (hardware_api los resuelve desde el inventario) o cabeza MTP embebida (sus
    tensores ya están dentro de weights_gb). Un mmproj sin tamaño legible no
    entra: sin dato no hay fit honesto que hacer."""
    if not model:
        return None, None
    mtp: dict[str, Any] | None = None
    sidecar = model.get("mtp_draft_model")
    if sidecar:
        mtp = {
            "path": str(sidecar),
            "weights_gb": float(model.get("mtp_draft_size_gb") or 0.0),
            "metadata": model.get("mtp_draft_metadata") or None,
            # Capas declaradas del head (U6): el sidecar auto-detectado es una
            # copia de la cabeza MTP que el base declara y su header suele
            # copiar los layers completos del base.
            "max_layers": model.get("mtp_embedded_layers"),
        }
    elif model.get("mtp_embedded"):
        mtp = {
            "path": None, "weights_gb": 0.0, "metadata": None,
            "max_layers": model.get("mtp_embedded_layers"),
        }
    vision: dict[str, Any] | None = None
    mmproj_path = model.get("mmproj_path")
    mmproj_size = float(model.get("mmproj_size_gb") or 0.0)
    if mmproj_path and mmproj_size > 0:
        vision = {"path": str(mmproj_path), "weights_gb": mmproj_size}
    return mtp, vision


def _plan_gpu(
    params: dict[str, Any], rationale: list[dict], warnings: list[dict],
    gpu: GpuInfo, meta: dict[str, Any], weights_gb: float, is_moe: bool,
    model_specific: bool, aux_mtp: dict[str, Any] | None,
    aux_vision: dict[str, Any] | None, ram_available_gb: float | None,
) -> None:
    vram_gb = float(gpu.vram_total_mb or 0.0) / 1024.0
    ctx_train = int(meta.get("context_length") or 0) or None

    # 1) Encaje completo (gpu_only): el mayor ctx cuya estimación total quede
    #    bajo FIT_RATIO de la VRAM. Es más estricto que el "≥55% del peso"
    #    del diseño: exige que quepa también el KV, que es lo que revienta.
    #    A2F-1: la escalera puede extenderse hasta 131K si la ficha declara
    #    context_length (ver _model_ladder).
    for ctx in _ctx_ladder(vram_gb, ctx_train):
        est = _estimate(meta, weights_gb, ctx, "q8_0", vram_gb)
        if est is not None and est["total_gb"] <= vram_gb * FIT_RATIO:
            limit = vram_gb * FIT_RATIO
            total = est["total_gb"]  # bundle acumulado: base + aux emitidos
            params["gpu_mode"] = "gpu_only"
            params["n_gpu_layers"] = -1
            params["n_ctx"] = ctx
            rationale.append({"param": "n_ctx", "key": "max_fit",
                             "args": {"ctx": ctx, "vram_gb": round(vram_gb, 1),
                                      "pct": est["pct"]}})
            # 2) Draft MTP (A2F-1): se emite solo si su coste real entra en el
            #    presupuesto del ctx elegido, con el helper compartido del
            #    estimador. Antes la regla era un umbral fijo "pct ≤ 60" que
            #    ignoraba cuánto cuesta el draft de verdad; con el presupuesto
            #    explícito el umbral extra es redundante. OJO (LL-4 U3): la
            #    propuesta automática sigue siendo MTP-only en v1 — es el único
            #    draft auto-descubrible con confianza; los demás modos draft se
            #    presupuestan igual en /vram-estimate pero se eligen a mano
            #    (extender el fit a dflash requeriría oferta HF de sidecars).
            #    No se baja de ctx para hacer hueco al draft: el contexto
            #    manda sobre la velocidad de especulación (el usuario puede
            #    activar la especulación a mano con otro ctx).
            if aux_mtp is not None:
                mtp_gb = estimate_draft_aux_gb(
                    meta, ctx,
                    float(aux_mtp.get("weights_gb") or 0.0),
                    aux_mtp.get("metadata"),
                    base_size_gb=float(weights_gb),
                    max_layers=aux_mtp.get("max_layers"),
                )
                if mtp_gb is not None:
                    new_total = total + mtp_gb
                    if new_total <= limit:
                        if aux_mtp["path"]:
                            params["mtp_draft_model"] = aux_mtp["path"]
                        else:
                            params["mtp_embedded"] = True
                        rationale.append({"param": "mtp_draft_model" if aux_mtp["path"]
                                          else "mtp_embedded",
                                          "key": "mtp_fit",
                                          "args": {"aux_gb": round(mtp_gb, 2),
                                                  "pct": round(100.0 * new_total / vram_gb, 1)}})
                        total = new_total
                    else:
                        warnings.append({"key": "mtp_no_room",
                                         "args": {"aux_gb": round(mtp_gb, 2),
                                                 "pct": round(100.0 * new_total / vram_gb, 1)}})
            # 3) Visión: solo con mmproj medido, y solo si el peso real del
            #    archivo entra en el presupuesto. Sus activaciones de encoder no
            #    se modelan aparte: el margen FIT_RATIO las cubre (el único
            #    overhead extra observado en llama.cpp es de tamaño moderado).
            if aux_vision is not None:
                vis_gb = aux_vision["weights_gb"]
                new_total = total + vis_gb
                if new_total <= limit:
                    params["mmproj_path"] = aux_vision["path"]
                    rationale.append({"param": "mmproj_path", "key": "vision_fit",
                                      "args": {"mmproj_gb": round(vis_gb, 2),
                                              "pct": round(100.0 * new_total / vram_gb, 1)}})
                    total = new_total
                else:
                    warnings.append({"key": "vision_no_room",
                                     "args": {"mmproj_gb": round(vis_gb, 2),
                                             "pct": round(100.0 * new_total / vram_gb, 1)}})
            # 4) KV f16: se re-estima el MISMO bundle con caché f16 y se emite
            #    solo si sigue bajo FIT_RATIO. Esto reemplaza el viejo umbral
            #    fijo KV_HEADROOM_PCT sobre la estimación q8_0, que era ciego
            #    al patrón por capa: con SWA el KV es pequeño y f16 casi siempre
            #    cabe; con KV grande la propia re-estimación lo descarta.
            #    El KV del draft NO cambia: son flags propios (--spec-draft-type-*)
            #    que el draft deja en su default q8_0.
            if vram_gb >= 16:
                est_f16 = _estimate(meta, weights_gb, ctx, "f16", vram_gb)
                aux_emitted = total - est["total_gb"]
                if est_f16 is not None and est_f16["total_gb"] + aux_emitted <= limit:
                    params["cache_type_k"] = "f16"
                    params["cache_type_v"] = "f16"
                    rationale.append({"param": "cache_type_k", "key": "headroom_f16",
                                      "args": {"pct": round(100.0 * (est_f16["total_gb"]
                                                                    + aux_emitted) / vram_gb, 1)}})
            return

    # 5) Sin encaje completo: partial offload por capas (hybrid). El KV del ctx
    #    elegido no puede pasar HYBRID_KV_BUDGET_RATIO de la VRAM; el resto del
    #    presupuesto es para la fracción de pesos que se queda en GPU. Los
    #    auxiliares (MTP/visión) no se presupuestan acá: el modo parcial ya es
    #    un ajuste fino que el usuario revisa, y añadir componentes encima de un
    #    fit incompleto sería una conjetura.
    for ctx in _ctx_ladder(vram_gb, ctx_train):
        est = _estimate(meta, weights_gb, ctx, "q8_0", vram_gb)
        if est is None:
            continue
        kv_compute_gb = est["kv_gb"] + est["ssm_gb"] + est["compute_gb"]
        if kv_compute_gb > vram_gb * HYBRID_KV_BUDGET_RATIO:
            continue
        weights_budget = vram_gb * FIT_RATIO - kv_compute_gb
        n_layer = int(meta["n_layer"])
        # Distribución uniforme de pesos por capa: para MoE es una
        # aproximación (los expertos concentran el peso), admitida y
        # documentada — el usuario ajusta el número en la UI.
        fit_layers = math.floor(n_layer * weights_budget / weights_gb) if weights_gb > 0 else 0
        if weights_gb / vram_gb < MIN_USEFUL_OFFLOAD_RATIO:
            fit_layers = max(fit_layers, 1)
        if fit_layers < 1:
            continue
        params["gpu_mode"] = "hybrid"
        params["n_ctx"] = ctx
        if is_moe:
            # En MoE las capas siguen en GPU y lo que baja es la caché de
            # expertos: --n-cpu-moe N (N capas de expertos en RAM).
            n_cpu_moe = max(1, n_layer - fit_layers)
            params["n_gpu_layers"] = -1
            params["n_cpu_moe"] = n_cpu_moe
            rationale.append({"param": "n_cpu_moe", "key": "moe_experts_cpu",
                              "args": {"layers": n_cpu_moe, "vram_gb": round(vram_gb, 1)}})
        else:
            params["n_gpu_layers"] = fit_layers
            rationale.append({"param": "n_gpu_layers", "key": "partial_fit",
                              "args": {"layers": fit_layers, "n_layer": n_layer,
                                       "vram_gb": round(vram_gb, 1)}})
        return

    # 6) GPU presente pero el modelo no tiene ninguna configuración útil aquí:
    #    cpu_only con el presupuesto de RAM real de la máquina.
    warnings.append({"key": "model_not_fit_gpu",
                     "args": {"vram_gb": round(vram_gb, 1), "weights_gb": round(weights_gb, 1)}})
    _plan_cpu_only(params, rationale, warnings, meta, weights_gb, model_specific,
                   ram_available_gb)


def _plan_cpu_only(
    params: dict[str, Any], rationale: list[dict], warnings: list[dict],
    meta: dict[str, Any], weights_gb: float, model_specific: bool,
    ram_available_gb: float | None,
) -> None:
    params["gpu_mode"] = "cpu_only"
    params["n_gpu_layers"] = 0

    # n_threads: núcleos físicos, nunca lógicos con SMT por defecto (diseño §4.4).
    # Physical=None (VMs, algunas CPU) => se omite y la build usa su auto.
    threads = meta.get("_physical_cores")
    if isinstance(threads, int) and threads >= 1:
        params["n_threads"] = threads
        rationale.append({"param": "n_threads", "key": "physical_cores",
                         "args": {"cores": threads}})

    # En CPU el techo lo pone la RAM, no la VRAM: la escalera se extiende con
    # los pasos que la ficha del modelo declara (mismo criterio _model_ladder
    # que en GPU; sin context_length declarado sigue en 32k).
    ladder = _model_ladder(int(meta.get("context_length") or 0) or None)

    if ram_available_gb is not None and ram_available_gb > 0:
        chosen: int | None = None
        for ctx in ladder:
            est = _estimate(meta, weights_gb, ctx, "q8_0", 0.0)
            if est is not None and est["total_gb"] <= ram_available_gb * CPU_TOTAL_RATIO:
                chosen = ctx
                break
        if chosen is not None:
            params["n_ctx"] = chosen
            rationale.append({"param": "n_ctx", "key": "max_fit_ram",
                              "args": {"ctx": chosen, "ram_gb": round(ram_available_gb, 1)}})
        else:
            params["n_ctx"] = ladder[-1]
            warnings.append({"key": "ram_tight",
                             "args": {"ram_gb": round(ram_available_gb, 1),
                                      "weights_gb": round(weights_gb, 1)}})
        if weights_gb > ram_available_gb * CPU_WEIGHT_RATIO:
            warnings.append({"key": "quant_suggested",
                             "args": {"weights_gb": round(weights_gb, 1),
                                      "ram_gb": round(ram_available_gb, 1)}})


def recommend(
    snapshot: HardwareSnapshot, model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draft de parámetros + justificación + advertencias.

    `model` es la ficha opcional ({size_gb, metadata, mtp_embedded,
    mtp_draft_model/mtp_draft_size_gb, mmproj_path/mmproj_size_gb}); sin ella
    el draft usa la clase media genérica y se marca como genérico."""
    params: dict[str, Any] = {}
    rationale: list[dict] = []
    warnings: list[dict] = []

    model_specific = model is not None
    if not model_specific:
        warnings.append({"key": "generic_draft"})

    meta, weights_gb, is_moe, generic_arch = _model_inputs(model)
    aux_mtp, aux_vision = _model_aux(model)
    if generic_arch:
        # Ficha real pero metadata inutilizable: honestidad con el usuario —
        # el fit es de arquitectura genérica (no un fit real del modelo).
        warnings.append({"key": "generic_architecture"})

    ram_gb = (snapshot.ram.available_mb / 1024.0) if (snapshot.ram and snapshot.ram.available_mb) else None
    if snapshot.cpu is not None:
        # n_threads para cpu_only sale de los núcleos físicos de la foto.
        meta = dict(meta)
        meta["_physical_cores"] = snapshot.cpu.physical_cores

    gpu = best_gpu(snapshot)
    if gpu is None:
        if any(g.vram_total_mb is None for g in snapshot.gpus):
            # GPU listada pero sin VRAM conocida (Intel WDDM): fuera del motor.
            warnings.append({"key": "no_vram_source"})
        _plan_cpu_only(params, rationale, warnings, meta, weights_gb,
                       model_specific, ram_gb)
    else:
        _plan_gpu(params, rationale, warnings, gpu, meta, weights_gb, is_moe,
                  model_specific, aux_mtp, aux_vision, ram_gb)

    # El draft debe ser un LaunchConfig válido ANTES de llegar al frontend.
    try:
        LaunchConfig(model_id="__draft__", **params)
    except Exception as exc:  # noqa: BLE001 — nunca un draft roto al usuario
        logger.warning("draft de recomendación inválido: %s", exc)
        return {
            "params": {},
            "rationale": [],
            "warnings": [{"key": "draft_invalid"}],
            "inputs": _inputs(snapshot, weights_gb, model_specific),
        }

    return {
        "params": params,
        "rationale": rationale,
        "warnings": warnings,
        "inputs": _inputs(snapshot, weights_gb, model_specific),
    }


def _inputs(
    snapshot: HardwareSnapshot, weights_gb: float, model_specific: bool,
) -> dict[str, Any]:
    gpu = best_gpu(snapshot)
    return {
        "vram_gb": round(gpu.vram_total_mb / 1024.0, 1) if gpu and gpu.vram_total_mb else None,
        "ram_available_gb": round(snapshot.ram.available_mb / 1024.0, 1) if (snapshot.ram and snapshot.ram.available_mb) else None,
        "weights_gb": round(weights_gb, 1),
        "model_specific": model_specific,
        "gpu_family": snapshot.gpu_family,
    }

"""
vram_estimate.py — Estimación de uso de VRAM para un lanzamiento (soporte de M2).

Cálculo deliberadamente simple y conservador (todo en GPU, sin offload):

    total = pesos (tamaño del .gguf en disco)
          + KV cache (2 × n_attn × n_head_kv × head_dim × n_ctx × slots × bytes/elemento)
          + estado recurrente SSM (fijo, solo en modelos híbridos)
          + buffer de cómputo fijo

Los modelos híbridos SSM/atención (Qwen3-Next/Qwen3.5: full_attention_interval=N)
solo tienen KV que crece con el contexto en 1 de cada N capas; el resto son SSM
con estado recurrente FIJO (no crece con n_ctx). Contar todas las capas como
atención sobreestimaba el KV ~4× (ver _ssm_state_bytes).

La clasificación (comodo / justo / no cabe) compara el total con la VRAM de la
GPU declarada en config (hardware.vram_gb). Con VRAM sin declarar el resultado
sigue siendo útil (muestra el desglose) pero el estado queda "unknown".

Función pura sobre el dict de `read_gguf_metadata`: sin I/O, sin GPU y sin
llama.cpp, así que los tests corren con números de modelos reales y sin mundo
real.
"""

from __future__ import annotations

from typing import Any

# Bytes por elemento del cache K/V por tipo. La cuantización bloqueada de
# llama.cpp usa QK_K=32 elementos por bloque + un escala fp16 (2 bytes):
# q8_0 = (32×1 + 2)/32 = 1.0625 · q4_0 = (16 + 2)/32 = 0.5625 · q4_1 = (16+4)/32.
CACHE_TYPE_BYTES = {
    "f16": 2.0,
    "bf16": 2.0,
    "f32": 4.0,
    "q8_0": 34.0 / 32.0,
    "q4_0": 18.0 / 32.0,
    "q4_1": 20.0 / 32.0,
}

DEFAULT_CACHE_BYTES = 2.0
# Activaciones + workspace de la inference. Un valor fijo a propósito: depende
# poco del modelo y mucho del batch; el margen real lo cubre la clasificación.
COMPUTE_BUFFER_GB = 1.5
# Con flash attention el kernel de atención trabaja por tiles y no materializa
# la matriz completa de scores (que sin FA crece con el ctx), así que el buffer
# de cómputo baja. Valor medido en una run real (RTX 3090, Qwen3.8-27B, 128K
# ctx, FA on): ~720 MiB, redondeado a 0.75 GB. A ctx muy largo el ahorro crece,
# así que esto es un piso, no un tope. FA no toca el KV cache (K/V se guardan
# igual): el ajuste va solo sobre el cómputo.
COMPUTE_BUFFER_GB_FA = 0.75

# Comodo por debajo de este % de la VRAM declarada; de ahí al 100% es "justo".
COMFY_RATIO = 80.0

# Contextos comunes para la leyenda del estimador (la UI muestra "con X de
# contexto sale Y de VRAM"). Espeja N_CTX_PRESETS del frontend (Launcher.jsx):
# si cambian los presets de la UI, actualizar esto a la par.
LEGEND_CTXS = (4096, 8192, 16384, 32768, 65536, 131072, 262144)

_GIB = 1024 ** 3


def _ssm_state_bytes(meta: dict[str, Any], n_ssm: int) -> int:
    """
    Estado recurrente FIJO de las capas SSM: no crece con n_ctx. Dominado por
    la matriz de estado (ssm.inner_size × ssm.state_size) más el buffer conv,
    en fp32 (el estado se acumula en precisión completa aunque los pesos estén
    cuantizados). Qwen3.8: 6144×(128+4)×4 ≈ 3.1 MiB/capa. Sin los params SSM
    devuelve 0 (se trata como denso: mejor subestimar ~150 MiB que inventar).
    """
    if not n_ssm:
        return 0
    inner = meta.get("ssm_inner_size")
    state = meta.get("ssm_state_size")
    if not inner or not state:
        return 0
    conv = meta.get("ssm_conv_kernel") or 0
    per_layer = int(inner) * (int(state) + int(conv)) * 4
    return int(n_ssm) * per_layer


def estimate_vram_usage(
    meta: dict[str, Any],
    model_size_gb: float,
    n_ctx: int,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    n_parallel: int = 1,
    flash_attn: bool = False,
    gpu_vram_gb: float = 0,
) -> dict[str, Any]:
    """
    Estimar la VRAM total (GB) de un lanzamiento y clasificarla.

    `meta` es el dict de `read_gguf_metadata` (puede traer campos None: se
    cae a defaults razonables, como n_head_kv → n_head). Nunca levanta:
    sin los campos mínimos devuelve {"available": False, "error": ...}.
    """
    n_layer = meta.get("n_layer")
    n_head = meta.get("n_head")
    n_embd = meta.get("n_embd")
    if not n_layer or not n_head or not n_embd:
        return {
            "available": False,
            "error": "metadata del GGUF sin n_layer/n_head/n_embd: no se puede estimar la VRAM",
        }

    # Sin GQA (head_count_kv ausente) cada head tiene su propio KV (MHA).
    n_head_kv = meta.get("n_head_kv") or n_head
    head_dim = meta.get("head_dim") or max(1, n_embd // n_head)
    bytes_k = CACHE_TYPE_BYTES.get(cache_type_k, DEFAULT_CACHE_BYTES)
    bytes_v = CACHE_TYPE_BYTES.get(cache_type_v, DEFAULT_CACHE_BYTES)
    slots = max(1, int(n_parallel))

    # Híbrido SSM/atención: con full_attention_interval=N solo 1 de cada N capa
    # lleva KV que crece con el contexto; las demás son SSM con estado recurrente
    # FIJO (no crece). Sin el campo → modelo denso clásico (todas de atención).
    interval = meta.get("full_attention_interval")
    n_attn = n_layer
    n_ssm = 0
    if interval and int(interval) > 1:
        n_attn = n_layer // int(interval)
        n_ssm = n_layer - n_attn

    # 2 = K + V; solo las capas de atención; cada slot lleva su KV completo.
    kv_bytes = 2 * n_attn * n_head_kv * head_dim * int(n_ctx) * slots * ((bytes_k + bytes_v) / 2.0)
    ssm_bytes = _ssm_state_bytes(meta, n_ssm)

    weights_gb = float(model_size_gb)
    kv_gb = kv_bytes / _GIB
    ssm_gb = ssm_bytes / _GIB
    compute_gb = COMPUTE_BUFFER_GB_FA if flash_attn else COMPUTE_BUFFER_GB
    total_gb = weights_gb + kv_gb + ssm_gb + compute_gb

    state = "unknown"
    pct: float | None = None
    if gpu_vram_gb and gpu_vram_gb > 0:
        pct = round(100.0 * total_gb / float(gpu_vram_gb), 1)
        state = "comodo" if pct < COMFY_RATIO else "justo" if pct <= 100.0 else "no_cabe"

    return {
        "available": True,
        "error": None,
        "weights_gb": round(weights_gb, 2),
        "kv_gb": round(kv_gb, 2),
        "ssm_gb": round(ssm_gb, 2),
        "compute_gb": round(compute_gb, 2),
        "total_gb": round(total_gb, 2),
        "gpu_vram_gb": float(gpu_vram_gb) if gpu_vram_gb else None,
        "pct": pct,
        "state": state,
        "is_moe": bool(meta.get("is_moe")),
        "is_hybrid": n_ssm > 0,
        "n_attn_layers": int(n_attn),
        "n_ssm_layers": int(n_ssm),
        "n_ctx": int(n_ctx),
        "n_parallel": slots,
        "flash_attn": bool(flash_attn),
    }


def estimate_vram_legend(
    meta: dict[str, Any],
    model_size_gb: float,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    n_parallel: int = 1,
    flash_attn: bool = False,
    gpu_vram_gb: float = 0,
    ctx_values: tuple[int, ...] = LEGEND_CTXS,
) -> list[dict[str, Any]]:
    """
    Leyenda para la UI: para cada n_ctx común, el total de VRAM y su
    clasificación (comodo/justo/no_cabe). Mismo modelo y mismos parámetros que
    el estimador principal, variando solo el contexto. Devuelve [] si la
    metadata no alcanza (mismo criterio que estimate_vram_usage).
    """
    legend: list[dict[str, Any]] = []
    for n_ctx in ctx_values:
        r = estimate_vram_usage(
            meta, model_size_gb, n_ctx,
            cache_type_k=cache_type_k, cache_type_v=cache_type_v,
            n_parallel=n_parallel, flash_attn=flash_attn, gpu_vram_gb=gpu_vram_gb,
        )
        if not r.get("available"):
            break  # sin metadata mínima no hay leyenda que mostrar
        legend.append({
            "n_ctx": r["n_ctx"],
            "total_gb": r["total_gb"],
            "state": r["state"],
            "pct": r["pct"],
        })
    return legend

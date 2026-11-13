"""
vram_estimate.py — Estimación de uso de VRAM para un lanzamiento (soporte de M2).

Cálculo deliberadamente simple y conservador (todo en GPU, sin offload):

    total = pesos (tamaño del .gguf en disco)
          + KV cache (Σ por capa de atención: ventana × kv_heads × head_dim ×
            bytes — full n_ctx en atención completa, sliding_window en capas
            marcadas por sliding_window_pattern)
          + estado recurrente SSM (fijo, solo en modelos híbridos)
          + buffer de cómputo fijo

Los modelos híbridos SSM/atención (Qwen3-Next/Qwen3.5: full_attention_interval=N)
solo tienen KV que crece con el contexto en 1 de cada N capas; el resto son SSM
con estado recurrente FIJO (no crece con n_ctx). Contar todas las capas como
atención sobreestimaba el KV ~4× (ver _ssm_state_bytes).

Los modelos con Sliding Window Attention (gemma4/gemma3, mistral) tienen capas
cuyo KV NUNCA supera la ventana (1024 tokens en gemma4) y además cambian
cabezales y dims KV POR CAPA (head_count_kv es un array; las capas sliding usan
key_length_swa=256 y las full key_length=512). Contar todo al ctx completo con
el primer elemento del array sobreestimaba el KV de Gemma-4 a 128k ~21×
(31.9 GB estimados vs 1.5 GB medidos por llama.cpp). Sin esos campos el
comportamiento es idéntico al anterior: modelo denso (EST-SWA).

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

# --- Presupuesto del draft especulativo (LL-4 U3) ----------------------------
# Modos que cargan un .gguf de draft junto al modelo base. Lista canónica
# compartida por el presupuesto de VRAM (models.py, hardware_recommend.py) y
# el launcher: vive aquí porque models.py NO puede importar launcher (launcher
# importa models). Debe coincidir con launcher.SpecType.
SPEC_DRAFT_MODES = frozenset({"draft-mtp", "draft-dflash", "draft-simple", "draft-eagle3"})
# Modos que NO suman pesos a la VRAM: su lookup vive en RAM-CPU (ngram) o no
# hay especulación (none). El semáforo no debe desaconsejarlos por VRAM.
SPEC_NO_AUX_MODES = frozenset({"none", "ngram-simple", "ngram-mod"})

# Guard físico contra "headers copiados" en sidecars de draft (LL-4 U6). Ciertos
# adaptadores MTP declaran en su header la arquitectura COMPLETA del modelo base
# en vez de la suya — caso real medido 2026-11-10: mtp-Qwen3.8-27B-Q4_0.gguf
# (1.28 GB) declara los 65 layers y las dims completas de Qwen3.8-27B (16.35 GB)
# y el estimador presupuestaba 6.42 GB frente a ~2.2 medidos. Si el peso que el
# header del draft implicaría al quant del base supera DRAFT_HEADER_GUARD_RATIO
# veces el tamaño real del archivo, esas capas no existen: se corta a lo que el
# archivo cabe físicamente (ver _clamp_draft_layers). La tolerancia de 8× deja
# pasar un draft legítimamente de quant más alto que el base (un Q8 junto a un
# Q4 pesa ~2× el equivalente; 8× es holgura, no licencia para inflar).
DRAFT_HEADER_GUARD_RATIO = 8.0

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
    aux_weights_gb: float = 0.0,
    aux_components: list[dict[str, Any]] | None = None,
    draft_aux: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Estimar la VRAM total (GB) de un lanzamiento y clasificarla.

    `meta` es el dict de `read_gguf_metadata` (puede traer campos None: se
    cae a defaults razonables, como n_head_kv → n_head). Nunca levanta:
    sin los campos mínimos devuelve {"available": False, "error": ...}.

    VRAM-MMPROJ: `aux_weights_gb` son los pesos de componentes auxiliares que
    el launch carga además del .gguf principal (mmproj de visión, draft MTP
    sidecar — tamaños REALES de archivo los mide el llamador desde el
    inventario) y `aux_components` es su desglose [{"kind","label","size_gb"}].
    Se suman al total y se publican aparte para que la UI pueda decir «si no
    usas visión/MTP, el uso real será menor». Con aux=0 el resultado es
    idéntico al cálculo anterior (los componentes no medidos no se inventan;
    un MTP embebido NO entra acá: sus tensores ya están contados en
    model_size_gb).

    Draft especulativo (LL-4 U3): `draft_aux` = {"label","weights_gb",
    "metadata", opcionalmente "max_layers"} describe el draft que el launch va
    a cargar en un modo spec_type de SPEC_DRAFT_MODES (pesos 0 si la cabeza va
    embebida; max_layers = capas declaradas por una fuente confiable, ver
    estimate_draft_aux_gb). Su
    coste NO es un peso fijo: se re-estima a este n_ctx con
    estimate_draft_aux_gb (pesos + KV propio q8_0 + cómputo) y se publica
    como componente {"kind": "draft_model"}. Por eso el llamador pasa solo
    los pesos/metadata del archivo y cada fila de la leyenda re-estima el
    suyo. Los modos ngram/none simplemente no pasan draft_aux: suman 0 VRAM.
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

    # Mapa por capa. Híbrido SSM (full_attention_interval=N → 1 de cada N capa
    # es atención) y Sliding Window Attention (sliding_window +
    # sliding_window_pattern + arrays head_count_kv/head_dim_swa): las capas
    # marcadas True reservan KV de min(n_ctx, window) tokens con sus cabezales
    # y dims SWA; las full reservan n_ctx con las dims completas. Sin esos
    # campos el resultado es idéntico al cálculo anterior: modelo denso.
    interval = meta.get("full_attention_interval")
    kv_heads_layers = meta.get("n_head_kv_layers")
    swa_window = meta.get("sliding_window")
    swa_pattern = meta.get("sliding_window_pattern")
    head_dim_swa = meta.get("head_dim_swa")

    n_attn = 0
    n_ssm = 0
    n_swa = 0
    kv_units = 0  # Σ window × kv_heads × head_dim sobre capas de atención
    for i in range(n_layer):
        if interval and int(interval) > 1 and (i + 1) % int(interval) != 0:
            n_ssm += 1  # capa SSM: estado recurrente fijo, no KV que crece
            continue
        n_attn += 1
        # Sin patrón por capa, SWA uniforme (mistral/gemma2: solo el escalar
        # sliding_window) desliza en todas las capas. Un patrón más corto que
        # n_layer se completa como atención completa (no inventar patrones).
        if swa_pattern is not None and i < len(swa_pattern):
            sliding = bool(swa_pattern[i])
        else:
            sliding = swa_window is not None and int(swa_window) > 0
        window = min(int(n_ctx), int(swa_window)) if sliding and swa_window else int(n_ctx)
        kv_heads = kv_heads_layers[i] if kv_heads_layers and i < len(kv_heads_layers) else n_head_kv
        dim = head_dim_swa if (sliding and swa_window and head_dim_swa) else head_dim
        if sliding and swa_window:
            n_swa += 1
        kv_units += window * int(kv_heads) * int(dim)

    # 2 = K + V; solo las capas de atención; cada slot lleva su KV completo.
    kv_bytes = 2 * kv_units * slots * ((bytes_k + bytes_v) / 2.0)
    ssm_bytes = _ssm_state_bytes(meta, n_ssm)

    weights_gb = float(model_size_gb)
    aux_gb = float(aux_weights_gb or 0.0)
    kv_gb = kv_bytes / _GIB
    ssm_gb = ssm_bytes / _GIB
    compute_gb = COMPUTE_BUFFER_GB_FA if flash_attn else COMPUTE_BUFFER_GB
    components = [
        {
            "kind": str(c.get("kind", "aux")),
            "label": str(c.get("label", "")),
            "size_gb": round(float(c.get("size_gb") or 0.0), 2),
        }
        for c in (aux_components or [])
    ]
    if draft_aux is not None:
        # El draft se re-estima para ESTE ctx (su KV propio crece con la fila;
        # tratarlo como peso fijo subestimaría los contextos largos). El tamaño
        # real del base y las capas declaradas viajan al clamp de U6: sin ellos
        # un sidecar con header copiado presupuesta capas que no existen.
        draft_gb = estimate_draft_aux_gb(
            meta, int(n_ctx),
            float(draft_aux.get("weights_gb") or 0.0),
            draft_aux.get("metadata"),
            base_size_gb=float(model_size_gb),
            max_layers=draft_aux.get("max_layers"),
        )
        if draft_gb is not None:
            aux_gb += draft_gb
            components.append({
                "kind": "draft_model",
                "label": str(draft_aux.get("label") or "draft"),
                "size_gb": round(draft_gb, 2),
            })
    total_gb = weights_gb + aux_gb + kv_gb + ssm_gb + compute_gb

    state = "unknown"
    pct: float | None = None
    if gpu_vram_gb and gpu_vram_gb > 0:
        pct = round(100.0 * total_gb / float(gpu_vram_gb), 1)
        state = "comodo" if pct < COMFY_RATIO else "justo" if pct <= 100.0 else "no_cabe"

    return {
        "available": True,
        "error": None,
        "weights_gb": round(weights_gb, 2),
        "aux_gb": round(aux_gb, 2),
        "aux_components": components,
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
        "n_swa_layers": int(n_swa),
        "swa_window": int(swa_window) if (n_swa and swa_window) else None,
        "n_ctx": int(n_ctx),
        "n_parallel": slots,
        "flash_attn": bool(flash_attn),
    }


def _declared_layers(value: Any) -> int:
    """Capas declaradas utilizables (>=1); 0 si el valor no es utilizable."""
    try:
        declared = int(value)
    except (TypeError, ValueError):
        return 0
    return declared if declared >= 1 else 0


def _clamp_draft_layers(
    base: dict[str, Any],
    base_meta: dict[str, Any],
    base_size_gb: float,
    max_layers: int | None,
    weights_gb: float,
) -> None:
    """Ajusta IN-PLACE el nº de capas del draft a las que DE VERDAD tiene
    (LL-4 U6). Dos clamps independientes:

    - `max_layers` (fuente declarada): la arquitectura del modelo base sabe
      cuántas capas MTP/NextN tiene — `{arch}.nextn_predict_layers`, publicado
      en la entry como `mtp_embedded_layers`. Un sidecar MTP auto-detectado es
      una copia de esa cabeza y no puede tener más capas que ella.
    - Guard físico (`base_size_gb` + peso real): un header copiado del base
      (mismas dims, mismas capas) implicaría un draft proporcional al base al
      mismo quant; si ese peso implicado supera DRAFT_HEADER_GUARD_RATIO veces
      el archivo real, las capas sobrantes no existen y se corta a lo que el
      archivo físicamente cabe.

    Al cortar se eliminan del dict las capas del mapa híbrido copiado
    (`full_attention_interval` / `n_head_kv_layers`): en un base híbrido
    (interval=4) las primeras N capas de ese mapa serían SSM, y un draft no
    tiene SSM — sus capas decoder son de atención. Dejar el mapa dejaría al
    head cortado sin su KV propio (peor que la aproximación sin metadata)."""
    claimed = int(base["n_layer"])
    layers = claimed

    declared = _declared_layers(max_layers)
    if declared:
        layers = min(layers, declared)

    if base_size_gb and weights_gb > 0:
        base_layers = int(base_meta.get("n_layer") or 0)
        base_head = base_meta.get("n_head")
        if base_layers >= 1 and base_head and (
            base.get("n_embd") == base_meta.get("n_embd")
            and base.get("n_head") == base_head
            and base.get("n_head_kv") == base_meta.get("n_head_kv")
            and (base.get("head_dim") or max(1, base["n_embd"] // base_head))
            == (base_meta.get("head_dim") or max(1, base_meta["n_embd"] // base_head))
        ):
            implied_gb = float(base_size_gb) * layers / base_layers
            if implied_gb > DRAFT_HEADER_GUARD_RATIO * weights_gb:
                layers = max(1, int(layers * weights_gb / implied_gb))

    if layers != claimed:
        base["n_layer"] = layers
        base.pop("full_attention_interval", None)
        base.pop("n_head_kv_layers", None)


def estimate_draft_aux_gb(
    base_meta: dict[str, Any],
    n_ctx: int,
    draft_weights_gb: float = 0.0,
    draft_meta: dict[str, Any] | None = None,
    base_size_gb: float = 0.0,
    max_layers: int | None = None,
) -> float | None:
    """VRAM que un draft especulativo suma al modelo principal (pesos + KV
    propio + cómputo) al ctx dado. Generaliza a todos los modos de
    SPEC_DRAFT_MODES (mtp/dflash/simple/eagle3, LL-4 U3) el presupuesto que
    antes vivía privado en el motor de fit (A2F-1), con el MISMO estimador del
    fit base:

    - .gguf de draft con metadata legible: usa sus dims reales y su tamaño
      real de archivo. Su KV usa los tipos de cache propios del draft, que
      LaunchConfig define q8_0 por defecto (--spec-draft-type-k/-v).
    - Sin metadata del draft (cabeza embebida, o .gguf sin header legible):
      aproximación conservadora = una capa de atención completa del modelo
      principal al ctx pedido (dims completas, sin ventanas SWA); los pesos
      van aparte solo si vienen en archivo.

    El overhead de cómputo no es una constante aparte: es el propio buffer FA
    que usa el estimador (COMPUTE_BUFFER_GB_FA). Medido en vivo con llama.cpp
    b11827 (RTX 3090, ctx 131072, mtp-gemma-4-26B-A4B): ~690 MiB de cómputo
    del draft junto a ~425 MiB de pesos y ~290 MiB de KV — el buffer ya cubre
    esa suma con holgura.

    Capas reales del draft (LL-4 U6): el header de muchos sidecars MTP es una
    COPIA del modelo base (declara sus N layers completos y sus dims) y
    presupuestar esas capas infla la card (medido: 6.42 GB estimados vs ~2.2
    reales en Qwen3.8-27B). `max_layers` son las capas DECLARADAS por una
    fuente confiable (entry.mtp_embedded_layers = nextn_predict_layers del
    base); `base_size_gb` es el tamaño real del archivo del base para el guard
    físico DRAFT_HEADER_GUARD_RATIO. Ambos se aplican vía _clamp_draft_layers;
    sin esos parámetros el resultado es idéntico al de U3. En el caso sin
    metadata, `max_layers` dimensiona la aproximación: la cabeza declarada tiene
    exactamente esas capas.

    Devuelve None si no se puede estimar (ni el draft ni el modelo principal
    traen metadata mínima): entonces el draft no entra en el fit ni al
    presupuesto — mejor subestimar declarándolo que inventar un número."""
    draft_meta = draft_meta or {}
    if draft_meta.get("n_layer") and draft_meta.get("n_head") and draft_meta.get("n_embd"):
        base = dict(draft_meta)  # copia: el clamp no debe reescribir la metadata del llamador
        _clamp_draft_layers(
            base, base_meta, float(base_size_gb or 0.0), max_layers,
            float(draft_weights_gb or 0.0),
        )
    else:
        base = {k: base_meta[k] for k in ("n_head", "n_embd") if base_meta.get(k)}
        # Sin metadata propia, la aproximación usa las capas DECLARADAS si el
        # llamador las conoce (cabeza MTP embebida); si no, una capa del base.
        base["n_layer"] = max(1, _declared_layers(max_layers) or 1)
        if base_meta.get("n_head_kv"):
            base["n_head_kv"] = base_meta["n_head_kv"]
    est = estimate_vram_usage(
        base, float(draft_weights_gb or 0.0), int(n_ctx),
        cache_type_k="q8_0", cache_type_v="q8_0",
        n_parallel=1, flash_attn=True, gpu_vram_gb=0.0,
    )
    if not est.get("available"):
        return None
    return est["total_gb"]


def estimate_vram_legend(
    meta: dict[str, Any],
    model_size_gb: float,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    n_parallel: int = 1,
    flash_attn: bool = False,
    gpu_vram_gb: float = 0,
    ctx_values: tuple[int, ...] = LEGEND_CTXS,
    aux_weights_gb: float = 0.0,
    aux_components: list[dict[str, Any]] | None = None,
    draft_aux: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Leyenda para la UI: para cada n_ctx común, el total de VRAM y su
    clasificación (comodo/justo/no_cabe). Mismo modelo y mismos parámetros que
    el estimador principal, variando solo el contexto. Devuelve [] si la
    metadata no alcanza (mismo criterio que estimate_vram_usage). Los auxiliares
    fijos (mmproj) entran iguales en cada fila para que el estado clasificado
    coincida con la estimación principal; el draft especulativo (draft_aux) NO
    es constante: se re-estima en cada fila con el ctx de esa fila (LL-4 U3),
    porque su KV propio crece con el contexto.
    """
    legend: list[dict[str, Any]] = []
    for n_ctx in ctx_values:
        r = estimate_vram_usage(
            meta, model_size_gb, n_ctx,
            cache_type_k=cache_type_k, cache_type_v=cache_type_v,
            n_parallel=n_parallel, flash_attn=flash_attn, gpu_vram_gb=gpu_vram_gb,
            aux_weights_gb=aux_weights_gb, aux_components=aux_components,
            draft_aux=draft_aux,
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

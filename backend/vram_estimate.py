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
        "n_swa_layers": int(n_swa),
        "swa_window": int(swa_window) if (n_swa and swa_window) else None,
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

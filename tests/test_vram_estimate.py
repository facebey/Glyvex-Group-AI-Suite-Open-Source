"""Tests del estimador de VRAM (backend/vram_estimate.py).

Función pura: se testea con números representativos de modelos reales
(densos y MoE), sin GPU, sin archivos y sin llama.cpp.
"""

from vram_estimate import (
    CACHE_TYPE_BYTES, COMPUTE_BUFFER_GB, COMPUTE_BUFFER_GB_FA,
    LEGEND_CTXS, estimate_vram_usage, estimate_vram_legend,
)


def _dense_meta() -> dict:
    # Denso GQA (tipo Llama/Qwen): 48 capas, 32 heads, 8 KV heads, head_dim 160.
    # KV por token (f16) = 2 × 48 × 8 × 160 × 2 = 245760 bytes = 240 KiB.
    return {"n_layer": 48, "n_head": 32, "n_embd": 5120, "n_head_kv": 8, "head_dim": 160}


def test_dense_f16_no_cabe_con_24gb():
    # 240 KiB/token × 65536 = 15 GiB de KV; 10 de pesos + 1.5 de cómputo = 26.5 total.
    r = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=65536,
                            cache_type_k="f16", cache_type_v="f16", gpu_vram_gb=24)
    assert r["available"] is True
    assert r["kv_gb"] == 15.0
    assert r["compute_gb"] == COMPUTE_BUFFER_GB
    assert r["total_gb"] == 26.5
    assert r["state"] == "no_cabe"
    assert r["pct"] == 110.4


def test_dense_q4_0_cache_comodo():
    # q4_0 (0.5625 B/elemento) contra f16 (2.0): KV de 15 → 4.22 GiB.
    r = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=65536,
                            cache_type_k="q4_0", cache_type_v="q4_0", gpu_vram_gb=24)
    assert r["state"] == "comodo"
    assert r["kv_gb"] == 4.22
    assert r["pct"] == 65.5


def test_estado_justo_en_fronteras():
    # Meta de 128 KiB/token en f16: 8192 tokens = 1 GiB exacto de KV.
    # Total exacto: 5 + 1 + 1.5 = 7.5 GiB.
    meta = {"n_layer": 32, "n_head": 32, "n_embd": 4096, "n_head_kv": 8, "head_dim": 128}
    r = estimate_vram_usage(meta, model_size_gb=5, n_ctx=8192,
                            cache_type_k="f16", cache_type_v="f16", gpu_vram_gb=7.5)
    # 100% → "justo" (no "no_cabe").
    assert r["state"] == "justo"
    assert r["pct"] == 100.0

    r80 = estimate_vram_usage(meta, model_size_gb=5, n_ctx=8192,
                              cache_type_k="f16", cache_type_v="f16", gpu_vram_gb=9.375)
    # Exactamente 80% → "justo" (comodo es estrictamente < 80).
    assert r80["state"] == "justo"
    assert r80["pct"] == 80.0


def test_sin_vram_declarada_dao_unknown():
    r = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192, gpu_vram_gb=0)
    assert r["available"] is True
    assert r["state"] == "unknown"
    assert r["pct"] is None
    assert r["gpu_vram_gb"] is None


def test_sin_campos_minimos_no_disponible():
    r = estimate_vram_usage({"n_layer": None, "n_head": 32, "n_embd": None},
                            model_size_gb=10, n_ctx=8192)
    assert r["available"] is False
    assert "n_layer" in r["error"]


def test_moe_se_marca_y_no_cambia_el_caulo():
    meta = {**_dense_meta(), "is_moe": True, "ffn_expert_count": 128}
    r = estimate_vram_usage(meta, model_size_gb=18, n_ctx=8192, gpu_vram_gb=24)
    assert r["is_moe"] is True
    assert r["weights_gb"] == 18.0


def test_n_parallel_multplica_el_kv():
    # 128 KiB/token en f16: 8192 tokens = 1 GiB exacto (redondeo limpio x2).
    meta = {"n_layer": 32, "n_head": 32, "n_embd": 4096, "n_head_kv": 8, "head_dim": 128}
    base = estimate_vram_usage(meta, model_size_gb=10, n_ctx=8192,
                               cache_type_k="f16", cache_type_v="f16")
    doble = estimate_vram_usage(meta, model_size_gb=10, n_ctx=8192,
                                cache_type_k="f16", cache_type_v="f16", n_parallel=2)
    assert doble["kv_gb"] == base["kv_gb"] * 2
    assert doble["n_parallel"] == 2


def test_fallback_mha_y_head_dim():
    # Sin n_head_kv ni head_dim: MHA y head_dim = n_embd // n_head.
    r = estimate_vram_usage({"n_layer": 12, "n_head": 12, "n_embd": 768},
                            model_size_gb=5, n_ctx=4096, cache_type_k="f16",
                            cache_type_v="f16", gpu_vram_gb=24)
    # KV/token = 2 × 12 × 12 × 64 × 2 = 36864 B; × 4096 = 0.140625 GiB.
    assert r["kv_gb"] == 0.14


def test_tipos_desconocidos_caen_a_f16():
    assert CACHE_TYPE_BYTES["f16"] == 2.0
    r = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192,
                            cache_type_k="i-lll", cache_type_v="i-lll")
    r16 = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192,
                              cache_type_k="f16", cache_type_v="f16")
    assert r["kv_gb"] == r16["kv_gb"]


def test_flash_attn_baja_solo_el_compute():
    base = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192,
                               cache_type_k="f16", cache_type_v="f16", gpu_vram_gb=24)
    fa = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192,
                             cache_type_k="f16", cache_type_v="f16", flash_attn=True,
                             gpu_vram_gb=24)
    assert base["compute_gb"] == COMPUTE_BUFFER_GB
    assert fa["compute_gb"] == COMPUTE_BUFFER_GB_FA
    assert fa["kv_gb"] == base["kv_gb"]  # FA no toca el KV cache
    assert base["flash_attn"] is False and fa["flash_attn"] is True
    delta = COMPUTE_BUFFER_GB - COMPUTE_BUFFER_GB_FA
    # 0.02: cada total se redondea por separado, el delta puede variar 1 cent.
    assert abs(fa["total_gb"] - (base["total_gb"] - delta)) <= 0.02


def test_legend_una_fila_por_ctx_y_crece():
    legend = estimate_vram_legend(_dense_meta(), model_size_gb=10,
                                  cache_type_k="f16", cache_type_v="f16", gpu_vram_gb=24)
    assert [r["n_ctx"] for r in legend] == list(LEGEND_CTXS)
    totals = [r["total_gb"] for r in legend]
    assert totals == sorted(totals)  # el consumo crece con el contexto
    # la fila de 8192 coincide con el estimador único a ese contexto
    single = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192,
                                 cache_type_k="f16", cache_type_v="f16", gpu_vram_gb=24)
    r8192 = next(r for r in legend if r["n_ctx"] == 8192)
    assert r8192["total_gb"] == single["total_gb"]
    assert r8192["state"] == single["state"]


def _qwen35_meta() -> dict:
    # Qwen3.8-27B (arch qwen35): 65 capas, 24 heads, 4 KV heads, head_dim 256,
    # full_attention_interval=4 → 16 capas de atención + 49 SSM, estado SSM real.
    return {
        "n_layer": 65, "n_head": 24, "n_embd": 5120, "n_head_kv": 4, "head_dim": 256,
        "full_attention_interval": 4,
        "ssm_inner_size": 6144, "ssm_state_size": 128, "ssm_conv_kernel": 4,
    }


def test_hibrido_ssm_kv_solo_capas_atencion():
    r = estimate_vram_usage(_qwen35_meta(), model_size_gb=16.34, n_ctx=131072,
                            cache_type_k="q8_0", cache_type_v="q8_0", flash_attn=True,
                            gpu_vram_gb=24)
    assert r["is_hybrid"] is True
    assert r["n_attn_layers"] == 16   # 65 // 4
    assert r["n_ssm_layers"] == 49    # 65 - 16
    # KV = 2×16×4×256×131072×1.0625 / GiB ≈ 4.25 (coincide con el log real, 4352 MiB).
    assert r["kv_gb"] == 4.25
    # SSM = 49×6144×(128+4)×4 / GiB ≈ 0.15 (log real: 149.62 MiB).
    assert r["ssm_gb"] == 0.15
    # total = 16.34 + 4.25 + 0.15 + 0.75 (FA) = 21.49 (antes ~32 con las 65 capas).
    assert r["total_gb"] == 21.49


def test_denso_sin_ssm_no_cambia():
    r = estimate_vram_usage(_dense_meta(), model_size_gb=10, n_ctx=8192,
                            cache_type_k="f16", cache_type_v="f16")
    assert r["is_hybrid"] is False
    assert r["n_attn_layers"] == 48
    assert r["n_ssm_layers"] == 0
    assert r["ssm_gb"] == 0.0


def test_hibrido_ssm_fijo_kv_crece():
    a = estimate_vram_usage(_qwen35_meta(), model_size_gb=16.34, n_ctx=8192,
                            cache_type_k="q8_0", cache_type_v="q8_0")
    b = estimate_vram_usage(_qwen35_meta(), model_size_gb=16.34, n_ctx=32768,
                            cache_type_k="q8_0", cache_type_v="q8_0")
    assert b["ssm_gb"] == a["ssm_gb"]          # el estado SSM no crece con el ctx
    assert abs(b["kv_gb"] - a["kv_gb"] * 4) <= 0.03  # el KV sí, ×4


def test_hibrido_sin_params_ssm_kv_cortado_ssm_cero():
    m = {k: v for k, v in _qwen35_meta().items()
         if k not in ("ssm_inner_size", "ssm_state_size", "ssm_conv_kernel")}
    r = estimate_vram_usage(m, model_size_gb=16.34, n_ctx=131072,
                            cache_type_k="q8_0", cache_type_v="q8_0")
    assert r["n_attn_layers"] == 16   # el corte de KV sigue aplicando
    assert r["ssm_gb"] == 0.0         # sin params SSM se trata como denso en ese término

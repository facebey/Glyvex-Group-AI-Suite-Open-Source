"""
test_hardware_recommend.py — A2 (M6): motor de recomendaciones + A2F-1
(escalera adaptativa y fit que incluye draft MTP / visión).

Los números esperados son aritmética pura determinista sobre las constantes
del propio módulo (CTX_LADDER, CTX_LADDER_EXTENDED, FIT_RATIO,
MTP_COMPUTE_OVERHEAD_GB) y las del estimador (q8_0 = 1.0625 B/elemento,
COMPUTE_BUFFER_GB_FA = 0.75). Ejemplo del modelo genérico (48 capas, 8 heads
KV, head_dim 128): KV q8_0 = 104 448 B/token → 32k ≈ 3.19 GiB, 16k ≈ 1.59,
8k ≈ 0.80, 4k ≈ 0.40; f16 duplica el KV (6.0 GiB a 32k).

Invariantes duras que cubre (diseño §4 aprobado + extensiones A2F-1):
- cpu_only => n_gpu_layers = 0 SIEMPRE.
- n_ctx solo desde la escalera CTX_LADDER (+ CTX_LADDER_EXTENDED cuando la
  ficha declara context_length; nunca por encima de lo declarado ni de 131072).
- El draft pasa LaunchConfig ANTES de devolverse (o se descarta con
  draft_invalid).
- Solo se emiten parámetros que LaunchConfig expone.
- GPUs sin VRAM conocida quedan fuera del motor (no_vram_source).
- MTP y mmproj se emiten solo si caben en el presupuesto del ctx elegido; si
  no caben se reportan como warning (mtp_no_room / vision_no_room), nunca un
  draft que revienta la VRAM.
"""

from __future__ import annotations

import pytest

import hardware_recommend as rec
from hardware_detection import CpuInfo, GpuInfo, HardwareSnapshot, RamInfo
from launcher import LaunchConfig

# Modelo genérico del motor (GENERIC_META): los números de KV del docstring.
GENERIC_MODEL = {
    "size_gb": 20.0,
    "metadata": {"n_layer": 48, "n_head": 32, "n_embd": 4096, "n_head_kv": 8},
}

# Campos que el launcher puede emitir: ningún otro puede aparecer en un draft.
EMITABLE_KEYS = {
    "gpu_mode", "n_gpu_layers", "n_ctx", "n_threads", "n_cpu_moe",
    "cache_type_k", "cache_type_v", "mtp_embedded",
    # A2F-1: componentes auxiliares con archivo real (launch_config los expone
    # igual que la UI de GroupCard: --mmproj y --spec-draft-model).
    "mmproj_path", "mtp_draft_model",
}


def _snapshot(*, gpus=(), cpu_physical=None, ram_available_mb=None) -> HardwareSnapshot:
    cpu = None
    if cpu_physical is not None:
        cpu = CpuInfo(vendor="amd", logical_cores=cpu_physical * 2,
                      physical_cores=cpu_physical)
    ram = None
    if ram_available_mb is not None:
        ram = RamInfo(total_mb=ram_available_mb * 2, available_mb=ram_available_mb)
    return HardwareSnapshot(
        detected_at="2026-10-29T00:00:00+00:00",
        cpu=cpu,
        ram=ram,
        gpus=list(gpus),
        gpu_family=gpus[0].vendor if gpus else "cpu",
    )


def _nvidia(vram_mb: int) -> GpuInfo:
    return GpuInfo(index=0, name="NVIDIA GeForce", vendor="nvidia",
                   vram_total_mb=float(vram_mb))


def _amd(vram_mb: int) -> GpuInfo:
    return GpuInfo(index=0, name="AMD Radeon RX", vendor="amd",
                   vram_total_mb=float(vram_mb))


def _intel_no_vram() -> GpuInfo:
    return GpuInfo(index=0, name="Intel(R) UHD Graphics", vendor="intel")


def _assert_valid_draft(params: dict, *, ctx_train: int = 32768) -> LaunchConfig:
    """El draft debe construir un LaunchConfig real y respetar las invariantes.
    ctx_train = el contexto declarado por la ficha usada (32768 = el techo de
    la escalera base, suficiente para los drafts sin ficha)."""
    cfg = LaunchConfig(model_id="__draft__", **params)
    assert set(params) <= EMITABLE_KEYS
    if cfg.gpu_mode == "cpu_only":
        assert cfg.n_gpu_layers == 0
    if "n_ctx" in params:
        assert params["n_ctx"] in set(rec.CTX_LADDER) | set(rec.CTX_LADDER_EXTENDED)
        if params["n_ctx"] not in rec.CTX_LADDER:
            # Paso extendido (A2F-1): solo legal con contexto declarado.
            assert ctx_train >= params["n_ctx"]
    if cfg.gpu_mode == "gpu_only":
        assert cfg.n_gpu_layers == -1
    if cfg.gpu_mode == "hybrid":
        assert cfg.n_gpu_layers != 0
    return cfg


# --------------------------------------------------------------------------
# Selección de GPU
# --------------------------------------------------------------------------


def test_best_gpu_picks_max_known_vram():
    snap = _snapshot(gpus=[_intel_no_vram(), _amd(8192), _nvidia(24576)])
    assert rec.best_gpu(snap).vendor == "nvidia"
    snap2 = _snapshot(gpus=[_amd(16384), _nvidia(8192)])
    assert rec.best_gpu(snap2).vram_total_mb == 16384


def test_best_gpu_ignores_gpus_without_vram():
    snap = _snapshot(gpus=[_intel_no_vram()])
    assert rec.best_gpu(snap) is None


# --------------------------------------------------------------------------
# GPU grande: encaje completo (gpu_only)
# --------------------------------------------------------------------------


def test_high_vram_generic_draft_full_fit():
    # RTX 4090 24 GB + modelo genérico (12 GB): 32k cabe bajo FIT_RATIO
    # (12 + 3.19 + 0.75 = 15.94 <= 22.8). El fit re-estimado en f16
    # (12 + 6.0 + 0.75 = 18.75 <= 22.8) también cabe -> KV f16.
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap)
    assert out["params"]["gpu_mode"] == "gpu_only"
    assert out["params"]["n_gpu_layers"] == -1
    assert out["params"]["n_ctx"] == 32768
    assert out["params"]["cache_type_k"] == "f16"
    _assert_valid_draft(out["params"])
    keys = [r["key"] for r in out["rationale"]]
    assert keys == ["max_fit", "headroom_f16"]
    assert any(w["key"] == "generic_draft" for w in out["warnings"])
    assert out["inputs"]["vram_gb"] == 24.0
    assert out["inputs"]["model_specific"] is False


def test_fit_includes_kv_not_just_weights():
    # 16 GB, pesos 12: SOLO los pesos caben (12 <= 15.2), pero pesos+KV a 32k
    # (15.94) no -> el escalón elegido es 16k (14.34 <= 15.2). El "≥55% del
    # peso" del diseño hubiera dicho 32k: el fit estricto (con KV) es lo
    # aprobado.
    snap = _snapshot(gpus=[_amd(16384)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, dict(GENERIC_MODEL, size_gb=12.0))
    assert out["params"]["gpu_mode"] == "gpu_only"
    assert out["params"]["n_ctx"] == 16384
    # El fit re-estimado en f16 (12 + 3.0 + 0.75 = 15.75 > 15.2) no cabe ->
    # NO se emite f16 (q8_0 default).
    assert "cache_type_k" not in out["params"]
    _assert_valid_draft(out["params"])


def test_low_vram_ctx_cap():
    # 10 GB con modelo de 4 GB: 32k cabría numéricamente (7.94 <= 9.5), pero
    # con VRAM < LOW_VRAM_CAP_CTX la escalera se corta en 16k (cuelchón de
    # drivers/desktop, diseño §4.2).
    model = dict(GENERIC_MODEL, size_gb=4.0)
    snap = _snapshot(gpus=[_amd(10240)], cpu_physical=6, ram_available_mb=16384)
    out = rec.recommend(snap, model)
    assert out["params"]["gpu_mode"] == "gpu_only"
    assert out["params"]["n_ctx"] == 16384
    _assert_valid_draft(out["params"])


def test_mtp_embedded_fits_budget():
    # Modelo con cabeza MTP embebida: se sugiere porque su coste (1 capa de KV
    # del modelo al ctx elegido + MTP_COMPUTE_OVERHEAD ≈ 0.82 GB) entra en el
    # presupuesto del 32k (11.94 + 0.82 = 12.76 <= 22.8). Con MTP emitido, el
    # f16 se re-estima sobre el bundle (14.75 + 0.82 = 15.57 <= 22.8): también
    # cabe.
    model = dict(GENERIC_MODEL, size_gb=8.0, mtp_embedded=True)
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["mtp_embedded"] is True
    keys = [r["key"] for r in out["rationale"]]
    assert keys == ["max_fit", "mtp_fit", "headroom_f16"]
    _assert_valid_draft(out["params"])


def test_mtp_no_room_when_tight():
    # El bundle del ctx elegido + el coste del draft pasa FIT_RATIO: MTP se
    # descarta y se avisa (mtp_no_room), NO se baja de ctx para hacerlo caber.
    # 13 GB en 16 GB: 16k no cabe (15.34 > 15.2), 8k sí (14.55). El MTP
    # embebido (~0.77 GB al 8k) llevaría el total a 15.32 > 15.2 -> descarte.
    model = dict(GENERIC_MODEL, size_gb=13.0, mtp_embedded=True)
    snap = _snapshot(gpus=[_amd(16384)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["gpu_mode"] == "gpu_only"
    assert out["params"]["n_ctx"] == 8192
    assert "mtp_embedded" not in out["params"]
    warns = [w["key"] for w in out["warnings"]]
    assert "mtp_no_room" in warns
    _assert_valid_draft(out["params"])


# --------------------------------------------------------------------------
# GPU chica: offload parcial por capas (hybrid)
# --------------------------------------------------------------------------


def test_mid_vram_hybrid_partial_layers():
    # RX 7600 8 GB + genérico 12 GB: nada encaja completo. A 16k el KV+compute
    # (2.34) <= 50% de la VRAM: presupuesto de pesos = 7.6-2.34 = 5.26 GB ->
    # floor(48 * 5.26 / 12) = 21 capas en GPU.
    snap = _snapshot(gpus=[_amd(8192)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap)
    assert out["params"]["gpu_mode"] == "hybrid"
    assert out["params"]["n_ctx"] == 16384
    assert out["params"]["n_gpu_layers"] == 21
    _assert_valid_draft(out["params"])
    assert [r["key"] for r in out["rationale"]] == ["partial_fit"]


def test_hybrid_layer_count_bounded():
    # Invariante: el offload parcial siempre queda entre 1 y n_layer-1 capas.
    snap = _snapshot(gpus=[_amd(6144)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, dict(GENERIC_MODEL, size_gb=9.0))
    assert out["params"]["gpu_mode"] == "hybrid"
    assert 1 <= out["params"]["n_gpu_layers"] < 48
    _assert_valid_draft(out["params"])


def test_moe_hybrid_uses_n_cpu_moe_not_partial_layers():
    # MoE: la atención queda en GPU (n_gpu_layers=-1) y lo que se traslada a
    # RAM son capas de expertos via n_cpu_moe (no un corte parcial de capas).
    # 12 GB (escalera cortada en 16k por el cap): KV+compute = 2.34 GB
    # (valores redondeados por el propio estimador, que es lo que compara el
    # motor) -> presupuesto de pesos = 11.4 - 2.34 = 9.06 ->
    # fit_layers = floor(48 * 9.06 / 20) = 21 -> n_cpu_moe = 48 - 21 = 27.
    model = {
        "size_gb": 20.0,
        "metadata": {**GENERIC_MODEL["metadata"], "is_moe": True},
    }
    snap = _snapshot(gpus=[_nvidia(12288)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["gpu_mode"] == "hybrid"
    assert out["params"]["n_gpu_layers"] == -1
    assert out["params"]["n_ctx"] == 16384  # cap de la clase 8-12 GB (límite incluido)
    assert out["params"]["n_cpu_moe"] == 27
    assert 1 <= out["params"]["n_cpu_moe"] <= 48
    _assert_valid_draft(out["params"])
    assert [r["key"] for r in out["rationale"]] == ["moe_experts_cpu"]


def test_model_not_fit_gpu_falls_back_to_cpu_with_ram_budget():
    # GPU presente pero el modelo no tiene ninguna configuración útil: cpu_only
    # con la RAM real de la máquina (model_not_fit_gpu + ram_tight).
    model = dict(GENERIC_MODEL, size_gb=400.0)
    snap = _snapshot(gpus=[_amd(8192)], cpu_physical=16, ram_available_mb=65536)
    out = rec.recommend(snap, model)
    assert out["params"]["gpu_mode"] == "cpu_only"
    assert out["params"]["n_gpu_layers"] == 0
    assert out["params"]["n_ctx"] == 4096  # ni el ctx mínimo cabe en RAM
    assert out["params"]["n_threads"] == 16
    keys = [w["key"] for w in out["warnings"]]
    assert "model_not_fit_gpu" in keys and "ram_tight" in keys
    _assert_valid_draft(out["params"])


# --------------------------------------------------------------------------
# Sin GPU utilizable: cpu_only
# --------------------------------------------------------------------------


def test_no_gpu_cpu_only_with_threads_and_ram_fit():
    # Sin GPU: cpu_only, n_threads = núcleos FÍSICOS (nunca lógicos, diseño
    # §4.4), y el mayor ctx cuyo total (12+3.19+0.75 = 15.94) cabe en 80% de
    # la RAM disponible (20 GB -> 16.0).
    snap = _snapshot(cpu_physical=8, ram_available_mb=20480)
    out = rec.recommend(snap)
    assert out["params"]["gpu_mode"] == "cpu_only"
    assert out["params"]["n_gpu_layers"] == 0
    assert out["params"]["n_threads"] == 8
    assert out["params"]["n_ctx"] == 32768
    _assert_valid_draft(out["params"])
    assert [r["key"] for r in out["rationale"]] == ["physical_cores", "max_fit_ram"]


def test_no_gpu_tight_ram_warns():
    # 8 GB disponibles con pesos de 12 GB: nada cabe -> ctx mínimo +
    # ram_tight + quant_suggested (12 > 0.75*8).
    snap = _snapshot(cpu_physical=4, ram_available_mb=8192)
    out = rec.recommend(snap)
    assert out["params"]["gpu_mode"] == "cpu_only"
    assert out["params"]["n_ctx"] == 4096
    keys = [w["key"] for w in out["warnings"]]
    assert "ram_tight" in keys and "quant_suggested" in keys
    _assert_valid_draft(out["params"])


def test_intel_gpu_without_vram_is_excluded_from_engine():
    # Intel iGPU sin VRAM legible (WDDM sin NVML): fuera del motor por diseño;
    # el draft es cpu_only y avisa por qué.
    snap = _snapshot(gpus=[_intel_no_vram()], cpu_physical=8, ram_available_mb=20480)
    out = rec.recommend(snap)
    assert out["params"]["gpu_mode"] == "cpu_only"
    assert any(w["key"] == "no_vram_source" for w in out["warnings"])
    _assert_valid_draft(out["params"])


def test_missing_ram_still_emits_cpu_only_core():
    # Sin datos de RAM (sonda degradada): sigue el draft cpu_only con
    # n_gpu_layers=0, sin inventar ctx.
    snap = _snapshot(cpu_physical=8)
    out = rec.recommend(snap)
    assert out["params"] == {"gpu_mode": "cpu_only", "n_gpu_layers": 0,
                             "n_threads": 8}
    _assert_valid_draft(out["params"])
    assert out["inputs"]["ram_available_gb"] is None


# --------------------------------------------------------------------------
# Fichas de modelo y garantías del draft
# --------------------------------------------------------------------------


def test_model_without_metadata_keeps_real_weights():
    # Ficha GGUF sin metadata utilizable: se cae a la clase genérica para la
    # arquitectura pero CONSERVA el peso real (es el dato que sí hay).
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, {"size_gb": 6.0, "metadata": {}})
    assert out["inputs"]["weights_gb"] == 6.0
    assert out["inputs"]["model_specific"] is True
    assert any(w["key"] == "generic_architecture" for w in out["warnings"])
    assert out["params"]["n_ctx"] == 32768
    _assert_valid_draft(out["params"])


def test_draft_invalid_is_discarded(monkeypatch):
    # Garantía dura: si un draft no valida contra LaunchConfig, se descarta
    # ENTERO (params vacío) y se reporta draft_invalid — nunca un draft roto.
    class _BoomConfig:
        def __init__(self, **kwargs):
            raise ValueError("validación cruzada falló (simulada)")

    monkeypatch.setattr(rec, "LaunchConfig", _BoomConfig)
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap)
    assert out["params"] == {}
    assert out["rationale"] == []
    assert [w["key"] for w in out["warnings"]] == ["draft_invalid"]


def test_generic_draft_is_flagged():
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap)
    assert any(w["key"] == "generic_draft" for w in out["warnings"])
    assert out["inputs"]["model_specific"] is False
    assert out["inputs"]["weights_gb"] == rec.GENERIC_WEIGHTS_GB


@pytest.mark.parametrize("vram_mb", [24576, 16384, 12288, 8192, 6144])
def test_invariants_hold_across_vram_tiers(vram_mb):
    # Tabla de VRAMs típicas: el draft SIEMPRE valida, cpu_only implica
    # n_gpu_layers=0 y n_ctx solo sale de la escalera.
    snap = _snapshot(gpus=[_amd(vram_mb)], cpu_physical=8, ram_available_mb=32768)
    for model in (None, GENERIC_MODEL):
        out = rec.recommend(snap, model)
        _assert_valid_draft(out["params"])
        assert out["params"]["gpu_mode"] in {"gpu_only", "hybrid", "cpu_only"}


# --------------------------------------------------------------------------
# A2F-1: escalera adaptativa (context_length de la ficha)
# --------------------------------------------------------------------------


# Ficha pequeña con contexto declarado: aislamos el efecto de la escalera
# extendida de los problemas de capacidad (2 GB de pesos en 24 GB caben a 131K:
# KV q8_0 genérico 104 448 B/token * 131072 ≈ 12.75 GiB; total 15.5 ≤ 22.8).
CTX_MODEL = {
    "size_gb": 2.0,
    "metadata": {
        "n_layer": 48, "n_head": 32, "n_embd": 4096, "n_head_kv": 8,
        "context_length": 131072,
    },
}


def test_extended_ladder_when_context_declared():
    # Con context_length declarado y capacidad sobra, el motor sube a 131K
    # (la escalera vieja se habría quedado en 32k aunque cupiera holgadamente).
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, CTX_MODEL)
    assert out["params"]["n_ctx"] == 131072
    assert [r["key"] for r in out["rationale"]] == ["max_fit"]
    # f16 a 131K (12.75*2 + 2 + 0.75 = 27.0 > 22.8) no cabe -> no se emite.
    assert "cache_type_k" not in out["params"]
    _assert_valid_draft(out["params"], ctx_train=131072)


def test_extended_ladder_respects_declared_cap():
    # Sin context_length NO se inventa entrenamiento: aunque el modelo sea el
    # mismo y cupieran pasos mayores, el techo sigue siendo 32k.
    meta = {k: v for k, v in CTX_MODEL["metadata"].items() if k != "context_length"}
    model = dict(CTX_MODEL, metadata=meta)
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 32768
    _assert_valid_draft(out["params"])


def test_ctx_train_below_extended_step_caps_ladder():
    # Ficha que declara un entrenamiento chico (8192): la escalera queda cortada
    # ahí aunque la VRAM permitiera 131K. 2 GB + KV 8k (0.8) + 0.75 = 3.55 ≤ 22.8.
    model = {
        "size_gb": 2.0,
        "metadata": dict(GENERIC_MODEL["metadata"], context_length=8192),
    }
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 8192
    _assert_valid_draft(out["params"], ctx_train=8192)


def test_low_vram_cap_applies_to_extended_steps():
    # Placa de 10 GB con ficha que declara 131K: el recorte de 8–12 GB manda
    # igual que antes (el colchón de drivers/desktop no depende del modelo).
    # Con 6 GB de pesos, 32k ni siquiera cabría (6 + 3.19 + 0.75 = 9.94 > 9.5)
    # y 16k sí (8.34 ≤ 9.5) — pero lo que se prueba es que NUNCA aparece un
    # paso extendido (65k/131k) en una placa de 10 GB.
    model = dict(CTX_MODEL, size_gb=6.0)
    snap = _snapshot(gpus=[_amd(10240)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 16384
    _assert_valid_draft(out["params"])


def test_extended_step_not_emitted_when_it_does_not_fit():
    # Contexto declarado 131K pero sin capacidad: 12 GB de pesos, KV 131K
    # (12.75) desborda; el motor baja peldaños hasta 16k (14.34 ≤ 15.2), que
    # es la escalera BASE — la ficha no fuerza un paso que no cabe.
    model = {
        "size_gb": 12.0,
        "metadata": dict(GENERIC_MODEL["metadata"], context_length=131072),
    }
    snap = _snapshot(gpus=[_amd(16384)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 16384
    _assert_valid_draft(out["params"], ctx_train=131072)


def test_uniform_swa_model_gets_full_ladder_and_f16():
    # Caso que motivó el rediseño: modelo con TODAS las capas en ventana SWA de
    # 1024 (48L, 8 KV heads, head_dim 128) — solo el escalar sliding_window,
    # como mistral/gemma2; sin array de patrón todas las capas deslizan. KV
    # real a 131K = 48*1024*2*8*128 *1.0625 ≈ 0.10 GiB, no 12.75. Con contexto
    # declarado, 131K + KV f16 (0.19 GiB) caben holgadamente; el umbral fijo
    # de pct clásico los negaba.
    swa_meta = dict(CTX_MODEL["metadata"], sliding_window=1024)
    model = {"size_gb": 15.84, "metadata": swa_meta}
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 131072
    assert out["params"]["cache_type_k"] == "f16"
    assert [r["key"] for r in out["rationale"]] == ["max_fit", "headroom_f16"]
    _assert_valid_draft(out["params"], ctx_train=131072)


# --------------------------------------------------------------------------
# A2F-1: fit de componentes auxiliares (draft MTP y visión)
# --------------------------------------------------------------------------


def test_mtp_sidecar_emitted_with_real_cost():
    # Draft sidecar (mtp-*.gguf): se emite su RUTA real y su coste se calcula
    # con la metadata del propio draft (1 capa, 4096/8/128 -> KV 32k ≈ 0.07 GB
    # + pesos 0.43 + cómputo 0.75 ≈ 1.25). Base 32k (15.94) + 1.25 ≤ 22.8.
    # Ficha SIN contexto declarado (escalera base) para aislar el caso aux.
    model = dict(GENERIC_MODEL, size_gb=12.0, mtp_draft_model="M:/mtp-draft.gguf",
                 mtp_draft_size_gb=0.43,
                 mtp_draft_metadata={"n_layer": 1, "n_head": 32, "n_embd": 4096,
                                     "n_head_kv": 8, "context_length": 32768})
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 32768
    assert out["params"]["mtp_draft_model"] == "M:/mtp-draft.gguf"
    assert "mtp_embedded" not in out["params"]
    keys = [r["key"] for r in out["rationale"]]
    assert keys == ["max_fit", "mtp_fit", "headroom_f16"]
    mtp_row = next(r for r in out["rationale"] if r["key"] == "mtp_fit")
    assert mtp_row["param"] == "mtp_draft_model"
    assert mtp_row["args"]["aux_gb"] == pytest.approx(1.25, abs=0.02)
    _assert_valid_draft(out["params"], ctx_train=131072)


def test_vision_mmproj_emitted_when_measured():
    # mmproj con tamaño medido (1.111 GB reales de mmproj-F16): entra en el
    # presupuesto del ctx elegido -> se emite mmproj_path con rationale vision_fit.
    model = dict(CTX_MODEL, mmproj_path="M:/vision-F16.gguf", mmproj_size_gb=1.111)
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["mmproj_path"] == "M:/vision-F16.gguf"
    keys = [r["key"] for r in out["rationale"]]
    assert "vision_fit" in keys
    _assert_valid_draft(out["params"], ctx_train=131072)


def test_vision_no_room_when_tight():
    # 13 GB de pesos en 16 GB: el mayor ctx que cabe completo es 8k
    # (13 + 0.80 + 0.75 = 14.55 ≤ 15.2). mmproj 1.111 encima lo llevaría a
    # 15.66 > 15.2 -> se descarta con warning vision_no_room; el contexto NO
    # baja a 4k para darle hueco a la visión (el contexto manda).
    model = dict(GENERIC_MODEL, size_gb=13.0, mmproj_path="M:/vision-F16.gguf",
                 mmproj_size_gb=1.111)
    snap = _snapshot(gpus=[_amd(16384)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert out["params"]["n_ctx"] == 8192
    assert "mmproj_path" not in out["params"]
    warns = [w["key"] for w in out["warnings"]]
    assert "vision_no_room" in warns
    _assert_valid_draft(out["params"])


def test_aux_unmeasured_is_not_budgeted():
    # mmproj declarado sin tamaño legible (stat falló) o MTP sin archivo ni
    # embebido: no hay fit honesto que hacer -> no se emite ni se avisa.
    model = dict(CTX_MODEL, mmproj_path="M:/vision.gguf", mmproj_size_gb=0.0)
    snap = _snapshot(gpus=[_nvidia(24576)], cpu_physical=8, ram_available_mb=32768)
    out = rec.recommend(snap, model)
    assert "mmproj_path" not in out["params"]
    assert "mtp_embedded" not in out["params"]
    warns = [w["key"] for w in out["warnings"]]
    assert "vision_no_room" not in warns and "mtp_no_room" not in warns
    _assert_valid_draft(out["params"], ctx_train=131072)

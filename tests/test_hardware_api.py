"""
test_hardware_api.py — A2 (M6): wiring de GET /api/hardware y
POST /api/hardware/recommend.

La detección real se reemplaza por una foto fija (monkeypatch de
detect_hardware): estos tests cubren el wiring de la API (status, forma del
JSON, fuerza del refresh, resolución de model_id), no las sondas ni el motor
(viven en test_hardware.py y test_hardware_recommend.py). El cache TTL del
módulo se limpia en el fixture autouse para que ninguna llamada herede la
foto de otra.
"""

from __future__ import annotations

import pytest

import hardware_detection as hw
import hardware_recommend as rec
import models as models_module
from helpers import write_gguf


@pytest.fixture(autouse=True)
def _clean_hw_cache():
    hw._cache = None
    yield
    hw._cache = None


SNAPSHOT = hw.HardwareSnapshot(
    detected_at="2026-10-29T00:00:00+00:00",
    cpu=hw.CpuInfo(vendor="amd", model="Ryzen", logical_cores=16,
                   physical_cores=8, max_mhz=4200.0, source="env"),
    ram=hw.RamInfo(total_mb=32768.0, available_mb=24576.0, swap_total_mb=None),
    gpus=[hw.GpuInfo(index=0, name="AMD Radeon RX", vendor="amd",
                     vram_total_mb=12288.0, vram_used_mb=1024.0)],
    gpu_family="amd",
)


@pytest.fixture(autouse=True)
def _fixed_snapshot(monkeypatch):
    """detect_hardware -> foto fija. Registra la cantidad de llamadas y el
    flag force para verificar el wiring de ?refresh=1."""
    calls = {"count": 0, "force": None}

    def fake_detect(force: bool = False):
        calls["count"] += 1
        calls["force"] = force
        return SNAPSHOT

    monkeypatch.setattr(hw, "detect_hardware", fake_detect)
    monkeypatch.setattr("hardware_api.hardware_detection.detect_hardware",
                       fake_detect)
    return calls


def _register(entry_kwargs: dict) -> models_module.ModelEntry:
    entry = models_module.ModelEntry(
        id=entry_kwargs.pop("id"),
        name="Test Model",
        filename=entry_kwargs.pop("filename", "test.gguf"),
        path=entry_kwargs.pop("path", "C:/fake/test.gguf"),
        format="gguf",
        size_gb=entry_kwargs.pop("size_gb", 20.0),
        last_seen="2026-10-29T00:00:00+00:00",
        **entry_kwargs,
    )
    models_module.inventory._by_id[entry.id] = entry
    return entry


# --------------------------------------------------------------------------
# GET /api/hardware
# --------------------------------------------------------------------------


async def test_get_hardware_returns_snapshot(client, _fixed_snapshot):
    res = await client.get("/api/hardware")
    assert res.status_code == 200
    data = res.json()
    assert data["cpu"]["vendor"] == "amd"
    assert data["cpu"]["physical_cores"] == 8
    assert data["ram"]["available_mb"] == 24576.0
    assert data["gpu_family"] == "amd"
    assert data["gpus"][0]["vram_total_mb"] == 12288.0
    assert data["degraded"] == []
    assert data["detected_at"] == SNAPSHOT.detected_at
    assert _fixed_snapshot["force"] is False


async def test_get_hardware_refresh_forces_probe(client, _fixed_snapshot):
    # Sin refresh: la segunda llamada sale del cache del endpoint... pero el
    # fixture reemplaza detect_hardware, así que el indicador duro es que
    # ?refresh=1 pasa force=True al módulo.
    res = await client.get("/api/hardware", params={"refresh": "true"})
    assert res.status_code == 200
    assert _fixed_snapshot["force"] is True
    assert _fixed_snapshot["count"] == 1


async def test_get_hardware_exposes_degraded_reasons(client, monkeypatch):
    # Sondas degradadas: la respuesta sigue siendo 200 con campos None y los
    # motivos listados (la UI los muestra como avisos, no como error).
    degraded = SNAPSHOT.model_copy(update={
        "cpu": None, "ram": None, "gpus": [], "gpu_family": "cpu",
        "degraded": ["cpu.vendor", "ram", "gpu.nvml"],
    })
    monkeypatch.setattr(hw, "detect_hardware", lambda force=False: degraded)
    res = await client.get("/api/hardware")
    assert res.status_code == 200
    data = res.json()
    assert data["cpu"] is None
    assert data["degraded"] == ["cpu.vendor", "ram", "gpu.nvml"]


# --------------------------------------------------------------------------
# POST /api/hardware/recommend
# --------------------------------------------------------------------------


async def test_recommend_without_body_is_generic_draft(client):
    res = await client.post("/api/hardware/recommend")
    assert res.status_code == 200
    data = res.json()
    assert data["inputs"]["model_specific"] is False
    assert data["inputs"]["weights_gb"] == rec.GENERIC_WEIGHTS_GB
    assert data["inputs"]["vram_gb"] == 12.0
    assert any(w["key"] == "generic_draft" for w in data["warnings"])
    # Foto de 12 GB (LOW_VRAM_CAP_CTX): la escalera se corta en 16k.
    assert data["params"]["n_ctx"] == 16384


async def test_recommend_empty_body_object(client):
    res = await client.post("/api/hardware/recommend", json={})
    assert res.status_code == 200
    assert res.json()["inputs"]["model_specific"] is False


async def test_recommend_unknown_model_404(client):
    res = await client.post("/api/hardware/recommend",
                            json={"model_id": "no-existe"})
    assert res.status_code == 404
    assert "no-existe" in res.json()["detail"]


async def test_recommend_uses_registered_metadata(client):
    # Ficha registrada con metadata MoE: el draft es model_specific y usa
    # n_cpu_moe (el mismo resultado que test_hardware_recommend con 12 GB).
    _register({
        "id": "moe-test",
        "metadata": {**rec.GENERIC_META, "is_moe": True},
        "size_gb": 20.0,
    })
    res = await client.post("/api/hardware/recommend",
                            json={"model_id": "moe-test"})
    assert res.status_code == 200
    data = res.json()
    assert data["inputs"]["model_specific"] is True
    assert data["inputs"]["weights_gb"] == 20.0
    assert data["params"]["gpu_mode"] == "hybrid"
    assert data["params"]["n_gpu_layers"] == -1
    assert data["params"]["n_cpu_moe"] == 27


async def test_recommend_falls_back_to_header_read_when_metadata_missing(
    client, tmp_path, monkeypatch
):
    # Entrada de esquema viejo (metadata None): se lee el header GGUF del
    # archivo con la cache de models.py (mismo material que /vram-estimate).
    path = write_gguf(tmp_path / "old.gguf", "llama", None, kv={
        "block_count": 32,
        "attention.head_count": 16,
        "attention.head_count_kv": 4,
        "embedding_length": 2048,
    })
    _register({"id": "old-schema", "path": str(path), "size_gb": 6.0,
               "metadata": None})

    calls = {"n": 0}
    real_read = models_module.read_gguf_metadata_cached

    async def counting_read(p):
        calls["n"] += 1
        return await real_read(p)

    monkeypatch.setattr(models_module, "read_gguf_metadata_cached",
                       counting_read)
    res = await client.post("/api/hardware/recommend",
                            json={"model_id": "old-schema"})
    assert res.status_code == 200
    data = res.json()
    assert calls["n"] == 1
    # Metadata real del header: model_specific True con la arquitectura del
    # archivo (n_layer 32), no la clase genérica.
    assert data["inputs"]["model_specific"] is True
    assert data["inputs"]["weights_gb"] == 6.0


async def test_recommend_unreadable_header_degrades_to_generic(
    client, tmp_path, monkeypatch
):
    # Archivo sin header legible: la lectura falla, el motor cae a la clase
    # genérica CONSERVANDO el peso real de la ficha (nunca un 500).
    ghost = tmp_path / "broken.gguf"
    ghost.touch()
    _register({"id": "broken", "path": str(ghost), "size_gb": 7.0,
               "metadata": None})
    res = await client.post("/api/hardware/recommend",
                            json={"model_id": "broken"})
    assert res.status_code == 200
    data = res.json()
    assert data["inputs"]["model_specific"] is True
    assert data["inputs"]["weights_gb"] == 7.0
    assert any(w["key"] == "generic_architecture" for w in data["warnings"])


# --------------------------------------------------------------------------
# A2F-1: la ficha que recibe el motor incluye los auxiliares reales
# --------------------------------------------------------------------------


async def test_recommend_resolves_aux_components_from_inventory(client, tmp_path):
    # Wiring (no el motor, que se cubre en test_hardware_recommend): el draft
    # model-specific debe llevar la RUTA del mmproj con su tamaño de archivo y
    # la del sidecar MTP resuelto por la regla compartida mtp_companion_file.
    main = tmp_path / "test.gguf"
    main.write_bytes(b"x" * 2048)
    mmproj = tmp_path / "test-mmproj-F16.gguf"
    mmproj.write_bytes(b"y" * 4096)
    # Sidecar SIN dims en el header: el motor usa la aproximación de 1 capa
    # (ramas cubiertas en test_hardware_recommend; acá importa la RUTA).
    mtp = write_gguf(tmp_path / "test-mtp.gguf")
    _register({"id": "with-aux", "path": str(main), "size_gb": 1.0,
               "metadata": dict(rec.GENERIC_META),
               "has_mmproj": True, "mmproj_path": str(mmproj)})
    _register({"id": "aux-draft", "filename": "test-mtp.gguf", "path": str(mtp),
               "size_gb": 0.01, "metadata": None})

    res = await client.post("/api/hardware/recommend",
                            json={"model_id": "with-aux"})
    assert res.status_code == 200
    data = res.json()
    # Foto fija de 12 GB: 1 GB de pesos + KV 16k (1.59) + 0.75 = 3.34 cabe
    # completo; el draft (~0.78) y el mmproj (bytes reales, >0) también.
    assert data["params"]["n_ctx"] == 16384
    assert data["params"]["mtp_draft_model"] == str(mtp)
    assert data["params"]["mmproj_path"] == str(mmproj)
    keys = [r["key"] for r in data["rationale"]]
    assert keys == ["max_fit", "mtp_fit", "vision_fit"]


async def test_recommend_without_aux_files_emits_no_aux_params(client, tmp_path):
    # Sin mmproj registrado ni sidecar en la carpeta: el draft no inventa
    # auxiliares (no hay mtp_draft_model/mmproj_path ni warnings de descarte).
    main = tmp_path / "test.gguf"
    main.write_bytes(b"x" * 2048)
    other = tmp_path / "otra-carpeta"
    other.mkdir()
    lonely = other / "unrelated-mtp-neighbor.gguf"  # otra carpeta: no cuenta
    lonely.write_bytes(b"z" * 1024)
    _register({"id": "solo", "path": str(main), "size_gb": 1.0,
               "metadata": dict(rec.GENERIC_META)})
    _register({"id": "neighbor", "filename": "unrelated-mtp-neighbor.gguf",
               "path": str(lonely), "size_gb": 0.01})

    res = await client.post("/api/hardware/recommend",
                            json={"model_id": "solo"})
    data = res.json()
    assert "mtp_draft_model" not in data["params"]
    assert "mmproj_path" not in data["params"]
    warns = [w["key"] for w in data["warnings"]]
    assert "mtp_no_room" not in warns and "vision_no_room" not in warns

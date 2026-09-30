"""
test_piper_runtime.py — runtime de voces neuronales Piper (TTS-1).

Sin red ni ONNX: `runtime._download_stage` se stubbea (escribe los archivos
"descargados" a mano) y el paquete `piper` se simula vía sys.modules, así la
suite verifica catálogo, estados, download/move/clean y los endpoints SSE sin
tocar HuggingFace ni cargar modelos.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

import piper_runtime
import piper_runtime_api  # noqa: F401  (montado por main.py; smoke de carga)
import runtime as runtime_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_voice_files(name: str) -> dict[str, Path]:
    """Marca una voz como descargada tocando sus onnx/json en el DATA_DIR tmp."""
    paths = piper_runtime.piper_voice_paths(name)
    paths["onnx"].parent.mkdir(parents=True, exist_ok=True)
    # 1 MiB para que size_mb (redondeado a 2 decimales) no caiga en 0.0.
    paths["onnx"].write_bytes(b"o" * (1024 * 1024))
    paths["json"].write_bytes(b'{"@loader": "piper"}')
    return paths


def fake_download_stage_factory(fail: str | None = None):
    """
    Reemplazo de runtime._download_stage: escribe en la carpeta de trabajo un
    archivo por entrada (tamaño real recortado) y emite progreso. Si `fail`
    está, levanta RuntimeError(fail) para el escenario de error.
    """
    progress_calls: list[tuple[float, str]] = []

    async def fake_stage(entries, work: Path, on_progress, pct_lo, pct_hi):
        for i, entry in enumerate(entries):
            if fail:
                raise RuntimeError(fail)
            (Path(work) / entry["name"]).write_bytes(b"x" * min(entry["size"], 4096))
            pct = pct_lo + (pct_hi - pct_lo) * ((i + 1) / len(entries))
            on_progress(pct, f"descargando {entry['name']}")
            progress_calls.append((pct, f"descargando {entry['name']}"))

    return fake_stage, progress_calls


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def test_catalogo_seis_voces_completo():
    assert len(piper_runtime.PIPER_VOICES) == 6
    assert piper_runtime.PIPER_DEFAULT_VOICE in piper_runtime.PIPER_VOICES
    for name, spec in piper_runtime.PIPER_VOICES.items():
        assert set(spec) == {
            "label", "onnx", "json", "url", "sha256", "size",
            "json_url", "json_sha256", "json_size",
        }, name
        assert len(spec["sha256"]) == 64
        assert int(spec["sha256"], 16) >= 0
        assert int(spec["json_sha256"], 16) >= 0
        assert spec["size"] > 0 and spec["json_size"] > 0
        assert name in spec["onnx"] and spec["onnx"].endswith(".onnx")
        assert spec["json"].endswith(".onnx.json")


def test_catalogo_todas_las_urls_usan_v1():
    for name, spec in piper_runtime.PIPER_VOICES.items():
        assert "resolve/v1.0.0/" in spec["url"], name
        assert "resolve/v1.0.0/" in spec["json_url"], name


def test_voice_paths_desconocida_levanta_value_error():
    with pytest.raises(ValueError):
        piper_runtime.piper_voice_paths("es_MX-ghost-medium")


# ---------------------------------------------------------------------------
# Estados de voz
# ---------------------------------------------------------------------------


def test_voice_status_missing_en_catalogo(tmp_path):
    status = piper_runtime.piper_voice_status(piper_runtime.PIPER_DEFAULT_VOICE)
    assert status["state"] == "missing"
    assert status["error"] is None
    assert status["size_mb"] == 0.0
    assert status["download_mb"] > 0
    spec = piper_runtime.PIPER_VOICES[piper_runtime.PIPER_DEFAULT_VOICE]
    assert status["download_mb"] == round((spec["size"] + spec["json_size"]) / (1024 * 1024), 1)


def test_voice_status_ready_con_archivos_en_disco(tmp_path):
    make_voice_files("es_ES-davefx-medium")
    status = piper_runtime.piper_voice_status("es_ES-davefx-medium")
    assert status["state"] == "ready"
    assert status["size_mb"] > 0


def test_voice_status_downloading_por_meta(tmp_path):
    piper_runtime._record_voice_state("es_MX-claude-high", "downloading")
    assert piper_runtime.piper_voice_status("es_MX-claude-high")["state"] == "downloading"


def test_voice_status_downloading_por_lock_en_memoria(tmp_path):
    piper_runtime._downloading_voices.add("es_MX-claude-high")
    try:
        assert piper_runtime.piper_voice_status("es_MX-claude-high")["state"] == "downloading"
    finally:
        piper_runtime._downloading_voices.discard("es_MX-claude-high")


def test_voice_status_error_por_meta_incluye_mensaje(tmp_path):
    piper_runtime._record_voice_state("es_AR-daniela-high", "error", "sha256 inválido")
    status = piper_runtime.piper_voice_status("es_AR-daniela-high")
    assert status["state"] == "error"
    assert status["error"] == "sha256 inválido"


def test_voice_status_desconocida_levanta_value_error(tmp_path):
    with pytest.raises(ValueError):
        piper_runtime.piper_voice_status("es_MX-ghost-medium")


def test_voices_status_devuelve_el_catalogo_completo(tmp_path):
    statuses = piper_runtime.piper_voices_status()
    assert [s["name"] for s in statuses] == list(piper_runtime.PIPER_VOICES)


# ---------------------------------------------------------------------------
# Descarga (runtime._download_stage stubbeado)
# ---------------------------------------------------------------------------


async def test_download_mueve_archivos_y_deja_voz_ready(tmp_path, monkeypatch):
    fake_stage, progress_calls = fake_download_stage_factory()
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    result = await piper_runtime.download_piper_voice("es_ES-davefx-medium", lambda pct, d: None)

    assert result["state"] == "ready"
    paths = piper_runtime.piper_voice_paths("es_ES-davefx-medium")
    assert paths["onnx"].is_file()
    assert paths["json"].is_file()
    assert (paths["onnx"].parent / ".piper-voices-meta.json").is_file()
    # La carpeta de trabajo se limpia.
    assert not (tmp_path / "runtime" / "tts" / ".voice-download").exists()
    assert len(progress_calls) == 2  # un progreso por archivo


async def test_download_falla_registra_error_y_propaga(tmp_path, monkeypatch):
    fake_stage, _ = fake_download_stage_factory(fail="sha256 no coincide")
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    with pytest.raises(RuntimeError, match="sha256 no coincide"):
        await piper_runtime.download_piper_voice("en_US-lessac-medium", lambda pct, d: None)

    status = piper_runtime.piper_voice_status("en_US-lessac-medium")
    assert status["state"] == "error"
    assert "sha256" in (status["error"] or "")
    # No quedó marcada como downloading en memoria ni en meta.
    assert "en_US-lessac-medium" not in piper_runtime._downloading_voices


async def test_download_voz_desconocida_levanta_value_error(tmp_path):
    with pytest.raises(ValueError):
        await piper_runtime.download_piper_voice("es_MX-ghost-medium", lambda pct, d: None)


# ---------------------------------------------------------------------------
# Eliminación
# ---------------------------------------------------------------------------


def test_delete_borra_archivos_y_meta(tmp_path):
    make_voice_files("es_MX-claude-high")
    piper_runtime._record_voice_state("es_MX-claude-high", "ready")
    piper_runtime.delete_piper_voice("es_MX-claude-high")
    paths = piper_runtime.piper_voice_paths("es_MX-claude-high")
    assert not paths["onnx"].exists()
    assert not paths["json"].exists()
    assert piper_runtime.piper_voice_status("es_MX-claude-high")["state"] == "missing"


def test_delete_desconocida_levanta_value_error(tmp_path):
    with pytest.raises(ValueError):
        piper_runtime.delete_piper_voice("es_MX-ghost-medium")


# ---------------------------------------------------------------------------
# Estado del motor (paquete piper simulado vía sys.modules)
# ---------------------------------------------------------------------------


def test_engine_status_importable(tmp_path, monkeypatch):
    fake_piper = types.ModuleType("piper")
    monkeypatch.setitem(sys.modules, "piper", fake_piper)
    status = piper_runtime.piper_engine_status()
    assert status["available"] is True
    assert "piper" in status["detail"]


def test_engine_status_no_importable(tmp_path, monkeypatch):
    # sys.modules["piper"] = None hace que `import piper` levante ImportError.
    monkeypatch.setitem(sys.modules, "piper", None)
    status = piper_runtime.piper_engine_status()
    assert status["available"] is False
    assert "piper no importable" in status["detail"]


def test_runtime_status_forma_general(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "piper", None)
    status = piper_runtime.piper_runtime_status()
    assert status["default_voice"] == piper_runtime.PIPER_DEFAULT_VOICE
    assert status["can_download"] is True
    assert status["engine"]["available"] is False
    assert [v["name"] for v in status["voices"]] == list(piper_runtime.PIPER_VOICES)


# ---------------------------------------------------------------------------
# Saneo de phoneme_type (json corrupto servido por HuggingFace, 2026-09)
# ---------------------------------------------------------------------------


def _write_voice_json(name: str, config: dict) -> Path:
    paths = piper_runtime.piper_voice_paths(name)
    paths["onnx"].parent.mkdir(parents=True, exist_ok=True)
    paths["onnx"].write_bytes(b"o" * (1024 * 1024))
    paths["json"].write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return paths["json"]


def test_sanitize_reescribe_phoneme_type_corrupto(tmp_path):
    json_path = _write_voice_json(
        "en_US-ryan-high",
        {"phoneme_type": "PhonemeType.ESPEAK", "num_symbols": 130, "espeak": {"voice": "en-us"}},
    )
    assert piper_runtime.sanitize_voice_config("en_US-ryan-high") is True
    config = json.loads(json_path.read_text(encoding="utf-8"))
    assert config["phoneme_type"] == "espeak"
    assert config["num_symbols"] == 130  # el resto del json se conserva
    assert config["espeak"] == {"voice": "en-us"}


def test_sanitize_idempotente(tmp_path):
    _write_voice_json("es_ES-davefx-medium", {"phoneme_type": "PhonemeType.ESPEAK"})
    assert piper_runtime.sanitize_voice_config("es_ES-davefx-medium") is True
    assert piper_runtime.sanitize_voice_config("es_ES-davefx-medium") is False


def test_sanitize_no_toca_valor_valido(tmp_path):
    json_path = _write_voice_json("es_ES-davefx-medium", {"phoneme_type": "espeak"})
    before = json_path.read_bytes()
    assert piper_runtime.sanitize_voice_config("es_ES-davefx-medium") is False
    assert json_path.read_bytes() == before


def test_sanitize_no_toca_valor_desconocido(tmp_path):
    json_path = _write_voice_json("es_MX-claude-high", {"phoneme_type": "algo-ajeno"})
    before = json_path.read_bytes()
    assert piper_runtime.sanitize_voice_config("es_MX-claude-high") is False
    assert json_path.read_bytes() == before


def test_sanitize_sin_archivo_es_noop(tmp_path):
    assert piper_runtime.sanitize_voice_config("es_AR-daniela-high") is False


def test_sanitize_json_invalido_es_noop(tmp_path):
    paths = piper_runtime.piper_voice_paths("en_US-lessac-medium")
    paths["json"].parent.mkdir(parents=True, exist_ok=True)
    paths["json"].write_bytes(b"no soy json")
    assert piper_runtime.sanitize_voice_config("en_US-lessac-medium") is False


async def test_download_sanea_json_corrupto_antes_de_ready(tmp_path, monkeypatch):
    async def fake_stage(entries, work: Path, on_progress, pct_lo, pct_hi):
        for entry in entries:
            if entry["name"].endswith(".json"):
                (Path(work) / entry["name"]).write_text(
                    json.dumps({"phoneme_type": "PhonemeType.ESPEAK"}), encoding="utf-8"
                )
            else:
                (Path(work) / entry["name"]).write_bytes(b"x" * 1024)
            on_progress(pct_hi, f"descargando {entry['name']}")

    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)
    result = await piper_runtime.download_piper_voice("en_US-amy-medium", lambda pct, d: None)

    assert result["state"] == "ready"
    config = json.loads(piper_runtime.piper_voice_paths("en_US-amy-medium")["json"].read_text(encoding="utf-8"))
    assert config["phoneme_type"] == "espeak"


# ---------------------------------------------------------------------------
# Endpoints /api/tts/piper/*
# ---------------------------------------------------------------------------


async def test_api_status(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "piper", None)
    res = await client.get("/api/tts/piper/status")
    assert res.status_code == 200
    data = res.json()
    assert data["engine"]["available"] is False
    assert data["default_voice"] == piper_runtime.PIPER_DEFAULT_VOICE
    assert len(data["voices"]) == len(piper_runtime.PIPER_VOICES)
    for v in data["voices"]:
        assert v["state"] == "missing"


async def test_api_voces(client):
    res = await client.get("/api/tts/piper/voices")
    assert res.status_code == 200
    assert [v["name"] for v in res.json()["voices"]] == list(piper_runtime.PIPER_VOICES)


async def test_api_download_voz_desconocida_da_404(client):
    res = await client.post("/api/tts/piper/voices/es_MX-ghost-medium/download")
    assert res.status_code == 404


async def test_api_download_concurrente_da_409(client):
    piper_runtime._downloading_voices.add("es_AR-daniela-high")
    try:
        res = await client.post("/api/tts/piper/voices/es_AR-daniela-high/download")
        assert res.status_code == 409
    finally:
        piper_runtime._downloading_voices.discard("es_AR-daniela-high")


async def test_api_download_sse_progreso_y_done(client, monkeypatch):
    fake_stage, _ = fake_download_stage_factory()
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    res = await client.post("/api/tts/piper/voices/es_ES-davefx-medium/download")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = [
        json.loads(line[len("data: "):])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]
    assert any(e["type"] == "progress" for e in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["status"]["state"] == "ready"
    assert piper_runtime.piper_voice_status("es_ES-davefx-medium")["state"] == "ready"


async def test_api_download_sse_error(client, monkeypatch):
    fake_stage, _ = fake_download_stage_factory(fail="red caida")
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    res = await client.post("/api/tts/piper/voices/es_AR-daniela-high/download")
    assert res.status_code == 200
    events = [
        json.loads(line[len("data: "):])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "error"
    assert "red caida" in events[-1]["message"]
    assert piper_runtime.piper_voice_status("es_AR-daniela-high")["state"] == "error"


async def test_api_delete(client):
    make_voice_files("en_US-lessac-medium")
    res = await client.delete("/api/tts/piper/voices/en_US-lessac-medium")
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == "en_US-lessac-medium"
    assert body["status"]["state"] == "missing"


async def test_api_delete_desconocida_da_404(client):
    res = await client.delete("/api/tts/piper/voices/es_MX-ghost-medium")
    assert res.status_code == 404

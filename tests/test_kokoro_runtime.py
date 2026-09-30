"""
test_kokoro_runtime.py — runtime del motor TTS neural Kokoro (TTS-2).

Sin red ni ONNX real: `runtime._download_stage` se stubbea (escribe los
archivos "descargados" a mano) y `kokoro_onnx` se simula vía sys.modules,
así la suite verifica catálogo, estados, download/move/clean, el parche de
speed, la normalización de números y los endpoints SSE sin tocar GitHub ni
cargar el modelo de 325 MB.
"""

from __future__ import annotations

import io
import json
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

import kokoro_runtime
import kokoro_runtime_api  # noqa: F401  (montado por main.py; smoke de carga)
import runtime as runtime_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_model_files() -> dict[str, Path]:
    """Marca el modelo como descargado tocando onnx+bin en el DATA_DIR tmp."""
    paths = kokoro_runtime.kokoro_model_paths()
    paths["onnx"].parent.mkdir(parents=True, exist_ok=True)
    # 1 MiB por archivo para que size_mb (redondeado a 1 decimal) no caiga en 0.
    paths["onnx"].write_bytes(b"o" * (1024 * 1024))
    paths["bin"].write_bytes(b"b" * (1024 * 1024))
    return paths


def fake_download_stage_factory(fail: str | None = None):
    """
    Reemplazo de runtime._download_stage: escribe en la carpeta de trabajo un
    archivo por entrada y emite progreso. Si `fail` está, levanta RuntimeError.
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


def _stub_kokoro_package(monkeypatch, model_cls):
    """
    sys.modules["kokoro_onnx"] -> paquete fake con Kokoro=model_cls y el
    submodule config con las constantes que usa el parche.
    """
    fake_pkg = types.ModuleType("kokoro_onnx")
    fake_pkg.Kokoro = model_cls
    fake_cfg = types.ModuleType("kokoro_onnx.config")
    fake_cfg.MAX_PHONEME_LENGTH = 510
    fake_cfg.SAMPLE_RATE = 24000
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_pkg)
    monkeypatch.setitem(sys.modules, "kokoro_onnx.config", fake_cfg)


# La función original, capturada al importar el módulo (el autouse
# _kokoro_engine_off de conftest solo la pisa test a test, nunca el módulo).
kokoro_runtime_real_engine_status = kokoro_runtime.kokoro_engine_status


@pytest.fixture
def kokoro_engine_real(monkeypatch):
    """
    Restaura la kokoro_engine_status real: el fixture autouse
    _kokoro_engine_off de conftest la deshabilita para los tests de /speak y
    /status, pero estos prueban la función de verdad contra sys.modules.
    """
    monkeypatch.setattr(
        kokoro_runtime, "kokoro_engine_status", kokoro_runtime_real_engine_status
    )


class _FakeInput:
    def __init__(self, name: str, type_: str) -> None:
        self.name = name
        self.type = type_


def _fake_session(speed_declared: str):
    sess = types.SimpleNamespace()
    sess.get_inputs = lambda: [
        _FakeInput("input_ids", "tensor(int64)"),
        _FakeInput("style", "tensor(float)"),
        _FakeInput("speed", speed_declared),
    ]
    return sess


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def test_catalogo_cinco_voces_completo():
    assert len(kokoro_runtime.KOKORO_VOICES) == 5
    assert kokoro_runtime.KOKORO_DEFAULT_VOICE in kokoro_runtime.KOKORO_VOICES
    for name, spec in kokoro_runtime.KOKORO_VOICES.items():
        assert set(spec) == {"label", "lang"}, name
        assert spec["lang"] in ("es", "en-us"), name
        # El prefijo de la voz indica el idioma (ef_/em_ español, af_/am_ inglés).
        assert name[:2] in ("ef", "em", "af", "am"), name


def test_modelo_una_fuente_con_sha256_y_tamaños():
    model = kokoro_runtime.KOKORO_MODEL
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    assert model["url"].startswith(base)
    assert model["voices_url"].startswith(base)
    assert model["url"].endswith(model["filename"])
    assert model["voices_url"].endswith(model["voices_filename"])
    for key in ("sha256", "voices_sha256"):
        assert len(model[key]) == 64, key
        int(model[key], 16) >= 0
    assert model["size"] > 100 * 1024 * 1024
    assert model["voices_size"] > 10 * 1024 * 1024
    assert model["voices_count"] == 54


# ---------------------------------------------------------------------------
# Estados del modelo
# ---------------------------------------------------------------------------


def test_model_status_missing_en_disco(tmp_path):
    status = kokoro_runtime.kokoro_model_status()
    assert status["state"] == "missing"
    assert status["error"] is None
    assert status["size_mb"] == 0.0
    assert status["download_mb"] > 0
    model = kokoro_runtime.KOKORO_MODEL
    expected = round((model["size"] + model["voices_size"]) / (1024 * 1024), 1)
    assert status["download_mb"] == expected


def test_model_status_ready_con_archivos_en_disco(tmp_path):
    make_model_files()
    status = kokoro_runtime.kokoro_model_status()
    assert status["state"] == "ready"
    assert status["size_mb"] > 0
    assert status["voices_included"] == 54


def test_model_status_downloading_por_meta(tmp_path):
    kokoro_runtime._record_model_state("downloading")
    assert kokoro_runtime.kokoro_model_status()["state"] == "downloading"


def test_model_status_downloading_por_lock_en_memoria(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro_runtime, "_downloading", True)
    assert kokoro_runtime.kokoro_model_status()["state"] == "downloading"


def test_model_status_error_por_meta_incluye_mensaje(tmp_path):
    kokoro_runtime._record_model_state("error", "sha256 inválido")
    status = kokoro_runtime.kokoro_model_status()
    assert status["state"] == "error"
    assert status["error"] == "sha256 inválido"


def test_model_status_downloading_pisa_a_ready(tmp_path, monkeypatch):
    make_model_files()
    kokoro_runtime._record_model_state("downloading")
    monkeypatch.setattr(kokoro_runtime, "_downloading", True)
    assert kokoro_runtime.kokoro_model_status()["state"] == "downloading"


# ---------------------------------------------------------------------------
# Descarga (runtime._download_stage stubbeado)
# ---------------------------------------------------------------------------


async def test_download_mueve_archivos_y_deja_modelo_ready(tmp_path, monkeypatch):
    fake_stage, progress_calls = fake_download_stage_factory()
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    result = await kokoro_runtime.download_kokoro_model(lambda pct, d: None)

    assert result["state"] == "ready"
    paths = kokoro_runtime.kokoro_model_paths()
    assert paths["onnx"].is_file()
    assert paths["bin"].is_file()
    assert (paths["onnx"].parent / kokoro_runtime.MODEL_META_FILE).is_file()
    # La carpeta de trabajo se limpia.
    assert not (tmp_path / "runtime" / "tts" / ".kokoro-download").exists()
    assert len(progress_calls) == 2  # un progreso por archivo (onnx + bin)


async def test_download_falla_registra_error_y_propaga(tmp_path, monkeypatch):
    fake_stage, _ = fake_download_stage_factory(fail="sha256 no coincide")
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    with pytest.raises(RuntimeError, match="sha256 no coincide"):
        await kokoro_runtime.download_kokoro_model(lambda pct, d: None)

    status = kokoro_runtime.kokoro_model_status()
    assert status["state"] == "error"
    assert "sha256" in (status["error"] or "")
    assert kokoro_runtime._downloading is False


async def test_download_concurrente_levanta_runtime_error(tmp_path, monkeypatch):
    fake_stage, _ = fake_download_stage_factory()
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)
    monkeypatch.setattr(kokoro_runtime, "_downloading", True)

    with pytest.raises(RuntimeError, match="descarga del modelo Kokoro en curso"):
        await kokoro_runtime.download_kokoro_model(lambda pct, d: None)


# ---------------------------------------------------------------------------
# Eliminación
# ---------------------------------------------------------------------------


def test_delete_borra_archivos_y_meta(tmp_path):
    make_model_files()
    kokoro_runtime._record_model_state("ready")
    kokoro_runtime.delete_kokoro_model()
    paths = kokoro_runtime.kokoro_model_paths()
    assert not paths["onnx"].exists()
    assert not paths["bin"].exists()
    assert kokoro_runtime.kokoro_model_status()["state"] == "missing"


def test_delete_sin_archivos_es_noop(tmp_path):
    kokoro_runtime.delete_kokoro_model()
    assert kokoro_runtime.kokoro_model_status()["state"] == "missing"


# ---------------------------------------------------------------------------
# Estado del motor (paquete kokoro_onnx simulado vía sys.modules)
# ---------------------------------------------------------------------------


def test_engine_status_importable(tmp_path, monkeypatch, kokoro_engine_real):
    _stub_kokoro_package(monkeypatch, object)
    status = kokoro_runtime.kokoro_engine_status()
    assert status["available"] is True
    assert "kokoro" in status["detail"]


def test_engine_status_no_importable(tmp_path, monkeypatch, kokoro_engine_real):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    status = kokoro_runtime.kokoro_engine_status()
    assert status["available"] is False
    assert "no importable" in status["detail"]


def test_runtime_status_forma_general(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    status = kokoro_runtime.kokoro_runtime_status()
    assert status["default_voice"] == kokoro_runtime.KOKORO_DEFAULT_VOICE
    assert status["can_download"] is True
    assert status["engine"]["available"] is False
    assert [v["name"] for v in status["voices"]] == list(kokoro_runtime.KOKORO_VOICES)


# ---------------------------------------------------------------------------
# Parche de speed (kokoro-onnx 0.4.7 manda int32; el export espera float)
# ---------------------------------------------------------------------------


def test_speed_input_dtype_por_tipo_declarado():
    assert kokoro_runtime._speed_input_dtype(_fake_session("tensor(float)")) is np.float32
    assert kokoro_runtime._speed_input_dtype(_fake_session("tensor(int32)")) is np.int32
    assert kokoro_runtime._speed_input_dtype(_fake_session("tensor(int64)")) is np.int64
    assert kokoro_runtime._speed_input_dtype(_fake_session("tensor(float16)")) is np.float16
    # Sin input "speed" declarado -> default float32.
    bare = types.SimpleNamespace(
        get_inputs=lambda: [_FakeInput("input_ids", "tensor(int64)")]
    )
    assert kokoro_runtime._speed_input_dtype(bare) is np.float32


@pytest.fixture()
def patched_fake_kokoro(monkeypatch):
    """
    Aplica el parche a una clase Kokoro fake (bandera de idempotencia a
    False) y devuelve (factory de instancias, registro de inputs enviados).
    """
    calls: list[dict] = []

    class FakeKokoro:
        pass

    def make_instance(speed_declared: str):
        inst = FakeKokoro()
        inst.sess = _fake_session(speed_declared)
        inst.sess.run = lambda outputs, inputs: calls.append(inputs) or (np.zeros(3),)
        inst.tokenizer = types.SimpleNamespace(tokenize=lambda p: [101, 102])
        return inst

    _stub_kokoro_package(monkeypatch, FakeKokoro)
    monkeypatch.setattr(kokoro_runtime, "_speed_patch_applied", False)
    kokoro_runtime.apply_kokoro_speed_patch()
    return make_instance, calls


def test_speed_patch_envia_speed_con_dtype_declarado(patched_fake_kokoro):
    make_instance, calls = patched_fake_kokoro
    assert kokoro_runtime._speed_patch_applied is True

    inst = make_instance("tensor(float)")
    style = [0.25] * 256
    audio, sr = inst._create_audio("HEL LO", [None, None, style], 1.2)

    assert sr == 24000
    captured = calls[-1]
    assert captured["speed"].dtype == np.float32
    assert float(captured["speed"][0]) == pytest.approx(1.2)
    assert captured["input_ids"] == [[0, 101, 102, 0]]
    assert np.asarray(captured["input_ids"]).dtype == np.int64
    assert captured["style"].dtype == np.float32


def test_speed_patch_respeta_speed_int32(patched_fake_kokoro):
    make_instance, calls = patched_fake_kokoro
    inst = make_instance("tensor(int32)")
    inst._create_audio("HI", [None, None, [0.1] * 256], 1.0)
    assert calls[-1]["speed"].dtype == np.int32


def test_apply_speed_patch_es_idempotente(monkeypatch):
    class FakeKokoro:
        pass

    _stub_kokoro_package(monkeypatch, FakeKokoro)
    monkeypatch.setattr(kokoro_runtime, "_speed_patch_applied", False)
    kokoro_runtime.apply_kokoro_speed_patch()
    patched = FakeKokoro._create_audio
    kokoro_runtime.apply_kokoro_speed_patch()
    assert FakeKokoro._create_audio is patched


# ---------------------------------------------------------------------------
# Normalización de números (el G2P de Kokoro lee las cifras dígito a dígito)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("El costo fue 1.250,50 pesos.",
         "El costo fue mil doscientos cincuenta punto cinco pesos."),
        ("Son 1.250.500 unidades.",
         "Son un millón doscientos cincuenta mil quinientos unidades."),
        ("Tengo 3 manzanas.", "Tengo tres manzanas."),
        ("El numero 42 es especial.", "El numero cuarenta y dos es especial."),
        ("La version 0.6.3 ya esta lista.",
         "La version cero punto seis punto tres ya esta lista."),
        ("Pago 2.5 litros.", "Pago dos punto cinco litros."),
    ],
)
def test_normalize_es_convenciones_espanolas(text, expected):
    assert kokoro_runtime.normalize_numbers(text, "es") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The cost was 1,250.50 dollars.",
         "The cost was one thousand, two hundred and fifty point five dollars."),
        ("Pi is 3.14 here.", "Pi is three point one four here."),
        ("I have 3 apples.", "I have three apples."),
        ("Version 0.6.3 is ready.", "Version zero point six point three is ready."),
        ("There are 1,250,500 of them.",
         "There are one million, two hundred and fifty thousand, five hundred of them."),
    ],
)
def test_normalize_en_convenciones_inglesas(text, expected):
    assert kokoro_runtime.normalize_numbers(text, "en-us") == expected


def test_normalize_deja_teléfonos_largos_intactos():
    text = "Llama al 5551234567 ahora."
    assert "5551234567" in kokoro_runtime.normalize_numbers(text, "es")
    assert "5551234567" in kokoro_runtime.normalize_numbers(text, "en-us")


def test_normalize_sin_digitos_no_toca_nada():
    text = "Hola, como estas?"
    assert kokoro_runtime.normalize_numbers(text, "es") == text
    assert kokoro_runtime.normalize_numbers(text, "en-us") == text


def test_normalize_texto_vacio():
    assert kokoro_runtime.normalize_numbers("", "es") == ""


# ---------------------------------------------------------------------------
# Traducción de rate a speed
# ---------------------------------------------------------------------------


def test_rate_to_kokoro_speed_escalera():
    assert kokoro_runtime.rate_to_kokoro_speed(-10) == 0.5
    assert kokoro_runtime.rate_to_kokoro_speed(0) == 1.0
    assert kokoro_runtime.rate_to_kokoro_speed(5) == 1.25
    assert kokoro_runtime.rate_to_kokoro_speed(10) == 1.5


def test_rate_to_kokoro_speed_clampa_el_rango():
    assert kokoro_runtime.rate_to_kokoro_speed(-50) == 0.5
    assert kokoro_runtime.rate_to_kokoro_speed(50) == 2.0


# ---------------------------------------------------------------------------
# Síntesis (modelo fake: sin ONNX ni G2P real)
# ---------------------------------------------------------------------------


class _FakeModel:
    def __init__(self, audio=None):
        self.calls: list[dict] = []
        self.audio = audio

    def create(self, text, voice, speed=1.0, lang="en-us"):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        audio = self.audio if self.audio is not None else (
            np.sin(np.linspace(0, 6.28, 2400)).astype(np.float32) * 0.5
        )
        return audio, 24000


def test_synthesize_wav_devuelve_wav_24khz_y_normaliza(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(kokoro_runtime, "get_kokoro_model", lambda: model)

    wav = kokoro_runtime.kokoro_synthesize_wav(
        "El costo fue 1.250,50 pesos.", "ef_dora", speed=1.0
    )

    call = model.calls[-1]
    assert call["text"] == "El costo fue mil doscientos cincuenta punto cinco pesos."
    assert call["voice"] == "ef_dora"
    assert call["lang"] == "es"
    assert call["speed"] == 1.0
    with wave.open(io.BytesIO(wav)) as f:
        assert f.getframerate() == 24000
        assert f.getnchannels() == 1
        assert f.getsampwidth() == 2
        assert f.getnframes() > 0


def test_synthesize_wav_ingles_pasa_en_us(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(kokoro_runtime, "get_kokoro_model", lambda: model)
    kokoro_runtime.kokoro_synthesize_wav("I have 3 apples.", "af_heart", speed=1.3)
    call = model.calls[-1]
    assert call["text"] == "I have three apples."
    assert call["lang"] == "en-us"
    assert call["speed"] == pytest.approx(1.3)


def test_synthesize_wav_clampa_speed_fuera_de_rango(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(kokoro_runtime, "get_kokoro_model", lambda: model)
    kokoro_runtime.kokoro_synthesize_wav("hola", "ef_dora", speed=7.0)
    assert model.calls[-1]["speed"] == kokoro_runtime.KOKORO_SPEED_MAX


def test_synthesize_wav_voz_desconocida_levanta_value_error(monkeypatch):
    monkeypatch.setattr(kokoro_runtime, "get_kokoro_model", lambda: _FakeModel())
    with pytest.raises(ValueError, match="Voz Kokoro desconocida"):
        kokoro_runtime.kokoro_synthesize_wav("hola", "ef_ghost")


def test_synthesize_wav_sin_audio_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(kokoro_runtime, "get_kokoro_model", lambda: _FakeModel(audio=np.zeros(0)))
    with pytest.raises(RuntimeError, match="no generó audio"):
        kokoro_runtime.kokoro_synthesize_wav("hola", "ef_dora")


# ---------------------------------------------------------------------------
# Carga del modelo cacheado
# ---------------------------------------------------------------------------


def test_get_kokoro_model_sin_archivos_levanta_runtime_error(tmp_path, monkeypatch):
    # Una clase (no `object`): el parche escribe Kokoro._create_audio.
    _stub_kokoro_package(monkeypatch, type("FakeKokoro", (), {}))
    with pytest.raises(RuntimeError, match="no está descargado"):
        kokoro_runtime.get_kokoro_model()


def test_get_kokoro_model_cachea_la_instancia(tmp_path, monkeypatch):
    created: list[dict] = []

    class FakeKokoro:
        def __init__(self, model_path, voices_path):
            created.append({"model": model_path, "voices": voices_path})

    _stub_kokoro_package(monkeypatch, FakeKokoro)
    make_model_files()

    first = kokoro_runtime.get_kokoro_model()
    second = kokoro_runtime.get_kokoro_model()
    assert first is second
    assert len(created) == 1
    paths = kokoro_runtime.kokoro_model_paths()
    assert created[0]["model"] == str(paths["onnx"])
    assert created[0]["voices"] == str(paths["bin"])


def test_get_kokoro_model_paquete_faltante_levanta_runtime_error(tmp_path, monkeypatch):
    make_model_files()
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    with pytest.raises(RuntimeError, match="no está disponible"):
        kokoro_runtime.get_kokoro_model()


# ---------------------------------------------------------------------------
# Endpoints /api/tts/kokoro/*
# ---------------------------------------------------------------------------


async def test_api_status(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    res = await client.get("/api/tts/kokoro/status")
    assert res.status_code == 200
    data = res.json()
    assert data["engine"]["available"] is False
    assert data["default_voice"] == kokoro_runtime.KOKORO_DEFAULT_VOICE
    assert data["model"]["state"] == "missing"
    assert [v["name"] for v in data["voices"]] == list(kokoro_runtime.KOKORO_VOICES)


async def test_api_voces(client):
    res = await client.get("/api/tts/kokoro/voices")
    assert res.status_code == 200
    voices = res.json()["voices"]
    assert [v["name"] for v in voices] == list(kokoro_runtime.KOKORO_VOICES)
    for v in voices:
        assert set(v) == {"name", "label", "lang"}


async def test_api_download_modelo_sse_progreso_y_done(client, monkeypatch):
    fake_stage, progress_calls = fake_download_stage_factory()
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    res = await client.post("/api/tts/kokoro/model/download")
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
    assert kokoro_runtime.kokoro_model_status()["state"] == "ready"
    assert len(progress_calls) == 2


async def test_api_download_modelo_sse_error(client, monkeypatch):
    fake_stage, _ = fake_download_stage_factory(fail="red caida")
    monkeypatch.setattr(runtime_module, "_download_stage", fake_stage)

    res = await client.post("/api/tts/kokoro/model/download")
    assert res.status_code == 200
    events = [
        json.loads(line[len("data: "):])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "error"
    assert "red caida" in events[-1]["message"]
    assert kokoro_runtime.kokoro_model_status()["state"] == "error"


async def test_api_download_concurrente_da_409(client, monkeypatch):
    monkeypatch.setattr(kokoro_runtime, "_downloading", True)
    res = await client.post("/api/tts/kokoro/model/download")
    assert res.status_code == 409


async def test_api_delete_modelo(client):
    make_model_files()
    kokoro_runtime._record_model_state("ready")
    res = await client.delete("/api/tts/kokoro/model")
    assert res.status_code == 200
    body = res.json()
    assert body["status"]["state"] == "missing"
    paths = kokoro_runtime.kokoro_model_paths()
    assert not paths["onnx"].exists()
    assert not paths["bin"].exists()

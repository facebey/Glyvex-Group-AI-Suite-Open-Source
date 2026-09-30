"""
test_stt_engine.py — T2.3.4: tests del motor whisper.cpp en stt.py.

whisper-cli se simula con un "binario" fake (.bat en Windows, .sh en POSIX)
que registra sus argumentos en $FAKE_STT_LOG y termina con el código que el
test defina; el JSON de salida se escribe a mano por el test (mismo contrato
que el binario real: queda en <out_prefix>.json).
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest

import stt
import stt_runtime


def _make_fake_cli(tmp_path, exit_code: int = 0, name: str = "fake_cli") -> Path:
    n = _make_fake_cli.counter
    _make_fake_cli.counter = n + 1
    if platform.system() == "Windows":
        script = tmp_path / f"{name}_{n}.bat"
        script.write_text(
            "@echo off\n"
            'if defined FAKE_STT_LOG echo %* > "%FAKE_STT_LOG%"'
            f"\nexit {exit_code}\n",
            encoding="utf-8",
        )
    else:
        script = tmp_path / f"{name}_{n}.sh"
        script.write_text(
            "#!/bin/sh\n"
            'if [ -n "$FAKE_STT_LOG" ]; then echo "$@" > "$FAKE_STT_LOG"; fi\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    return script


_make_fake_cli.counter = 0


def _force_windows(monkeypatch):
    # stt_runtime lee platform.system() en runtime (no en import), así que
    # parchear el módulo alcanza para que los estados asuman Windows en CI.
    monkeypatch.setattr(stt_runtime.platform, "system", lambda: "Windows")


def _write_result_json(prefix: Path, language: str = "es", segments=None) -> Path:
    if segments is None:
        segments = [
            {"offsets": {"from": 0, "to": 1500}, "text": " Hola"},
            {"offsets": {"from": 1500, "to": 3000}, "text": "mundo."},
        ]
    data = {
        "result": {"language": language},
        "transcription": segments,
    }
    out = Path(str(prefix) + ".json")
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# _transcribe_whispercpp_sync
# --------------------------------------------------------------------------


def test_transcribe_parses_json_and_joins_segments(tmp_path):
    fake = _make_fake_cli(tmp_path)
    _write_result_json(tmp_path / "out")
    text, lang, duration = stt._transcribe_whispercpp_sync(
        fake, tmp_path / "ggml-base.bin", str(tmp_path / "a.wav"),
        str(tmp_path / "out"), 8, "es",
    )
    assert text == "Hola mundo."
    assert lang == "es"
    assert duration == 3.0


def test_transcribe_language_region_is_stripped(tmp_path, monkeypatch):
    fake = _make_fake_cli(tmp_path)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_STT_LOG", str(log))
    _write_result_json(tmp_path / "out")
    stt._transcribe_whispercpp_sync(
        fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
        str(tmp_path / "out"), 8, "es-AR",
    )
    argv = log.read_text(encoding="utf-8")
    assert "-l" in argv and "es" in argv
    assert "es-AR" not in argv


def test_transcribe_empty_language_omits_flag(tmp_path, monkeypatch):
    fake = _make_fake_cli(tmp_path)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_STT_LOG", str(log))
    _write_result_json(tmp_path / "out", language="en")
    text, lang, _ = stt._transcribe_whispercpp_sync(
        fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
        str(tmp_path / "out"), 8, "",
    )
    argv = log.read_text(encoding="utf-8")
    assert "-l" not in argv
    assert lang == "en"


def test_transcribe_translate_anchors_source_language(tmp_path, monkeypatch):
    # STT-5: --translate traduce a inglés y ancla el origen con -l cuando es
    # conocido; sin anclaje el modelo base maldetecta ES como EN y la
    # traducción queda en no-op (vuelve el texto original en español).
    fake = _make_fake_cli(tmp_path)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_STT_LOG", str(log))
    _write_result_json(tmp_path / "out", language="es")
    stt._transcribe_whispercpp_sync(
        fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
        str(tmp_path / "out"), 8, "es",
        translate=True,
    )
    argv = log.read_text(encoding="utf-8")
    assert "--translate" in argv
    assert "-l" in argv and "es" in argv


def test_transcribe_translate_auto_source_omits_flag(tmp_path, monkeypatch):
    # translate + origen "auto"/vacío → sin -l: el binario detecta solo.
    fake = _make_fake_cli(tmp_path)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_STT_LOG", str(log))
    _write_result_json(tmp_path / "out", language="en")
    stt._transcribe_whispercpp_sync(
        fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
        str(tmp_path / "out"), 8, "auto",
        translate=True,
    )
    argv = log.read_text(encoding="utf-8")
    assert "--translate" in argv
    assert "-l" not in argv


def test_transcribe_no_translate_keeps_language(tmp_path, monkeypatch):
    fake = _make_fake_cli(tmp_path)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_STT_LOG", str(log))
    _write_result_json(tmp_path / "out")
    stt._transcribe_whispercpp_sync(
        fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
        str(tmp_path / "out"), 8, "es",
        translate=False,
    )
    argv = log.read_text(encoding="utf-8")
    assert "--translate" not in argv
    assert "-l" in argv


def test_transcribe_nonzero_exit_raises(tmp_path):
    fake = _make_fake_cli(tmp_path, exit_code=1)
    with pytest.raises(RuntimeError, match="terminó con 1"):
        stt._transcribe_whispercpp_sync(
            fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
            str(tmp_path / "out"), 8, "es",
        )


def test_transcribe_missing_json_raises(tmp_path):
    fake = _make_fake_cli(tmp_path, exit_code=0)
    with pytest.raises(RuntimeError, match="no produjo"):
        stt._transcribe_whispercpp_sync(
            fake, tmp_path / "m.bin", str(tmp_path / "a.wav"),
            str(tmp_path / "out"), 8, "es",
        )


# --------------------------------------------------------------------------
# Resolución de idioma para whisper.cpp (fallback a stt.language)
# --------------------------------------------------------------------------


def test_stt_language_falls_back_to_stt_language():
    # whisper_language vacío (default): debe caer a stt.language para no
    # heredar el default de inglés de whisper-cli.
    assert stt._stt_language({"whisper_language": "", "language": "es-AR"}) == "es-AR"


def test_stt_language_override_wins():
    settings = {"whisper_language": "en", "language": "es-AR"}
    assert stt._stt_language(settings, "de") == "de"
    assert stt._stt_language(settings, None) == "en"


def test_stt_language_empty_falls_back_to_app_language(monkeypatch):
    # Sin override ni stt.language: cae al idioma de la app, no al auto-detect
    # nativo de whisper.cpp (modelo base sesga a inglés).
    monkeypatch.setattr(stt, "_app_language", lambda: "fr")
    assert stt._stt_language({}, None) == "fr"


def test_app_language_strips_region_and_defaults(monkeypatch):
    # 'es-AR' → 'es'; si la key no existe, default 'es'.
    def fake_get(key, default=None):
        return "es-AR" if key == "app.language" else default

    monkeypatch.setattr(stt.config, "get", fake_get)
    assert stt._app_language() == "es"
    monkeypatch.setattr(stt.config, "get", lambda key, default=None: default)
    assert stt._app_language() == "es"


def test_faster_whisper_language_strips_region(monkeypatch):
    monkeypatch.setattr(stt, "_app_language", lambda: "it")
    assert stt._faster_whisper_language({"whisper_language": "", "language": "es-AR"}) == "es"
    assert stt._faster_whisper_language({"language": "fr"}, None) == "fr"
    assert stt._faster_whisper_language({"language": "auto"}, None) == ""
    # Vacío ya resuelve al idioma de la app (no a detección).
    assert stt._faster_whisper_language({"language": ""}, None) == "it"


# --------------------------------------------------------------------------
# Selección de motor
# --------------------------------------------------------------------------


def test_whispercpp_binary_none_when_runtime_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(stt_runtime, "DATA_DIR", tmp_path)
    assert stt._whispercpp_binary() is None


def test_whispercpp_model_path_unknown_name_returns_none():
    assert stt._whispercpp_model_path("tiny") is None
    assert stt._whispercpp_model_path("tiny.en") is None


def test_whispercpp_model_path_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(stt_runtime, "DATA_DIR", tmp_path)
    assert stt._whispercpp_model_path("base") is None


def test_effective_engine_auto_prefers_whispercpp_when_ready(monkeypatch):
    monkeypatch.setattr(stt, "_whispercpp_binary", lambda: Path("bin"))
    monkeypatch.setattr(stt, "_whispercpp_model_path", lambda m: Path("m.bin") if m == "base" else None)
    assert stt._effective_engine({"engine": "auto", "model": "base"}) == "whispercpp"
    assert stt._effective_engine({"engine": "auto", "model": "tiny"}) == "whisper"


def test_effective_engine_auto_falls_back_to_whisper(monkeypatch):
    monkeypatch.setattr(stt, "_whispercpp_binary", lambda: None)
    assert stt._effective_engine({"engine": "auto", "model": "base"}) == "whisper"


def test_effective_engine_auto_falls_back_to_browser_when_frozen(monkeypatch):
    # STT-1: en el bundle faster-whisper no existe; auto debe caer a browser.
    monkeypatch.setattr(stt, "FROZEN", True)
    monkeypatch.setattr(stt, "_whispercpp_binary", lambda: None)
    assert stt._effective_engine({"engine": "auto", "model": "base"}) == "browser"


def test_effective_engine_frozen_still_prefers_whispercpp(monkeypatch):
    monkeypatch.setattr(stt, "FROZEN", True)
    monkeypatch.setattr(stt, "_whispercpp_binary", lambda: Path("bin"))
    monkeypatch.setattr(stt, "_whispercpp_model_path", lambda m: Path("m.bin"))
    assert stt._effective_engine({"engine": "auto", "model": "base"}) == "whispercpp"


def test_effective_engine_forced_is_untouched():
    assert stt._effective_engine({"engine": "whispercpp", "model": "base"}) == "whispercpp"
    assert stt._effective_engine({"engine": "whisper", "model": "base"}) == "whisper"
    assert stt._effective_engine({"engine": "browser", "model": "base"}) == "browser"


# --------------------------------------------------------------------------
# /api/stt/status
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_whispercpp_block(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stt_runtime, "DATA_DIR", tmp_path)
    resp = await client.get("/api/stt/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "whispercpp" in data
    cpp = data["whispercpp"]
    assert cpp["installed"] is False
    assert cpp["model_ready"] is False
    assert cpp["threads"] == stt.WHISPERCPP_THREADS
    assert cpp["reason"]
    assert data["engine"] == "auto"
    # Con el runtime ausente, auto resuelve a faster-whisper.
    assert data["engine_effective"] == "whisper"
    # STT-5: el default del toggle de traducción viene de la config.
    assert data["translate_english"] is False


@pytest.mark.asyncio
async def test_status_translate_flag_from_config(client, monkeypatch, tmp_path):
    import config as config_module

    monkeypatch.setattr(stt_runtime, "DATA_DIR", tmp_path)
    config_module.config.set("stt.translate_english", True)
    resp = await client.get("/api/stt/status")
    assert resp.status_code == 200
    assert resp.json()["translate_english"] is True


@pytest.mark.asyncio
async def test_status_reason_missing_vs_error_runtime(client, monkeypatch, tmp_path):
    # STT-2: sin directorio el estado es "missing" (hay que bajarlo); con el
    # directorio a medias es "error" con los archivos que faltan.
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt_runtime, "DATA_DIR", tmp_path)

    resp = await client.get("/api/stt/status")
    cpp = resp.json()["whispercpp"]
    assert cpp["runtime_state"] == "missing"
    assert "Descargar runtime" in cpp["reason"]

    stt_runtime.stt_runtime_dir().mkdir(parents=True, exist_ok=True)
    resp = await client.get("/api/stt/status")
    cpp = resp.json()["whispercpp"]
    assert cpp["runtime_state"] == "error"
    assert cpp["missing_files"]
    assert "Faltan" in cpp["reason"]


@pytest.mark.asyncio
async def test_status_effective_engine_browser_when_frozen(client, monkeypatch, tmp_path):
    # STT-1: en el bundle empaquetado, sin runtime, auto ⇒ browser.
    monkeypatch.setattr(stt, "FROZEN", True)
    monkeypatch.setattr(stt_runtime, "DATA_DIR", tmp_path)
    resp = await client.get("/api/stt/status")
    assert resp.json()["engine_effective"] == "browser"

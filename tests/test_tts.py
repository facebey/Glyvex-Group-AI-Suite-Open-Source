"""
test_tts.py — TTS SAPI con PowerShell fake (T3.4 de la fase 3).

`_run_powershell_sync` es el único punto de contacto con el mundo (SAPI vía
PowerShell), así que se reemplaza completo: los tests verifican qué texto se
escribió en el archivo de entrada (round-trip UTF-8), qué voz/rate se
pasaron, y qué devuelve cada endpoint — sin Windows real, sin SAPI y sin
procesos.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import config as config_module
import tts as tts_module

# Voces fake "instaladas": imita lo que trae un Windows por defecto —
# es-ES (Helena) y es-MX (Sabina), pero NO es-AR (el caso que la cadena de
# resolución existe para cubrir).
FAKE_VOICES = [
    {"name": "Microsoft David Desktop - English (United States)", "culture": "en-US"},
    {"name": "Microsoft Helena Desktop - Spanish (Spain)", "culture": "es-ES"},
    {"name": "Microsoft Sabina Desktop - Spanish (Mexico)", "culture": "es-MX"},
]

# WAV mínimo válido (44-byte header + 4 bytes de data) para las respuestas.
WAV_BYTES = (
    b"RIFF" + (104).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    + (22050).to_bytes(4, "little") + (44100).to_bytes(4, "little")
    + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
    + b"data" + (4).to_bytes(4, "little") + (0).to_bytes(4, "little")
)

SABINA = "Microsoft Sabina Desktop - Spanish (Mexico)"
HELENA = "Microsoft Helena Desktop - Spanish (Spain)"


@pytest.fixture
def fake_powershell(monkeypatch):
    """
    PowerShell fake de dos modos, distinguido por el contenido del script:
    - voices: escribe el JSON de FAKE_VOICES en el archivo indicado.
    - speak:  copia el texto de entrada a `state["last_text"]` (para verificar
              el round-trip UTF-8) y escribe WAV_BYTES en la ruta de salida.
    Deja en `state` las llamadas para inspeccionarlas desde los tests.
    """
    state = {"calls": [], "last_text": None, "voices": list(FAKE_VOICES)}

    def fake_run(script: Path, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        content = Path(script).read_text(encoding="utf-8")
        if "GetInstalledVoices" in content:
            state["calls"].append({"mode": "voices", "args": list(args), "timeout": timeout})
            Path(args[0]).write_text(json.dumps(state["voices"]), encoding="utf-8")
        else:
            state["calls"].append({"mode": "speak", "args": list(args), "timeout": timeout})
            state["last_text"] = Path(args[0]).read_text(encoding="utf-8")
            Path(args[1]).write_bytes(WAV_BYTES)
        return subprocess.CompletedProcess(args=[str(script)], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tts_module, "_run_powershell_sync", fake_run)
    monkeypatch.setattr(tts_module, "_is_windows", lambda: True)
    tts_module._reset_voices_cache()
    yield state
    tts_module._reset_voices_cache()


# ---------------------------------------------------------------------------
# _resolve_voice — cadena es-AR → es-MX → es-ES y voz preferida
# ---------------------------------------------------------------------------


def test_resolve_voice_prefiere_exacta_sin_casar_mayusculas():
    assert tts_module._resolve_voice("microsoft sabina desktop - spanish (mexico)", FAKE_VOICES) == SABINA


def test_resolve_voice_preferida_desconocida_da_none():
    assert tts_module._resolve_voice("Microsoft Zed - Spanish (Argentina)", FAKE_VOICES) is None


def test_resolve_voice_cadena_da_es_mx_si_no_hay_es_ar():
    assert tts_module._resolve_voice("", FAKE_VOICES) == SABINA


def test_resolve_voice_cadena_cae_a_es_es_si_no_hay_es_mx():
    voices = [v for v in FAKE_VOICES if v["culture"] != "es-MX"]
    assert tts_module._resolve_voice("", voices) == HELENA


def test_resolve_voice_cadena_es_ar_si_esta_instalada():
    voices = FAKE_VOICES + [
        {"name": "Microsoft Zed - Spanish (Argentina)", "culture": "es-AR"}
    ]
    assert tts_module._resolve_voice("", voices) == "Microsoft Zed - Spanish (Argentina)"


def test_resolve_voice_sin_voces_espanolas_da_none():
    assert tts_module._resolve_voice("", FAKE_VOICES[:1]) is None


# ---------------------------------------------------------------------------
# GET /api/tts/status
# ---------------------------------------------------------------------------


async def test_status_reporta_voces_y_voz_resuelta(client, fake_powershell):
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["platform_ok"] is True
    assert data["enabled"] is True
    assert data["reason"] is None
    assert [v["name"] for v in data["voices"]] == [v["name"] for v in FAKE_VOICES]
    assert data["voice"] == SABINA


async def test_status_respeta_voz_fijada_en_config(client, fake_powershell):
    config_module.config.set("tts.voice", HELENA)
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    assert res.json()["voice"] == HELENA


async def test_status_muestra_deshabilitado(client, fake_powershell):
    config_module.config.set("tts.enabled", False)
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    assert res.json()["enabled"] is False


async def test_status_reporta_fallo_al_listar_voces(client, monkeypatch):
    def boom(script, args, timeout):
        raise RuntimeError("System.Speech no disponible")

    monkeypatch.setattr(tts_module, "_run_powershell_sync", boom)
    monkeypatch.setattr(tts_module, "_is_windows", lambda: True)
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["platform_ok"] is False
    assert "System.Speech" in data["reason"]
    assert data["voices"] == []


async def test_status_no_windows(client, monkeypatch):
    monkeypatch.setattr(tts_module, "_is_windows", lambda: False)
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["platform_ok"] is False
    assert data["voice"] is None


# ---------------------------------------------------------------------------
# POST /api/tts/speak
# ---------------------------------------------------------------------------


async def test_speak_devuelve_wav_y_texto_utf8_intacto(client, fake_powershell):
    text = "Hola, mundo. ¿Cómo andás, Fabián? ¡Todo bien!"
    res = await client.post("/api/tts/speak", json={"text": text})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/wav")
    assert res.content == WAV_BYTES

    speak_calls = [c for c in fake_powershell["calls"] if c["mode"] == "speak"]
    assert len(speak_calls) == 1
    assert fake_powershell["last_text"] == text  # acentos por archivo, no codepage
    # Voz resuelta por la cadena (sin es-AR: Sabina) y rate 0 por defecto.
    assert speak_calls[0]["args"][2] == SABINA
    assert speak_calls[0]["args"][3] == "0"


async def test_speak_usa_voz_y_rate_del_body(client, fake_powershell):
    res = await client.post(
        "/api/tts/speak", json={"text": "hola", "voice": HELENA, "rate": 5}
    )
    assert res.status_code == 200
    speak_call = next(c for c in fake_powershell["calls"] if c["mode"] == "speak")
    assert speak_call["args"][2] == HELENA
    assert speak_call["args"][3] == "5"


async def test_speak_usa_voz_y_rate_de_config_si_el_body_no_trae(client, fake_powershell):
    config_module.config.set("tts.voice", HELENA)
    config_module.config.set("tts.rate", -2)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 200
    speak_call = next(c for c in fake_powershell["calls"] if c["mode"] == "speak")
    assert speak_call["args"][2] == HELENA
    assert speak_call["args"][3] == "-2"


async def test_speak_voz_pedida_desconocida_da_404(client, fake_powershell):
    res = await client.post("/api/tts/speak", json={"text": "hola", "voice": "Voz Fantasma"})
    assert res.status_code == 404


async def test_speak_deshabilitado_da_503(client, fake_powershell):
    config_module.config.set("tts.enabled", False)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 503
    # Nada de esto debe haber disparado PowerShell.
    assert fake_powershell["calls"] == []


async def test_speak_texto_solo_blancos_da_400(client, fake_powershell):
    res = await client.post("/api/tts/speak", json={"text": "   \n  "})
    assert res.status_code == 400


async def test_speak_texto_vacio_rechazado_por_schema(client, fake_powershell):
    res = await client.post("/api/tts/speak", json={"text": ""})
    assert res.status_code == 422


async def test_speak_rate_fuera_de_rango_rechazado(client, fake_powershell):
    res = await client.post("/api/tts/speak", json={"text": "hola", "rate": 11})
    assert res.status_code == 422


async def test_speak_timeout_da_504(client, monkeypatch):
    tts_module._reset_voices_cache()
    tts_module._voices_cache = list(FAKE_VOICES)  # la lista de voces sí funciona

    def boom(script, args, timeout):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=timeout)

    monkeypatch.setattr(tts_module, "_run_powershell_sync", boom)
    monkeypatch.setattr(tts_module, "_is_windows", lambda: True)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 504


async def test_speak_fallo_de_sapi_da_502(client, monkeypatch):
    tts_module._reset_voices_cache()
    tts_module._voices_cache = list(FAKE_VOICES)

    def speak_fails(script, args, timeout):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(tts_module, "_run_powershell_sync", speak_fails)
    monkeypatch.setattr(tts_module, "_is_windows", lambda: True)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 502


async def test_speak_fallo_al_listar_voces_da_502(client, monkeypatch):
    def boom(script, args, timeout):
        raise RuntimeError("no voices")

    monkeypatch.setattr(tts_module, "_run_powershell_sync", boom)
    monkeypatch.setattr(tts_module, "_is_windows", lambda: True)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 502


async def test_speak_no_windows_da_501(client, monkeypatch):
    monkeypatch.setattr(tts_module, "_is_windows", lambda: False)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 501


async def test_speak_reescuchar_el_mismo_texto_no_resintetiza(client, fake_powershell):
    await client.post("/api/tts/speak", json={"text": "hola de nuevo"})
    res = await client.post("/api/tts/speak", json={"text": "hola de nuevo"})
    assert res.status_code == 200
    assert res.content == WAV_BYTES
    speak_calls = [c for c in fake_powershell["calls"] if c["mode"] == "speak"]
    assert len(speak_calls) == 1  # la 2ª petición se sirve del cache de WAV


async def test_speak_cache_distingue_voz_y_rate(client, fake_powershell):
    await client.post("/api/tts/speak", json={"text": "hola ritmo", "rate": 1})
    await client.post("/api/tts/speak", json={"text": "hola ritmo", "rate": 2})
    await client.post("/api/tts/speak", json={"text": "hola ritmo", "voice": HELENA, "rate": 1})
    speak_calls = [c for c in fake_powershell["calls"] if c["mode"] == "speak"]
    assert len(speak_calls) == 3  # cada combinación distinta sintetiza su propia


async def test_voices_se_cachean_una_sola_vez(client, fake_powershell):
    await client.get("/api/tts/status")
    await client.get("/api/tts/status")
    await client.post("/api/tts/speak", json={"text": "hola"})
    voice_calls = [c for c in fake_powershell["calls"] if c["mode"] == "voices"]
    assert len(voice_calls) == 1


# ---------------------------------------------------------------------------
# Motor Piper (paquete simulado; nunca toca SAPI/PowerShell)
# ---------------------------------------------------------------------------
# El fixture _piper_engine_off de conftest deshabilita Piper por defecto en
# TODOS los tests; acá se reinyecta un stub (aplica después y pisa el del
# conftest). Sin onnxruntime, sin HuggingFace, sin PowerShell.

import types  # noqa: E402

import piper_runtime  # noqa: E402

PIPER_VOICE = "es_AR-daniela-high"


@pytest.fixture
def stub_piper(monkeypatch):
    """
    Paquete `piper` fake: PiperVoice.load() devuelve una voz que escribe 4
    bytes en el WAV y registra cada síntesis (texto + length_scale). Deja en
    `state` cuántas veces se cargó el modelo para verificar el cache.
    """
    state = {"loads": 0, "synths": []}

    class _Voice:
        def synthesize_wav(self, text, wav_file, syn_config=None):
            state["synths"].append(
                {"text": text, "length_scale": getattr(syn_config, "length_scale", None)}
            )
            # Contrato real de Piper (set_wav_format=True por defecto): el
            # motor fija canales/ancho/tasa antes de escribir los frames.
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00" * 4)

    fake_piper = types.ModuleType("piper")

    class PiperVoice:
        @staticmethod
        def load(onnx_path, json_path):
            state["loads"] += 1
            assert Path(onnx_path).is_file() and Path(json_path).is_file()
            return _Voice()

    class SynthesisConfig:
        def __init__(self, length_scale=None, **_kwargs):
            self.length_scale = length_scale

    fake_piper.PiperVoice = PiperVoice
    fake_piper.SynthesisConfig = SynthesisConfig
    monkeypatch.setattr(tts_module, "_import_piper_engine", lambda: fake_piper)
    return state


def _download_stub_voice(voice: str = PIPER_VOICE) -> None:
    paths = piper_runtime.piper_voice_paths(voice)
    paths["onnx"].parent.mkdir(parents=True, exist_ok=True)
    paths["onnx"].write_bytes(b"onnx" * 64)
    paths["json"].write_bytes(b"{}")


async def test_status_auto_con_piper_listo_activa_piper(client, stub_piper):
    _download_stub_voice()
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["engine"] == "auto"
    assert data["active_engine"] == "piper"
    assert data["platform_ok"] is True
    assert data["piper"]["voice"] == PIPER_VOICE
    assert data["piper"]["voice_state"] == "ready"


async def test_status_auto_sin_piper_cae_a_sapi(client, fake_powershell):
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["active_engine"] == "sapi"
    assert data["piper"]["voice_state"] == "missing"


async def test_status_engine_explicito_sapi_ignora_piper_listo(client, fake_powershell, stub_piper):
    config_module.config.set("tts.engine", "sapi")
    _download_stub_voice()
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["engine"] == "sapi"
    assert data["active_engine"] == "sapi"
    assert data["voice"] == SABINA  # la voz SAPI sigue reportándose


async def test_status_engine_explicito_piper_incluso_con_voz_faltante(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["active_engine"] == "piper"
    assert data["piper"]["voice_state"] == "missing"


async def test_speak_engine_piper_sintetiza_sin_tocar_sapi(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    _download_stub_voice()
    text = "Hola, mundo. ¿Cómo andás, Fabián?"
    res = await client.post("/api/tts/speak", json={"text": text})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/wav")
    assert res.content.startswith(b"RIFF")
    assert len(stub_piper["synths"]) == 1
    assert stub_piper["synths"][0]["text"] == text
    assert stub_piper["loads"] == 1  # el modelo se carga una sola vez


async def test_speak_piper_cachea_el_modelo_entre_sintesis(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    _download_stub_voice()
    await client.post("/api/tts/speak", json={"text": "uno"})
    await client.post("/api/tts/speak", json={"text": "dos"})
    assert stub_piper["loads"] == 1
    assert len(stub_piper["synths"]) == 2


async def test_speak_piper_reescuchar_el_mismo_texto_no_resintetiza(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    _download_stub_voice()
    res = await client.post("/api/tts/speak", json={"text": "hola piper"})
    res2 = await client.post("/api/tts/speak", json={"text": "hola piper"})
    assert res.status_code == 200 and res2.status_code == 200
    assert len(stub_piper["synths"]) == 1  # la 2ª se sirve del cache de WAV


async def test_speak_piper_rate_mapea_a_length_scale(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    _download_stub_voice()
    res = await client.post("/api/tts/speak", json={"text": "hola", "rate": 5})
    assert res.status_code == 200
    # 1.0 - 5 * 0.05 = 0.75 (más rate = más rápido = length_scale menor)
    assert stub_piper["synths"][0]["length_scale"] == pytest.approx(0.75)


async def test_speak_piper_rate_limite_superior(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    _download_stub_voice()
    res = await client.post("/api/tts/speak", json={"text": "hola", "rate": 10})
    assert res.status_code == 200
    # 1.0 - 10 * 0.05 = 0.5 (el borde inferior del rango de length_scale)
    assert stub_piper["synths"][0]["length_scale"] == pytest.approx(0.5)


async def test_speak_auto_preferencia_piper_cuando_esta_listo(client, stub_piper):
    _download_stub_voice()
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 200
    assert len(stub_piper["synths"]) == 1  # no pasó por SAPI


async def test_speak_engine_piper_voz_no_descargada_da_503(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 503


async def test_speak_engine_piper_voz_desconocida_da_404(client, stub_piper):
    config_module.config.set("tts.engine", "piper")
    _download_stub_voice()
    res = await client.post(
        "/api/tts/speak", json={"text": "hola", "piper_voice": "es_MX-ghost-medium"}
    )
    assert res.status_code == 404


async def test_speak_engine_piper_sin_paquete_da_503(client, monkeypatch):
    # Sin el stub (el fixture _piper_engine_off del conftest ya dejó a Piper
    # como None), /speak con engine piper debe dar 503, no romperse.
    config_module.config.set("tts.engine", "piper")
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 503


async def test_status_engine_valor_invalido_se_sanea_a_auto(client, fake_powershell):
    config_module.config.set("tts.engine", "zound")
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["engine"] == "auto"
    assert data["active_engine"] == "sapi"


# ---------------------------------------------------------------------------
# Motor Kokoro (runtime simulado; nunca toca SAPI/PowerShell/onnxruntime)
# ---------------------------------------------------------------------------
# El fixture _kokoro_engine_off de conftest deshabilita Kokoro por defecto en
# TODOS los tests; acá se reinyecta un stub (aplica después y pisa el del
# conftest). Sin onnxruntime, sin G2P y sin red.

import kokoro_runtime  # noqa: E402

KOKORO_VOICE = "ef_dora"


@pytest.fixture
def stub_kokoro(monkeypatch):
    """
    Motor Kokoro fake: se reporta disponible (pisando el _kokoro_engine_off
    de conftest) y registra cada síntesis (texto, voz y speed). Devuelve
    WAV_BYTES, igual que el SAPI fake.
    """
    state = {"synths": []}

    def fake_synthesize(text, voice_name, speed=1.0):
        # Mismo contrato real: ValueError si la voz es ajena al catálogo y
        # RuntimeError si el modelo no está descargado (los tests de /speak
        # dependen de ambos para los 404 y 503).
        if voice_name not in kokoro_runtime.KOKORO_VOICES:
            raise ValueError(f"Voz Kokoro desconocida: {voice_name}")
        if kokoro_runtime.kokoro_model_status()["state"] != "ready":
            raise RuntimeError("el modelo Kokoro no está descargado")
        state["synths"].append({"text": text, "voice": voice_name, "speed": speed})
        return WAV_BYTES

    monkeypatch.setattr(
        kokoro_runtime, "kokoro_engine_status",
        lambda: {"available": True, "detail": "kokoro-onnx fake"},
    )
    monkeypatch.setattr(kokoro_runtime, "kokoro_synthesize_wav", fake_synthesize)
    return state


def _download_stub_kokoro_model() -> None:
    paths = kokoro_runtime.kokoro_model_paths()
    paths["onnx"].parent.mkdir(parents=True, exist_ok=True)
    paths["onnx"].write_bytes(b"onnx" * 64)
    paths["bin"].write_bytes(b"voices")


async def test_status_auto_con_kokoro_listo_activa_kokoro(client, stub_kokoro, monkeypatch):
    # platform_ok refleja _is_windows(): en CI (Linux) forzarlo a Windows,
    # igual que el resto de tests de estado.
    monkeypatch.setattr(tts_module, "_is_windows", lambda: True)
    _download_stub_kokoro_model()
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["engine"] == "auto"
    assert data["active_engine"] == "kokoro"
    assert data["platform_ok"] is True
    assert data["kokoro"]["voice"] == KOKORO_VOICE
    assert data["kokoro"]["model_state"] == "ready"
    assert data["kokoro"]["engine"]["available"] is True


async def test_status_auto_kokoro_gana_sobre_piper_listo(client, stub_kokoro, stub_piper):
    _download_stub_kokoro_model()
    _download_stub_voice()
    res = await client.get("/api/tts/status")
    data = res.json()
    assert data["active_engine"] == "kokoro"  # prioridad Kokoro → Piper → SAPI


async def test_status_auto_kokoro_faltante_cae_a_piper(client, stub_piper):
    _download_stub_voice()
    res = await client.get("/api/tts/status")
    data = res.json()
    assert data["active_engine"] == "piper"
    assert data["kokoro"]["model_state"] == "missing"


async def test_status_engine_explicito_kokoro_incluso_con_modelo_faltante(client, stub_kokoro):
    config_module.config.set("tts.engine", "kokoro")
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["active_engine"] == "kokoro"
    assert data["kokoro"]["model_state"] == "missing"


async def test_status_kokoro_bloque_con_engine_no_disponible(client, fake_powershell):
    res = await client.get("/api/tts/status")
    assert res.status_code == 200
    data = res.json()
    assert data["kokoro"]["engine"]["available"] is False
    assert data["kokoro"]["model_state"] == "missing"
    assert data["active_engine"] == "sapi"


async def test_speak_engine_kokoro_sintetiza_sin_tocar_sapi(client, stub_kokoro, fake_powershell):
    config_module.config.set("tts.engine", "kokoro")
    _download_stub_kokoro_model()
    text = "Hola, mundo. ¿Cómo andás, Fabián? 1.250,50"
    res = await client.post("/api/tts/speak", json={"text": text})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/wav")
    assert res.content == WAV_BYTES
    assert len(stub_kokoro["synths"]) == 1
    assert stub_kokoro["synths"][0]["text"] == text
    assert stub_kokoro["synths"][0]["voice"] == KOKORO_VOICE
    # Nada de esto debe haber disparado PowerShell.
    assert [c for c in fake_powershell["calls"] if c["mode"] == "speak"] == []


async def test_speak_engine_kokoro_rate_mapea_a_speed(client, stub_kokoro):
    config_module.config.set("tts.engine", "kokoro")
    _download_stub_kokoro_model()
    res = await client.post("/api/tts/speak", json={"text": "hola", "rate": 5})
    assert res.status_code == 200
    assert stub_kokoro["synths"][0]["speed"] == pytest.approx(1.25)
    res = await client.post("/api/tts/speak", json={"text": "hola", "rate": -10})
    assert res.status_code == 200
    assert stub_kokoro["synths"][1]["speed"] == pytest.approx(0.5)


async def test_speak_engine_kokoro_voz_desconocida_da_404(client, stub_kokoro):
    config_module.config.set("tts.engine", "kokoro")
    _download_stub_kokoro_model()
    res = await client.post(
        "/api/tts/speak", json={"text": "hola", "kokoro_voice": "zz_ghost"}
    )
    assert res.status_code == 404


async def test_speak_engine_kokoro_voz_de_config_y_del_body(client, stub_kokoro):
    config_module.config.set("tts.engine", "kokoro")
    config_module.config.set("tts.kokoro_voice", "em_alex")
    _download_stub_kokoro_model()
    await client.post("/api/tts/speak", json={"text": "hola"})
    assert stub_kokoro["synths"][0]["voice"] == "em_alex"
    res = await client.post(
        "/api/tts/speak", json={"text": "hola", "kokoro_voice": "af_heart"}
    )
    assert res.status_code == 200
    assert stub_kokoro["synths"][1]["voice"] == "af_heart"


async def test_speak_engine_kokoro_modelo_no_descargado_da_503(client, stub_kokoro):
    config_module.config.set("tts.engine", "kokoro")
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 503


async def test_speak_engine_kokoro_sin_motor_da_503(client, monkeypatch):
    # Sin el stub (el fixture _kokoro_engine_off de conftest ya deshabilitó
    # Kokoro), /speak con engine kokoro debe dar 503, no romperse. El get del
    # modelo se parchea para no pagar el import real de kokoro_onnx.
    config_module.config.set("tts.engine", "kokoro")

    def sin_paquete():
        raise RuntimeError("el paquete kokoro-onnx no está disponible en este proceso")

    monkeypatch.setattr(kokoro_runtime, "get_kokoro_model", sin_paquete)
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 503


async def test_speak_auto_preferencia_kokoro_sobre_piper_y_sapi(client, stub_kokoro, stub_piper, fake_powershell):
    _download_stub_kokoro_model()
    _download_stub_voice()
    res = await client.post("/api/tts/speak", json={"text": "hola"})
    assert res.status_code == 200
    assert len(stub_kokoro["synths"]) == 1  # no pasó por Piper ni SAPI
    assert stub_piper["synths"] == []

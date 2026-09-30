"""
tts.py — Texto a voz para las respuestas del chat (fase 3 del plan Tauri).

Tres motores, 100% locales, elegibles en config (tts.engine):

- "sapi": SAPI (System.Speech) vía PowerShell, que viene con Windows — cero
  dependencias, cero descargas. El POC (T0.4) midió ~58 ms de latencia por
  frase de ~4 s y un WAV de ~250 KB, así que el audio se sintetiza por
  petición (no conviene cachearlo por tamaño).
- "piper": motor neuronal local (piper-tts + onnxruntime, importados del
  entorno; las voces .onnx/.json las gestiona piper_runtime). Devuelve WAV.
- "kokoro": motor neuronal local (kokoro-onnx + onnxruntime; el modelo
  .onnx compartido y voices.bin los gestiona kokoro_runtime). Devuelve WAV.
- "auto" (defecto): Kokoro si está listo (motor importable + modelo
  descargado), si no Piper listo (importable + voz descargada), si no SAPI.

La voz se resuelve en Python contra la lista real de voces instaladas:

  1. `tts.voice` en config.json si está fijado y existe en la máquina.
  2. Cadena es-AR → es-MX → es-ES: Windows no trae es-AR por defecto
     (tiene es-ES "Helena" y es-MX "Sabina"), así que la cadena existe para
     que la app "haga lo que toca" en cualquiera de las tres.
  3. Si nada coincide, la voz por defecto del sistema.

El texto se le pasa a PowerShell por archivo UTF-8 (no por argv ni stdin):
las comillas, acentos y saltos de línea del texto del chat rompen cualquier
otro transporte, y el stdout de PowerShell pasa por la codepage de la consola
(misma lección que el whisper-cli del STT: los artefactos van a archivo).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import platform
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from config import config
import piper_runtime
import kokoro_runtime

logger = logging.getLogger("glyvex.tts")

router = APIRouter()

# es-AR va primero aunque Windows no lo traiga: si el usuario lo instaló
# (Microsoft ha sumado voces con las actualizaciones), es la que quiere.
VOICE_CHAIN = ("es-AR", "es-MX", "es-ES")

# Engines disponibles para tts.engine.
TTS_ENGINES = ("auto", "sapi", "piper", "kokoro")

MAX_TEXT_CHARS = 3000
SYNTH_TIMEOUT_S = 30
MAX_WAV_BYTES = 20 * 1024 * 1024

# Script de detección de voces: escribe el JSON a archivo (no por stdout,
# que pasa por la codepage de la consola y corrompe los acentos).
_VOICES_SCRIPT = """param([string]$VoicesFile)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$list = @()
foreach ($v in $s.GetInstalledVoices()) {
  if (-not $v.Enabled) { continue }
  $list += [pscustomobject]@{ name = $v.VoiceInfo.Name; culture = $v.VoiceInfo.Culture.ToString() }
}
$list | ConvertTo-Json -Compress | Set-Content -Path $VoicesFile -Encoding UTF8 -NoNewline
exit 0
"""

# Sintetizador: lee el texto del archivo UTF-8, fija la voz (si se pasa) y
# la velocidad, y escribe el WAV. Si el WAV no queda, rc != 0.
_SPEAK_SCRIPT = """param([string]$TextFile, [string]$WavFile, [string]$Voice, [int]$Rate)
Add-Type -AssemblyName System.Speech
$tts = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { try { $tts.SelectVoice($Voice) } catch {} }
$tts.Rate = $Rate
$text = Get-Content -Raw -Encoding UTF8 -Path $TextFile
$tts.SetOutputToWaveFile($WavFile)
$tts.Speak($text)
$tts.Dispose()
if (-not (Test-Path $WavFile)) { exit 1 }
exit 0
"""


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    # "" = resolver por config (tts.voice) y la cadena es-AR→es-MX→es-ES.
    voice: str = ""
    # None = la de config (tts.rate). Rango SAPI: -10..10 (en Piper se
    # traduce a length_scale, en Kokoro a speed).
    rate: int | None = Field(default=None, ge=-10, le=10)
    # "" = la de config (tts.piper_voice). Solo engines auto/piper.
    piper_voice: str = ""
    # "" = la de config (tts.kokoro_voice). Solo engines auto/kokoro.
    kokoro_voice: str = ""


# ---------------------------------------------------------------------------
# PowerShell / SAPI
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _powershell_exe() -> str:
    # La variable de entorno existe para tests (y debugging): apunta a un
    # "powershell" fake que emule los modos del script.
    return os.environ.get("TTS_POWERSHELL") or "powershell"


def _run_powershell_sync(script: Path, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    creationflags = subprocess.CREATE_NO_WINDOW if _is_windows() else 0
    return subprocess.run(
        [_powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True,
        timeout=timeout,
        creationflags=creationflags,
    )


# Las voces rara vez cambian (una actualización de Windows o un install manual):
# se detectan una sola vez por proceso y se cachean.
_voices_cache: list[dict[str, str]] | None = None


def _reset_voices_cache() -> None:
    global _voices_cache
    _voices_cache = None


def _list_voices_sync() -> list[dict[str, str]]:
    global _voices_cache
    if _voices_cache is not None:
        return _voices_cache

    with tempfile.TemporaryDirectory(prefix="glyvex-tts-") as tmp:
        tmp_dir = Path(tmp)
        script = tmp_dir / "voices.ps1"
        script.write_text(_VOICES_SCRIPT, encoding="utf-8")
        voices_file = tmp_dir / "voices.json"
        proc = _run_powershell_sync(script, [str(voices_file)], 15)
        if proc.returncode != 0:
            raise RuntimeError(f"no se pudo listar las voces SAPI (rc={proc.returncode})")
        raw = voices_file.read_text(encoding="utf-8-sig")
        _voices_cache = json.loads(raw or "[]")
    return _voices_cache


def _resolve_voice(preferred: str, voices: list[dict[str, str]]) -> str | None:
    """Devuelve el nombre exacto de la voz a usar, o None = la del sistema."""
    if preferred:
        low = preferred.lower()
        for v in voices:
            if v["name"].lower() == low:
                return v["name"]
        return None
    for culture in VOICE_CHAIN:
        culture_low = culture.lower()
        for v in voices:
            if v["culture"].lower() == culture_low:
                return v["name"]
    return None


# ---------------------------------------------------------------------------
# Piper (motor neuronal local)
# ---------------------------------------------------------------------------


def _configured_engine() -> str:
    """Engine configurado (tts.engine), saneado a auto | sapi | piper | kokoro."""
    engine = str(config.get("tts.engine", "auto")).lower()
    return engine if engine in TTS_ENGINES else "auto"


def _import_piper_engine():
    """Importa el paquete piper (dev: venv; empaquetado: wheels del runtime)."""
    try:
        import piper

        return piper
    except ImportError:
        return None


def _configured_piper_voice() -> str:
    return str(config.get("tts.piper_voice", piper_runtime.PIPER_DEFAULT_VOICE))


def _piper_available(voice_name: str) -> tuple[bool, str | None]:
    """(disponible, detalle): paquete piper importable + voz descargada."""
    if _import_piper_engine() is None:
        return False, "el paquete piper no está disponible en este proceso"
    try:
        state = piper_runtime.piper_voice_status(voice_name)
    except ValueError:
        return False, f"voz Piper desconocida: {voice_name}"
    if state["state"] != "ready":
        return False, (
            f"la voz Piper {voice_name} no está descargada (estado: {state['state']})"
        )
    return True, None


# Cargar un modelo ONNX tarda unos segundos: se cachea por nombre de voz.
_piper_voices: dict[str, Any] = {}
_piper_voices_lock = threading.Lock()


def _get_piper_voice(voice_name: str, paths: dict[str, Path]) -> Any:
    piper = _import_piper_engine()
    if piper is None:
        raise RuntimeError("el paquete piper no está disponible en este proceso")
    with _piper_voices_lock:
        cached = _piper_voices.get(voice_name)
        if cached is not None:
            return cached
        cached = piper.PiperVoice.load(str(paths["onnx"]), str(paths["json"]))
        _piper_voices[voice_name] = cached
        return cached


def _piper_speak_sync(text: str, rate: int, voice_name: str) -> bytes:
    """Sintetiza el texto con Piper y devuelve los bytes del WAV."""
    piper = _import_piper_engine()
    if piper is None:
        raise RuntimeError("el paquete piper no está disponible en este proceso")
    paths = piper_runtime.piper_voice_paths(voice_name)  # ValueError si es ajena
    if not paths["onnx"].is_file() or not paths["json"].is_file():
        raise RuntimeError(f"la voz Piper {voice_name} no está descargada")
    piper_runtime.sanitize_voice_config(voice_name)
    voice = _get_piper_voice(voice_name, paths)
    length_scale = max(0.5, min(2.0, 1.0 - rate * 0.05))
    buffer = io.BytesIO()
    with wave.Wave_write(buffer) as wav_file:
        voice.synthesize_wav(
            text, wav_file, syn_config=piper.SynthesisConfig(length_scale=length_scale)
        )
    data = buffer.getvalue()
    if not data:
        raise RuntimeError("Piper no generó audio")
    return data


# ---------------------------------------------------------------------------
# Kokoro (motor neuronal local)
# ---------------------------------------------------------------------------


def _configured_kokoro_voice() -> str:
    return str(config.get("tts.kokoro_voice", kokoro_runtime.KOKORO_DEFAULT_VOICE))


def _kokoro_available(voice_name: str) -> tuple[bool, str | None]:
    """(disponible, detalle): voz del catálogo + motor importable + modelo listo."""
    if voice_name not in kokoro_runtime.KOKORO_VOICES:
        return False, f"voz Kokoro desconocida: {voice_name}"
    engine = kokoro_runtime.kokoro_engine_status()
    if not engine["available"]:
        return False, "el paquete kokoro-onnx no está disponible en este proceso"
    if kokoro_runtime.kokoro_model_status()["state"] != "ready":
        state = kokoro_runtime.kokoro_model_status()["state"]
        return False, f"el modelo Kokoro no está descargado (estado: {state})"
    return True, None


def _kokoro_speak_sync(text: str, rate: int, voice_name: str) -> bytes:
    """Sintetiza el texto con Kokoro y devuelve los bytes del WAV (24 kHz)."""
    speed = kokoro_runtime.rate_to_kokoro_speed(rate)
    return kokoro_runtime.kokoro_synthesize_wav(text, voice_name, speed)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def tts_status() -> dict[str, Any]:
    engine = _configured_engine()
    enabled = bool(config.get("tts.enabled", True))
    piper_voice_name = _configured_piper_voice()
    kokoro_voice_name = _configured_kokoro_voice()
    piper_ok, _piper_reason = await asyncio.to_thread(_piper_available, piper_voice_name)
    kokoro_ok, _kokoro_reason = await asyncio.to_thread(_kokoro_available, kokoro_voice_name)

    if engine == "auto":
        if kokoro_ok:
            active_engine = "kokoro"
        elif piper_ok:
            active_engine = "piper"
        else:
            active_engine = "sapi"
    else:
        active_engine = engine

    voice: str | None = None
    voices: list[dict[str, str]] = []
    if _is_windows():
        try:
            voices = await asyncio.to_thread(_list_voices_sync)
            voice = _resolve_voice(str(config.get("tts.voice", "")), voices)
        except Exception as exc:  # noqa: BLE001 — PowerShell roto, SAPI sin voces, etc.
            logger.error("fallo listando voces SAPI: %s", exc)
            if active_engine == "sapi":
                return {
                    "enabled": enabled,
                    "engine": engine,
                    "active_engine": active_engine,
                    "platform_ok": False,
                    "reason": str(exc),
                    "voice": None,
                    "voices": [],
                    "piper": await _piper_block(piper_voice_name),
                    "kokoro": await _kokoro_block(kokoro_voice_name),
                }

    if active_engine == "piper":
        platform_ok, reason = True, None
    elif not _is_windows():
        platform_ok, reason = False, "SAPI es Windows-only"
    else:
        platform_ok, reason = True, None

    return {
        "enabled": enabled,
        "engine": engine,
        "active_engine": active_engine,
        "platform_ok": platform_ok,
        "reason": reason,
        "voice": voice,
        "voices": voices,
        "piper": await _piper_block(piper_voice_name),
        "kokoro": await _kokoro_block(kokoro_voice_name),
    }


async def _piper_block(piper_voice_name: str) -> dict[str, Any]:
    """Bloque Piper para /status: motor, voz configurada y catálogo."""
    try:
        voice_state = piper_runtime.piper_voice_status(piper_voice_name)
    except ValueError:
        voice_state = {"name": piper_voice_name, "state": "unknown", "error": None, "size_mb": 0.0}
    return {
        "engine": await asyncio.to_thread(piper_runtime.piper_engine_status),
        "voice": piper_voice_name,
        "voice_state": voice_state["state"],
        "voices": await asyncio.to_thread(piper_runtime.piper_voices_status),
    }


async def _kokoro_block(kokoro_voice_name: str) -> dict[str, Any]:
    """Bloque Kokoro para /status: motor, voz configurada, modelo y catálogo."""
    model = await asyncio.to_thread(kokoro_runtime.kokoro_model_status)
    return {
        "engine": await asyncio.to_thread(kokoro_runtime.kokoro_engine_status),
        "voice": kokoro_voice_name,
        "model_state": model["state"],
        "download_mb": model["download_mb"],
        "voices": await asyncio.to_thread(kokoro_runtime.kokoro_voices_catalog),
    }


async def _speak_piper_async(text: str, rate: int, voice_name: str) -> bytes:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_piper_speak_sync, text, rate, voice_name),
            timeout=SYNTH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504, detail=f"La síntesis Piper tardó más de {SYNTH_TIMEOUT_S} s y se abortó."
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — ONNX roto, espeakbridge, etc.
        logger.error("Piper falló: %s", exc)
        raise HTTPException(status_code=502, detail="Piper no pudo generar el audio.") from exc


async def _speak_kokoro_async(text: str, rate: int, voice_name: str) -> bytes:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_kokoro_speak_sync, text, rate, voice_name),
            timeout=SYNTH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504, detail=f"La síntesis Kokoro tardó más de {SYNTH_TIMEOUT_S} s y se abortó."
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — ONNX roto, G2P espeak-ng, etc.
        logger.error("Kokoro falló: %s", exc)
        raise HTTPException(status_code=502, detail="Kokoro no pudo generar el audio.") from exc


@router.post("/speak")
async def tts_speak(req: SpeakRequest) -> Response:
    if not config.get("tts.enabled", True):
        raise HTTPException(status_code=503, detail="El TTS está deshabilitado en la configuración (tts.enabled).")

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="El texto a leer está vacío.")

    rate = req.rate if req.rate is not None else int(config.get("tts.rate", 0))
    engine = _configured_engine()
    piper_voice_name = req.piper_voice or _configured_piper_voice()
    kokoro_voice_name = req.kokoro_voice or _configured_kokoro_voice()

    # auto: Kokoro → Piper → SAPI (calidad primero); fijado: ese motor.
    if engine == "kokoro":
        use_kokoro = True
    elif engine == "auto":
        use_kokoro = (await asyncio.to_thread(_kokoro_available, kokoro_voice_name))[0]
    else:
        use_kokoro = False
    use_piper = engine == "piper" or (
        engine == "auto" and not use_kokoro
        and (await asyncio.to_thread(_piper_available, piper_voice_name))[0]
    )

    if use_kokoro:
        wav = await _speak_kokoro_async(text, rate, kokoro_voice_name)
        if len(wav) > MAX_WAV_BYTES:
            raise HTTPException(status_code=502, detail="El WAV generado supera el tamaño máximo permitido.")
        return Response(content=wav, media_type="audio/wav")

    if use_piper:
        wav = await _speak_piper_async(text, rate, piper_voice_name)
        if len(wav) > MAX_WAV_BYTES:
            raise HTTPException(status_code=502, detail="El WAV generado supera el tamaño máximo permitido.")
        return Response(content=wav, media_type="audio/wav")

    if not _is_windows():
        raise HTTPException(status_code=501, detail="El TTS (SAPI) es Windows-only.")

    try:
        voices = await asyncio.to_thread(_list_voices_sync)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo consultar las voces SAPI: {exc}") from exc

    voice_name = _resolve_voice(req.voice or str(config.get("tts.voice", "")), voices)
    if req.voice and not voice_name:
        raise HTTPException(status_code=404, detail=f"La voz «{req.voice}» no está instalada en este equipo.")

    with tempfile.TemporaryDirectory(prefix="glyvex-tts-") as tmp:
        tmp_dir = Path(tmp)
        script = tmp_dir / "speak.ps1"
        script.write_text(_SPEAK_SCRIPT, encoding="utf-8")
        text_file = tmp_dir / "text.txt"
        text_file.write_text(text, encoding="utf-8")
        wav_path = tmp_dir / "out.wav"

        try:
            proc = await asyncio.to_thread(
                _run_powershell_sync, script, [str(text_file), str(wav_path), voice_name or "", str(rate)], SYNTH_TIMEOUT_S
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail=f"La síntesis tardó más de {SYNTH_TIMEOUT_S} s y se abortó.")

        if proc.returncode != 0 or not wav_path.is_file():
            logger.error("SAPI falló (rc=%s): %s", proc.returncode, proc.stderr.decode(errors="replace")[:300])
            raise HTTPException(status_code=502, detail="SAPI no pudo generar el audio.")

        wav = wav_path.read_bytes()

    if len(wav) > MAX_WAV_BYTES:
        raise HTTPException(status_code=502, detail="El WAV generado supera el tamaño máximo permitido.")

    return Response(content=wav, media_type="audio/wav")

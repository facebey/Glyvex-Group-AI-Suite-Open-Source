"""
stt.py — Voz a texto para el chat (Bloque 3 / módulo M3).

Dos motores:

  browser   El navegador transcribe con la Web Speech API y el backend no se
            entera. Cero latencia de red hacia acá, cero dependencias — pero
            en Chrome el audio viaja a servidores de Google, así que NO es
            local, y la API no está disponible en el WebView de Tauri ni en
            Firefox.
  whisper   El navegador graba con MediaRecorder y manda el blob acá, que lo
            transcribe con faster-whisper. Totalmente local, funciona en
            cualquier contenedor web, pero necesita el paquete instalado y
            descarga los pesos del modelo la primera vez.

El motor se elige con `stt.engine` (o la variable de entorno STT_ENGINE):

  auto      (default) El frontend consulta /api/stt/status, mira si el
            navegador soporta SpeechRecognition y decide. Es lo que hace
            falta para que la misma build funcione en Chrome durante el
            desarrollo y dentro del bundle de Tauri, donde el motor del
            navegador no existe.
  browser   Fuerza Web Speech API.
  whisper   Fuerza faster-whisper.

faster-whisper NO está en requirements.txt. Arrastra CTranslate2 y descarga
pesos en el primer uso, así que empaquetarlo por defecto inflaría el bundle
de Tauri de ~10 MB a varios cientos. Va en requirements-optional.txt y el
import es lazy: si no está, /status lo informa y el frontend muestra el
motivo en el tooltip del micrófono en vez de fallar al apretar.

TTS (texto a voz) queda fuera de esta versión a propósito.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from config import config

router = APIRouter()

# Tamaño aproximado de cada modelo ya convertido a CTranslate2, para poder
# avisarle al usuario cuánto se va a descargar antes de que lo dispare.
WHISPER_MODEL_SIZES_MB = {
    "tiny": 75,
    "tiny.en": 75,
    "base": 145,
    "base.en": 145,
    "small": 484,
    "small.en": 484,
    "medium": 1530,
    "medium.en": 1530,
    "large-v2": 3090,
    "large-v3": 3090,
    "large-v3-turbo": 1620,
    "distil-large-v3": 1520,
}

MAX_AUDIO_BYTES = 25 * 1024 * 1024

# El modelo se carga una sola vez y se comparte. El lock evita que dos
# grabaciones simultáneas disparen dos descargas del mismo modelo.
_model: Any = None
_model_key: tuple[str, str, str] | None = None
_model_lock = asyncio.Lock()


class TranscriptionResponse(BaseModel):
    text: str
    language: str = ""
    duration_s: float = 0.0
    model: str = ""


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------


def _settings() -> dict[str, Any]:
    # La variable de entorno gana: sirve para probar un motor sin tocar
    # config.json.
    engine = (os.environ.get("STT_ENGINE") or config.get("stt.engine", "auto")).lower()
    if engine not in ("auto", "browser", "whisper"):
        engine = "auto"
    return {
        "engine": engine,
        "language": str(config.get("stt.language", "es-AR")),
        "model": str(config.get("stt.whisper_model", "base")),
        "device": str(config.get("stt.whisper_device", "auto")),
        "compute_type": str(config.get("stt.whisper_compute_type", "int8")),
        # "" = que Whisper detecte el idioma solo.
        "whisper_language": str(config.get("stt.whisper_language", "")),
    }


def _faster_whisper_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def _model_is_cached(model_name: str) -> bool:
    """
    ¿Los pesos ya están en el cache de Hugging Face?

    Sirve para avisar "la primera transcripción descarga N MB" en vez de
    dejar al usuario esperando sin explicación.
    """
    cache_root = Path(
        os.environ.get("HF_HUB_CACHE")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or (Path.home() / ".cache" / "huggingface" / "hub")
    )
    if not cache_root.is_dir():
        return False
    # faster-whisper publica los modelos bajo Systran/faster-whisper-<nombre>.
    needle = model_name.replace(".", "-").lower()
    for entry in cache_root.iterdir():
        name = entry.name.lower()
        if entry.is_dir() and "faster-whisper" in name and name.endswith(needle):
            return True
    return False


# ---------------------------------------------------------------------------
# Carga del modelo
# ---------------------------------------------------------------------------


def _load_model_sync(model_name: str, device: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=device, compute_type=compute_type)


async def _get_model() -> Any:
    """Devuelve el modelo cargado, cargándolo (y descargándolo) si hace falta."""
    global _model, _model_key

    settings = _settings()
    key = (settings["model"], settings["device"], settings["compute_type"])

    if _model is not None and _model_key == key:
        return _model

    async with _model_lock:
        # Otra corrutina pudo haberlo cargado mientras esperábamos el lock.
        if _model is not None and _model_key == key:
            return _model

        if not _faster_whisper_installed():
            raise HTTPException(
                status_code=501,
                detail=(
                    "faster-whisper no está instalado. Instalalo con "
                    "`pip install -r requirements-optional.txt` o usá el motor "
                    "`browser` en la configuración de voz."
                ),
            )

        try:
            model = await asyncio.to_thread(_load_model_sync, *key)
        except Exception as exc:  # noqa: BLE001 — descarga fallida, CUDA rota, etc.
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo cargar el modelo `{key[0]}`: {exc}",
            ) from exc

        _model = model
        _model_key = key
        return _model


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def stt_status() -> dict[str, Any]:
    """
    Qué motores hay disponibles. El frontend combina esto con lo que soporta
    el navegador para decidir si muestra el micrófono y en qué modo.
    """
    await config.load()
    settings = _settings()
    installed = _faster_whisper_installed()
    cached = _model_is_cached(settings["model"]) if installed else False

    return {
        "engine": settings["engine"],
        "language": settings["language"],
        "whisper": {
            "installed": installed,
            "model": settings["model"],
            "device": settings["device"],
            "compute_type": settings["compute_type"],
            "loaded": _model is not None,
            "cached": cached,
            "download_mb": WHISPER_MODEL_SIZES_MB.get(settings["model"]),
            "reason": None
            if installed
            else (
                "faster-whisper no está instalado. `pip install -r "
                "requirements-optional.txt` para habilitar la transcripción local."
            ),
        },
    }


@router.post("/warmup")
async def stt_warmup() -> dict[str, Any]:
    """
    Carga el modelo por adelantado.

    La primera transcripción, si no está en cache, se lleva la descarga
    entera. Es mejor que el usuario dispare eso a propósito desde un botón
    que descubrirlo esperando después de hablar.
    """
    await config.load()
    started = time.monotonic()
    await _get_model()
    return {
        "loaded": True,
        "model": _model_key[0] if _model_key else "",
        "elapsed_s": round(time.monotonic() - started, 1),
    }


def _transcribe_sync(
    model: Any, path: str, language: str
) -> tuple[str, str, float]:
    segments, info = model.transcribe(
        path,
        language=language or None,
        beam_size=5,
        vad_filter=True,
    )
    # segments es un generador perezoso: recién acá corre la inferencia.
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, getattr(info, "language", "") or "", float(getattr(info, "duration", 0.0))


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    audio: UploadFile = File(...),
    language: str = Form(""),
) -> TranscriptionResponse:
    """
    Transcribe el audio grabado por MediaRecorder.

    El navegador manda webm/opus. faster-whisper decodifica con PyAV, que
    trae sus propios decoders, así que no hace falta un ffmpeg instalado
    aparte en la máquina del usuario.
    """
    await config.load()
    settings = _settings()

    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="El audio llegó vacío.")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"El audio supera los {MAX_AUDIO_BYTES // (1024 * 1024)} MB.",
        )

    model = await _get_model()

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    tmp_path: str | None = None
    try:
        # delete=False porque en Windows no se puede reabrir un NamedTemporary
        # todavía abierto; se borra a mano en el finally.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        text, detected, duration = await asyncio.to_thread(
            _transcribe_sync, model, tmp_path, language or settings["whisper_language"]
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"No se pudo transcribir el audio: {exc}"
        ) from exc
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

    return TranscriptionResponse(
        text=text,
        language=detected,
        duration_s=round(duration, 2),
        model=settings["model"],
    )


def unload_model() -> None:
    """Libera el modelo. Lo llama el lifespan de main.py al cerrar."""
    global _model, _model_key
    _model = None
    _model_key = None


__all__ = ["router", "unload_model"]

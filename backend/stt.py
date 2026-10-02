"""
stt.py — Voz a texto para el chat (Bloque 3 / módulo M3).

Tres motores:

  browser     El navegador transcribe con la Web Speech API y el backend no
              se entera. Cero latencia de red hacia acá, cero dependencias —
              pero en Chrome el audio viaja a servidores de Google, así que
              NO es local, y la API no está disponible en el WebView de
              Tauri ni en Firefox.
  whispercpp  (D6, Tauri) El navegador graba y manda el audio acá, que lo
              pasa a whisper-cli (binario nativo descargado en el primer uso,
              ver stt_runtime.py). Totalmente local, sin pip, y es el único
              que funciona dentro del bundle de Tauri sin PyInstaller.
  whisper     El navegador graba con MediaRecorder y manda el blob acá, que
              lo transcribe con faster-whisper. Totalmente local, funciona en
              cualquier contenedor web, pero necesita el paquete instalado y
              descarga los pesos del modelo la primera vez.

El motor se elige con `stt.engine` (o la variable de entorno STT_ENGINE):

  auto        (default) Elige whispercpp si el runtime está listo, si no
            faster-whisper, si no el navegador. Es lo que hace falta para que
            la misma build funcione en Chrome durante el desarrollo y dentro
            del bundle de Tauri, donde el motor del navegador no existe.
  browser     Fuerza Web Speech API.
  whispercpp  Fuerza el binario nativo.
  whisper     Fuerza faster-whisper.

faster-whisper NO está en requirements.txt. Arrastra CTranslate2 y descarga
pesos en el primer uso, así que empaquetarlo por defecto inflaría el bundle
de Tauri de ~10 MB a varios cientos. Va en requirements-optional.txt y el
import es lazy: si no está, /status lo informa y el frontend muestra el
motivo en el tooltip del micrófono en vez de fallar al apretar.

El texto a voz (respuestas del chat) vive en tts.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from config import config
from paths import FROZEN
import stt_runtime

logger = logging.getLogger("glyvex.stt")

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

# whisper.cpp como binario nativo (decisión D6, POC T2.1): 8 threads era el
# punto dulce medido (4t=2.6 s, 8t=1.9 s, 16t=1.9 s para 17 s de audio).
WHISPERCPP_THREADS = 8
# Tope duro por transcripción: el binario no tiene flag de timeout, así que
# si algo se cuelga (modelo corrupto, CPU saturada) se mata el proceso.
WHISPERCPP_TIMEOUT_S = 180


def _whispercpp_binary() -> Path | None:
    """Ruta de whisper-cli si el runtime STT está instalado y probeado."""
    if stt_runtime.stt_runtime_status()["state"] != "ready":
        return None
    return stt_runtime.stt_runtime_dir() / stt_runtime.STT_BINARY_NAME


def _whispercpp_model_path(model_name: str) -> Path | None:
    """Ruta del modelo .bin si existe y es de un modelo del catálogo STT."""
    if model_name not in stt_runtime.STT_MODELS:
        return None
    path = stt_runtime.stt_model_path(model_name)
    return path if path.is_file() else None


def _app_language() -> str:
    """Código corto del idioma de la app ('es-AR' → 'es'). Es el default
    cuando no se elige idioma: el auto-detect nativo de whisper.cpp (modelo
    base) sesga a inglés y el motor del navegador ya defaultea a es-AR."""
    return str(config.get("app.language", "es")).split("-")[0]


def _stt_language(settings: dict, override: str | None = None) -> str:
    """Idioma pedido para transcribir (todos los motores): override de la
    petición, luego `stt.whisper_language` (override legado M3), después
    `stt.language` y, si todo queda vacío, el idioma de la app: sin `-l`,
    whisper.cpp detecta solo y con el modelo base sesga a inglés aunque el
    audio sea español (mismo default que el motor del navegador)."""
    return (
        override
        or settings.get("whisper_language")
        or settings.get("language")
        or _app_language()
    )


def _faster_whisper_language(settings: dict, override: str | None = None) -> str:
    """Misma jerarquía que `_stt_language`, pero con el sufijo regional
    cortado: faster-whisper solo acepta códigos de 2 letras ("es-AR" → "es").
    Solo 'auto' explícito → "" (detección); el vacío ya resuelve al idioma de
    la app en `_stt_language`."""
    lang = _stt_language(settings, override).strip()
    if not lang or lang.lower() == "auto":
        return ""
    return lang.split("-")[0]


def _transcribe_whispercpp_sync(
    binary: Path,
    model_path: Path,
    wav_path: str,
    out_prefix: str,
    threads: int,
    language: str,
    translate: bool = False,
) -> tuple[str, str, float]:
    """
    Corre whisper-cli contra un WAV (16 kHz mono) y devuelve
    (texto, idioma_detectado, duracion_s).

    `-np -nt` limpia la salida de stdout y `-oj -of` escribe el JSON en
    `<out_prefix>.json`, que es donde se lee el texto de verdad: el stdout
    del binario pasa por la codepage de la consola de Windows y las tildes
    salen corruptas, el JSON sí es UTF-8 puro.
    """
    cmd = [
        str(binary),
        "-m", str(model_path),
        "-f", wav_path,
        "-t", str(threads),
        "-np", "-nt",
        "-oj", "-of", out_prefix,
    ]
    # whisper-cli solo acepta códigos de idioma de 2 letras; "es-AR" → "es".
    # "" o "auto" se omiten: el binario detecta solo.
    lang_code = (language or "").strip().split("-")[0].lower()
    if translate:
        # --translate traduce a inglés. El origen normalmente se detecta solo,
        # pero con el modelo base la detección sesga a inglés y la traducción
        # queda en no-op (vuelve el texto original). Si hay origen conocido
        # (override o default de la app) se ancla con -l para que sea fiable.
        cmd += ["--translate"]
        if lang_code and lang_code != "auto":
            cmd += ["-l", lang_code]
    elif lang_code and lang_code != "auto":
        cmd += ["-l", lang_code]

    kwargs: dict[str, Any] = {}
    if platform.system() == "Windows":
        # Sin CREATE_NO_WINDOW, cada transcripción abre una consola visible.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=WHISPERCPP_TIMEOUT_S,
        **kwargs,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"whisper-cli terminó con {proc.returncode}: {tail}")

    out_file = Path(out_prefix + ".json")
    if not out_file.is_file():
        raise RuntimeError("whisper-cli no produjo el JSON de salida")

    data = json.loads(out_file.read_text(encoding="utf-8"))
    segments = data.get("transcription") or []
    text = " ".join(str(seg.get("text", "")).strip() for seg in segments).strip()
    detected = str((data.get("result") or {}).get("language") or "")
    duration_ms = max(
        (int(seg.get("offsets", {}).get("to", 0)) for seg in segments),
        default=0,
    )
    return text, detected, duration_ms / 1000.0

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
    if engine not in ("auto", "browser", "whispercpp", "whisper"):
        engine = "auto"
    return {
        "engine": engine,
        "language": str(config.get("stt.language", "es-AR")),
        "model": str(config.get("stt.whisper_model", "base")),
        "device": str(config.get("stt.whisper_device", "auto")),
        "compute_type": str(config.get("stt.whisper_compute_type", "int8")),
        # "" = que Whisper detecte el idioma solo.
        "whisper_language": str(config.get("stt.whisper_language", "")),
        "translate_english": bool(config.get("stt.translate_english", False)),
        "whispercpp_threads": WHISPERCPP_THREADS,
    }


def _effective_engine(settings: dict[str, Any]) -> str:
    """
    `auto` resuelve a whispercpp si el runtime está completo (binario probeado
    + modelo presente), si no a faster-whisper. En el bundle empaquetado
    faster-whisper nunca existe, así que el fallback es `browser`: el dictado
    no debe morir con 501 solo porque falta el runtime (STT-1). `browser` se
    resuelve en el frontend: este backend nunca transcribe en modo browser.
    """
    engine = settings["engine"]
    if engine != "auto":
        return engine
    binary = _whispercpp_binary()
    model = _whispercpp_model_path(settings["model"]) if binary else None
    if binary and model:
        return "whispercpp"
    return "browser" if FROZEN else "whisper"


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
            started = time.monotonic()
            model = await asyncio.to_thread(_load_model_sync, *key)
        except Exception as exc:  # noqa: BLE001 — descarga fallida, CUDA rota, etc.
            logger.error("fallo cargando modelo STT %s: %s", key[0], exc)
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo cargar el modelo `{key[0]}`: {exc}",
            ) from exc

        _model = model
        _model_key = key
        logger.info(
            "modelo STT cargado: model=%s device=%s compute_type=%s duracion_s=%.1f",
            key[0], key[1], key[2], time.monotonic() - started,
        )
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
    settings = _settings()
    installed = _faster_whisper_installed()
    cached = _model_is_cached(settings["model"]) if installed else False

    runtime = stt_runtime.stt_runtime_status()
    binary = _whispercpp_binary()
    model_path = _whispercpp_model_path(settings["model"])
    if runtime["state"] == "ready" and model_path is None:
        cpp_reason = (
            f"El modelo `{settings['model']}` no está disponible para "
            "whisper.cpp. Modelos: "
            + ", ".join(sorted(stt_runtime.STT_MODELS))
            + "."
        )
    elif runtime["state"] == "missing":
        # STT-2: sin directorio no hay nada roto — hay que descargarlo, y el
        # botón "Descargar runtime" de esta misma sección lo hace.
        cpp_reason = (
            "El runtime de whisper.cpp no está descargado: usá el botón "
            "«Descargar runtime» de esta sección o la pantalla de Provisión."
        )
    elif runtime["state"] == "downloading":
        cpp_reason = "Descargando el runtime de whisper.cpp…"
    elif runtime["state"] == "unsupported":
        cpp_reason = "whisper.cpp requiere Windows; usá otro motor."
    elif runtime["state"] == "error":
        missing = runtime.get("missing_files") or []
        cause = runtime.get("error") or "El runtime de whisper.cpp tiene un error."
        cpp_reason = (
            cause
            + (f" Faltan: {', '.join(missing)}." if missing else "")
            + " Usá «Descargar runtime» para reintentar."
        )
    else:
        cpp_reason = None

    return {
        "engine": settings["engine"],
        "engine_effective": _effective_engine(settings),
        # App empaquetada (PyInstaller): faster-whisper no puede instalarse
        # ahí, y la UI lo usa para no ofrecerlo como motor ni mostrar su
        # estado como advertencia.
        "packaged": FROZEN,
        "language": settings["language"],
        # Default del toggle de traducción del chat (stt.translate_english).
        "translate_english": settings["translate_english"],
        "whispercpp": {
            "installed": runtime["state"] == "ready",
            # STT-2: el estado fino del runtime para que la UI distinga
            # "bajar" (missing) de "roto" (error con archivos faltantes).
            "runtime_state": runtime["state"],
            "missing_files": runtime.get("missing_files") or [],
            "model": settings["model"],
            "model_ready": model_path is not None,
            "threads": settings["whispercpp_threads"],
            "reason": cpp_reason,
        },
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
                # En el bundle no se empaqueta faster-whisper (dependencia
                # opcional): el motor local es whisper.cpp.
                "faster-whisper no viene en la app empaquetada; usá el motor "
                "whisper.cpp. En desarrollo: `pip install -r "
                "requirements-optional.txt`."
                if FROZEN
                else "faster-whisper no está instalado. `pip install -r "
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
    started = time.monotonic()
    await _get_model()
    return {
        "loaded": True,
        "model": _model_key[0] if _model_key else "",
        "elapsed_s": round(time.monotonic() - started, 1),
    }


def _transcribe_sync(
    model: Any, path: str, language: str, translate: bool = False
) -> tuple[str, str, float]:
    segments, info = model.transcribe(
        path,
        # translate: el origen se detecta solo y la salida siempre es inglés.
        language=None if translate else (language or None),
        task="translate" if translate else "transcribe",
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
    translate: bool = Form(False),
) -> TranscriptionResponse:
    """
    Transcribe el audio grabado por MediaRecorder.

    El navegador manda webm/opus a faster-whisper (decodifica con PyAV, que
    trae sus propios decoders: no hace falta ffmpeg aparte) o WAV 16 kHz mono
    a whisper-cli. Con `translate` los motores locales detectan el idioma de
    origen y transcriben directo a inglés (whisper.cpp `--translate` /
    faster-whisper `task="translate"`); el `language` queda ignorado.
    """
    settings = _settings()
    engine = _effective_engine(settings)
    if engine == "browser":
        # El frontend nunca manda audio en modo browser; si llega, cae al
        # motor local de antes en vez de explotar.
        engine = "whisper"

    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="El audio llegó vacío.")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"El audio supera los {MAX_AUDIO_BYTES // (1024 * 1024)} MB.",
        )

    started = time.monotonic()
    logger.info("transcripcion inicio: engine=%s bytes=%d model=%s", engine, len(raw), settings["model"])

    if engine == "whispercpp":
        binary = _whispercpp_binary()
        model_path = _whispercpp_model_path(settings["model"])
        if binary is None or model_path is None:
            raise HTTPException(
                status_code=501,
                detail=(
                    "El runtime de whisper.cpp no está listo: bajalo desde "
                    "Configuración → Voz con el botón «Descargar runtime» "
                    "(o /api/stt/runtime/download)."
                ),
            )
        suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
        tmp_path: str | None = None
        out_prefix: str | None = None
        try:
            # delete=False porque en Windows no se puede reabrir un
            # NamedTemporary todavía abierto; se borra a mano en el finally.
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            out_prefix = f"{tmp_path}.stt"

            text, detected, duration = await asyncio.to_thread(
                _transcribe_whispercpp_sync,
                binary,
                model_path,
                tmp_path,
                out_prefix,
                settings["whispercpp_threads"],
                _stt_language(settings, language),
                translate,
            )
        except HTTPException:
            raise
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504,
                detail=f"La transcripción superó los {WHISPERCPP_TIMEOUT_S} s.",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"No se pudo transcribir el audio: {exc}"
            ) from exc
        finally:
            for path in (tmp_path, out_prefix):
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                        Path(path + ".json").unlink(missing_ok=True)
                    except OSError:
                        pass
    else:
        model = await _get_model()
        suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name

            text, detected, duration = await asyncio.to_thread(
                _transcribe_sync, model, tmp_path, _faster_whisper_language(settings, language),
                translate,
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

    logger.info(
        "transcripcion fin: engine=%s chars=%d lang=%s translate=%s audio_s=%.1f duracion_s=%.1f",
        engine, len(text), detected, translate, duration, time.monotonic() - started,
    )

    return TranscriptionResponse(
        text=text,
        language=detected,
        duration_s=round(duration, 2),
        model=settings["model"],
    )


def unload_model() -> None:
    """Libera el modelo. Lo llama el lifespan de main.py al cerrar."""
    global _model, _model_key
    if _model is not None:
        logger.info("modelo STT descargado: %s", _model_key[0] if _model_key else "")
    _model = None
    _model_key = None


__all__ = ["router", "unload_model"]

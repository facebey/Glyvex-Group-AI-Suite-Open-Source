"""
kokoro_runtime.py — Motor TTS neural Kokoro gestionado por la suite (TTS-2).

`kokoro-onnx` (motor Kokoro-82M en ONNX + G2P espeak-ng) se importa del
entorno Python (dev: kokoro-onnx + onnxruntime + num2words en el venv). En la
fase de empaquetado (sidecar congelado) se provisionarán wheels a
<DATA_DIR>/runtime/tts/ — pendiente junto con piper_tts.

El modelo (kokoro-v1.0.onnx, ~310 MB) y las voces (voices-v1.0.bin, ~27 MB,
bin completo con las 54 voces) los descarga la suite desde el release público
`model-files-v1.1` del repo thewh1teagle/kokoro-onnx, con sha256 y tamaño
verificados, a <runtime tts>/kokoro/.

Dos defectos del paquete que la suite corrige en local:

- speed: kokoro-onnx 0.4.7 envía el input `speed` como int32 en los exports
  recientes ("input_ids"), pero el export model-files-v1.1 declara
  tensor(float) → ONNXRuntime falla con INVALID_ARGUMENT.
  `apply_kokoro_speed_patch()` parchea `Kokoro._create_audio` para usar el
  dtype que el modelo declara (idempotente, una vez por proceso).
- números: el G2P de Kokoro lee "1.250,50" dígito a dígito y "0.6.3" como
  "cero seis tres". `normalize_numbers()` convierte los números a palabras
  (num2words) antes de la fonematización.

Mismo patrón que piper_runtime.py: descarga por etapas vía
runtime._download_stage, sha256 por archivo y estado en meta local.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import runtime
from paths import DATA_DIR

logger = logging.getLogger("glyvex.kokoro_runtime")

__all__ = [
    "KOKORO_DEFAULT_VOICE",
    "KOKORO_MODEL",
    "KOKORO_SAMPLE_RATE",
    "KOKORO_SPEED_MAX",
    "KOKORO_SPEED_MIN",
    "KOKORO_VOICES",
    "apply_kokoro_speed_patch",
    "delete_kokoro_model",
    "download_kokoro_model",
    "get_kokoro_model",
    "kokoro_engine_status",
    "kokoro_model_paths",
    "kokoro_model_status",
    "kokoro_runtime_status",
    "kokoro_synthesize_wav",
    "kokoro_voices_dir",
    "normalize_numbers",
    "reset_kokoro_model_cache",
    "rate_to_kokoro_speed",
]

# Fuente: release público de GitHub (sin auth; los repos HuggingFace con el
# ONNX están gateados y exigen token). sha256 y tamaños verificados con las
# fichas descargadas (TTS-2, 2026-09).
_KOKORO_RELEASE_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
)

KOKORO_MODEL: dict = {
    "filename": "kokoro-v1.0.onnx",
    "url": _KOKORO_RELEASE_BASE + "kokoro-v1.0.onnx",
    "sha256": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    "size": 325505369,
    "voices_filename": "voices-v1.0.bin",
    "voices_url": _KOKORO_RELEASE_BASE + "voices-v1.0.bin",
    "voices_sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    "voices_size": 28214398,
    "voices_count": 54,
}

# Voces recomendadas del bin completo (54 disponibles en el .bin). `lang` es
# el valor que pasa Kokoro.create al G2P: "es" para español, "en-us" inglés.
KOKORO_VOICES: dict[str, dict] = {
    "ef_dora": {"label": "Dora · Español (F)", "lang": "es"},
    "em_alex": {"label": "Alex · Español (M)", "lang": "es"},
    "em_santa": {"label": "Santa · Español (M)", "lang": "es"},
    "af_heart": {"label": "Heart · Inglés (F)", "lang": "en-us"},
    "am_fenrir": {"label": "Fenrir · Inglés (M)", "lang": "en-us"},
}

KOKORO_DEFAULT_VOICE = "ef_dora"
KOKORO_SAMPLE_RATE = 24000

# Rango que Kokoro admite en create() (assert del paquete).
KOKORO_SPEED_MIN = 0.5
KOKORO_SPEED_MAX = 2.0

MODEL_META_FILE = ".kokoro-model-meta.json"

_download_lock = threading.Lock()
_downloading = False

_model_cache: dict[str, object] = {}
_model_lock = threading.Lock()
_speed_patch_applied = False


def kokoro_voices_dir() -> Path:
    """Directorio del modelo Kokoro: <DATA_DIR>/runtime/tts/kokoro/."""
    return DATA_DIR / "runtime" / "tts" / "kokoro"


def kokoro_model_paths() -> dict[str, Path]:
    base = kokoro_voices_dir()
    return {
        "onnx": base / KOKORO_MODEL["filename"],
        "bin": base / KOKORO_MODEL["voices_filename"],
    }


def _read_meta() -> dict:
    try:
        raw = (kokoro_voices_dir() / MODEL_META_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_meta(meta: dict) -> None:
    kokoro_voices_dir().mkdir(parents=True, exist_ok=True)
    (kokoro_voices_dir() / MODEL_META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _record_model_state(state: str, error: str | None = None) -> None:
    _write_meta({"model": {"state": state, "error": error}})


def kokoro_model_status() -> dict:
    """Estado del modelo (onnx + bin): ready | downloading | missing | error."""
    paths = kokoro_model_paths()
    base = kokoro_voices_dir()
    onnx_ready = base.is_dir() and paths["onnx"].is_file()
    bin_ready = base.is_dir() and paths["bin"].is_file()
    meta = _read_meta().get("model", {})
    if not isinstance(meta, dict):
        meta = {}
    if _downloading or meta.get("state") == "downloading":
        state = "downloading"
    elif onnx_ready and bin_ready:
        state = "ready"
    elif meta.get("state") == "error":
        state = "error"
    else:
        state = "missing"
    size_mb = 0.0
    for p in (paths["onnx"], paths["bin"]):
        if p.is_file():
            size_mb += p.stat().st_size / (1024 * 1024)
    return {
        "state": state,
        "error": meta.get("error") if state == "error" else None,
        "size_mb": round(size_mb, 1),
        "download_mb": round(
            (KOKORO_MODEL["size"] + KOKORO_MODEL["voices_size"]) / (1024 * 1024), 1
        ),
        "voices_included": KOKORO_MODEL["voices_count"],
    }


def kokoro_engine_status() -> dict:
    """
    Estado del paquete `kokoro-onnx` en el proceso actual (importable o no).

    Dev: kokoro-onnx + onnxruntime + num2words en el venv. Empaquetado
    (pendiente): wheels provisionados en <DATA_DIR>/runtime/tts/.
    """
    try:
        import kokoro_onnx  # noqa: F401
        import num2words  # noqa: F401

        from importlib.metadata import version as _pkg_version

        detail = f"kokoro-onnx {_pkg_version('kokoro-onnx')}"
        return {"available": True, "detail": detail}
    except ImportError as exc:
        return {"available": False, "detail": f"kokoro-onnx no importable: {exc}"}


def kokoro_voices_catalog() -> list[dict]:
    """Catálogo de voces del bin (las 54 viven en el mismo archivo)."""
    return [
        {"name": name, "label": spec["label"], "lang": spec["lang"]}
        for name, spec in KOKORO_VOICES.items()
    ]


def kokoro_runtime_status() -> dict:
    """Estado global para la UI: motor, modelo y voces."""
    return {
        "engine": kokoro_engine_status(),
        "default_voice": KOKORO_DEFAULT_VOICE,
        "can_download": True,
        "model": kokoro_model_status(),
        "voices": kokoro_voices_catalog(),
    }


def _move_into(src: Path, base: Path, dest_name: str) -> Path:
    dest = base / dest_name
    if src.resolve() != dest.resolve():
        dest.unlink(missing_ok=True)
        shutil.move(str(src), str(dest))
    else:
        src.unlink(missing_ok=True)
    return dest


async def download_kokoro_model(on_progress: Callable[[float, str], None]) -> dict:
    """
    Descarga modelo + bin de voces con sha256 y los mueve a kokoro/.

    on_progress(pct_global_0_100, detail) por cada chunk (mismo contrato que
    piper_runtime.download_piper_voice). Devuelve el estado final del modelo.
    """
    global _downloading
    with _download_lock:
        if _downloading:
            raise RuntimeError("Ya hay una descarga del modelo Kokoro en curso")
        base = kokoro_voices_dir()
        base.mkdir(parents=True, exist_ok=True)
        work = DATA_DIR / "runtime" / "tts" / ".kokoro-download"
        work.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "name": KOKORO_MODEL["filename"],
                "url": KOKORO_MODEL["url"],
                "sha256": KOKORO_MODEL["sha256"],
                "size": KOKORO_MODEL["size"],
            },
            {
                "name": KOKORO_MODEL["voices_filename"],
                "url": KOKORO_MODEL["voices_url"],
                "sha256": KOKORO_MODEL["voices_sha256"],
                "size": KOKORO_MODEL["voices_size"],
            },
        ]
        _downloading = True
        _record_model_state("downloading")
        try:
            await runtime._download_stage(entries, work, on_progress, 0.0, 100.0)
            for entry in entries:
                _move_into(work / entry["name"], base, entry["name"])
            _record_model_state("ready", None)
            shutil.rmtree(work, ignore_errors=True)
        except Exception as exc:
            _record_model_state("error", str(exc))
            raise
        finally:
            _downloading = False
    return kokoro_model_status()


def delete_kokoro_model() -> None:
    """Elimina modelo + bin (vuelve a missing) y descarta la caché del modelo."""
    paths = kokoro_model_paths()
    base = kokoro_voices_dir()
    for p in (paths["onnx"], paths["bin"]):
        if base.is_dir() and p.is_file():
            p.unlink()
    meta = _read_meta()
    meta.pop("model", None)
    _write_meta(meta)
    with _model_lock:
        _model_cache.clear()


# ---------------------------------------------------------------------------
# Parche de speed (kokoro-onnx 0.4.7: int32 vs tensor(float) del export)
# ---------------------------------------------------------------------------


def _speed_input_dtype(sess) -> type:
    for i in sess.get_inputs():
        if i.name == "speed":
            t = i.type or ""
            if "int32" in t:
                return np.int32
            if "int64" in t:
                return np.int64
            if "float16" in t:
                return np.float16
            return np.float32
    return np.float32


def apply_kokoro_speed_patch() -> None:
    """
    Sustituye Kokoro._create_audio por una copia que envía `speed` con el
    dtype que el modelo declara en su graph (el 0.4.7 manda int32 a ciegas y
    el export model-files-v1.1 espera float → INVALID_ARGUMENT).

    Idempotente: se aplica una vez por proceso. El resto de la función es
    idéntica al original de kokoro-onnx 0.4.7.
    """
    global _speed_patch_applied
    if _speed_patch_applied:
        return
    from kokoro_onnx import Kokoro
    from kokoro_onnx.config import MAX_PHONEME_LENGTH, SAMPLE_RATE

    def _patched_create_audio(self, phonemes: str, voice, speed: float):
        if len(phonemes) > MAX_PHONEME_LENGTH:
            phonemes = phonemes[:MAX_PHONEME_LENGTH]
        start_t = time.time()
        tokens = np.array(self.tokenizer.tokenize(phonemes), dtype=np.int64)
        voice = voice[len(tokens)]
        tokens = [[0, *tokens, 0]]
        if "input_ids" in [i.name for i in self.sess.get_inputs()]:
            inputs = {
                "input_ids": tokens,
                "style": np.array(voice, dtype=np.float32),
                "speed": np.array([speed], dtype=_speed_input_dtype(self.sess)),
            }
        else:
            inputs = {
                "tokens": tokens,
                "style": voice,
                "speed": np.ones(1, dtype=np.float32) * speed,
            }
        audio = self.sess.run(None, inputs)[0]
        audio_duration = len(audio) / SAMPLE_RATE
        create_duration = time.time() - start_t
        logger.debug(
            "kokoro: %s s de audio en %s s (RTF: %.2f)",
            audio_duration,
            create_duration,
            create_duration / max(audio_duration, 1e-6),
        )
        return audio, SAMPLE_RATE

    Kokoro._create_audio = _patched_create_audio
    _speed_patch_applied = True
    logger.debug("parche de speed de kokoro-onnx aplicado")


# ---------------------------------------------------------------------------
# Carga del modelo (cacheada: cargar el ONNX tarda unos segundos)
# ---------------------------------------------------------------------------


def _kokoro_files_ready() -> bool:
    paths = kokoro_model_paths()
    return paths["onnx"].is_file() and paths["bin"].is_file()


def get_kokoro_model():
    """Instancia Kokoro cacheada (singleton por proceso)."""
    with _model_lock:
        cached = _model_cache.get("model")
        if cached is not None:
            return cached
    apply_kokoro_speed_patch()
    try:
        import kokoro_onnx
    except ImportError as exc:
        raise RuntimeError(
            "el paquete kokoro-onnx no está disponible en este proceso"
        ) from exc
    if not _kokoro_files_ready():
        raise RuntimeError("el modelo Kokoro no está descargado")
    paths = kokoro_model_paths()
    model = kokoro_onnx.Kokoro(str(paths["onnx"]), str(paths["bin"]))
    with _model_lock:
        _model_cache["model"] = model
    return model


def reset_kokoro_model_cache() -> None:
    with _model_lock:
        _model_cache.clear()


# ---------------------------------------------------------------------------
# Normalización de números (el G2P de Kokoro lee las cifras dígito a dígito)
# ---------------------------------------------------------------------------

# Versiones/IPs con 2+ puntos: "0.6.3" → segmento a segmento con "punto".
_VERSION_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){2,}\b")
# Separador de miles: ES "1.250,50" / "1.250.500"; EN "1,250.50" / "1,250,500".
_ES_THOUSANDS_RE = re.compile(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b")
_EN_THOUSANDS_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
# Decimal simple sin miles: ES "250,5"; EN "3.14".
_ES_COMMA_DEC_RE = re.compile(r"\b\d+,\d{1,6}\b")
_EN_DEC_RE = re.compile(r"\b\d+\.\d+\b")
# Decimal con punto en ES sin grupo de miles: "2.5" (1-2 decimales, para no
# pisar "1.250" que ya resolvió la pasada de miles).
_ES_DOT_DEC_RE = re.compile(r"\b\d+\.\d{1,2}\b")
# Enteros cortos: un teléfono (10+ dígitos) se deja como está.
_INT_RE = re.compile(r"\b\d{1,9}\b")


def normalize_numbers(text: str, lang: str) -> str:
    """
    Convierte números a palabras antes de la fonematización de Kokoro.

    El G2P de Kokoro lee "1.250,50" dígito a dígito (sale "uno doscientos
    cincuenta, cincuenta" en vez de "mil...") y "0.6.3" como "cero seis
    tres" (sin "punto"). `lang` decide la convención de separadores
    (ES: punto=miles, coma=decimal; EN: lo inverso).
    """
    from num2words import num2words

    if not text or not any(ch.isdigit() for ch in text):
        return text
    es = lang.lower().startswith("es")
    words_lang = "es" if es else "en"
    sep = "punto" if es else "point"

    def _num(value: float) -> str:
        try:
            return num2words(value, lang=words_lang)
        except (ValueError, OverflowError):
            return str(value)

    def _version(m: re.Match) -> str:
        parts = m.group(0).split(".")
        # "1.250.500" (grupos de 3 tras el primero) es miles, no versión:
        # se deja para la pasada de separador de miles.
        if any(len(p) == 3 for p in parts[1:]):
            return m.group(0)
        return f" {sep} ".join(num2words(int(p), lang=words_lang) for p in parts)

    def _es_thousands(m: re.Match) -> str:
        return _num(float(m.group(0).replace(".", "").replace(",", ".")))

    def _en_thousands(m: re.Match) -> str:
        return _num(float(m.group(0).replace(",", "")))

    def _comma_dec(m: re.Match) -> str:
        return _num(float(m.group(0).replace(",", ".")))

    def _dot_dec(m: re.Match) -> str:
        return _num(float(m.group(0)))

    def _int(m: re.Match) -> str:
        return num2words(int(m.group(0)), lang=words_lang)

    text = _VERSION_RE.sub(_version, text)
    if es:
        text = _ES_THOUSANDS_RE.sub(_es_thousands, text)
        text = _ES_COMMA_DEC_RE.sub(_comma_dec, text)
        text = _ES_DOT_DEC_RE.sub(_dot_dec, text)
    else:
        text = _EN_THOUSANDS_RE.sub(_en_thousands, text)
        text = _EN_DEC_RE.sub(_dot_dec, text)
    text = _INT_RE.sub(_int, text)
    return text


# ---------------------------------------------------------------------------
# Síntesis
# ---------------------------------------------------------------------------


def rate_to_kokoro_speed(rate: int) -> float:
    """
    Traduce el rate de SAPI (-10..10, 0 = natural) a speed de Kokoro
    (0.5..2.0, 1.0 = natural; rango que el paquete aserta en create()).
    """
    speed = 1.0 + rate * 0.05
    return max(KOKORO_SPEED_MIN, min(KOKORO_SPEED_MAX, speed))


def kokoro_synthesize_wav(text: str, voice_name: str, speed: float = 1.0) -> bytes:
    """
    Sintetiza el texto con Kokoro y devuelve los bytes del WAV
    (mono 16-bit, 24 kHz). ValueError si la voz es ajena al catálogo.
    """
    spec = KOKORO_VOICES.get(voice_name)
    if spec is None:
        raise ValueError(f"Voz Kokoro desconocida: {voice_name}")
    model = get_kokoro_model()
    speed = max(KOKORO_SPEED_MIN, min(KOKORO_SPEED_MAX, float(speed)))
    normalized = normalize_numbers(text, spec["lang"])
    audio, sample_rate = model.create(
        normalized, voice_name, speed=speed, lang=spec["lang"]
    )
    if audio is None or len(audio) == 0:
        raise RuntimeError("Kokoro no generó audio")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    buffer = io.BytesIO()
    with wave.Wave_write(buffer) as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(pcm)
    data = buffer.getvalue()
    if not data:
        raise RuntimeError("Kokoro no generó audio")
    return data

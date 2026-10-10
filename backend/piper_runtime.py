"""
piper_runtime.py — Voces neuronales Piper gestionadas por la suite.

`piper` (motor TTS neuronal) se importa del entorno Python (dev: piper-tts +
onnxruntime en el venv). En la fase de empaquetado (sidecar congelado) se
provisionarán wheels a <DATA_DIR>/runtime/tts/piper-<pin>/ y se cargarán vía
sys.path — pendiente junto con kokoro_onnx (TTS-2). Las voces neuronales
(.onnx + .json) las descarga la suite desde HuggingFace (rhasspy/piper-voices)
con sha256 y tamaño verificados, a <runtime tts>/voices/.

Mismo patrón que stt_runtime.py: descarga por etapas, sha256 por archivo y
estado en meta local.
"""

from __future__ import annotations

import json
import asyncio
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

import runtime
from paths import DATA_DIR

logger = logging.getLogger("glyvex.piper_runtime")

__all__ = [
    "PIPER_DEFAULT_VOICE",
    "PIPER_VOICES",
    "delete_piper_voice",
    "download_piper_voice",
    "piper_engine_status",
    "piper_runtime_status",
    "piper_voice_paths",
    "piper_voice_status",
    "piper_voices_dir",
    "piper_voices_status",
    "sanitize_voice_config",
]

# Catálogo de voces (HuggingFace rhasspy/piper-voices). sha256 y tamaños
# verificados por descarga completa (TTS-1 ES 2026-09; TTS-2 EN 2026-09).
# Las dos voces es_MX-ald se retiraron del catálogo (TTS-2): la matriz
# comparativa las dejó fuera del TOP 6 final.
_PIPER_VOICES_BASE_V1 = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"

PIPER_VOICES: dict[str, dict] = {
    "es_ES-davefx-medium": {
        "label": "DaveFx · España",
        "onnx": "es_ES-davefx-medium.onnx",
        "json": "es_ES-davefx-medium.onnx.json",
        "url": _PIPER_VOICES_BASE_V1 + "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
        "sha256": "6658b03b1a6c316ee4c265a9896abc1393353c2d9e1bca7d66c2c442e222a917",
        "size": 63201294,
        "json_url": _PIPER_VOICES_BASE_V1 + "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json",
        "json_sha256": "0e0dda87c732f6f38771ff274a6380d9252f327dca77aa2963d5fbdf9ec54842",
        "json_size": 4817,
    },
    "es_MX-claude-high": {
        "label": "Claude · México (alta)",
        "onnx": "es_MX-claude-high.onnx",
        "json": "es_MX-claude-high.onnx.json",
        "url": _PIPER_VOICES_BASE_V1 + "es/es_MX/claude/high/es_MX-claude-high.onnx",
        "sha256": "3ef40a71ea63852cd8ab7e6fa7d2ecdcfa67a0b47c9c48e3f10e02ee02083ea0",
        "size": 63122309,
        "json_url": _PIPER_VOICES_BASE_V1 + "es/es_MX/claude/high/es_MX-claude-high.onnx.json",
        "json_sha256": "1afc81f703c0e4cb3b4d7c0dca096b8b54a98806807f0170cf5eb5557723c12d",
        "json_size": 4963,
    },
    "es_AR-daniela-high": {
        "label": "Daniela · Argentina (alta)",
        "onnx": "es_AR-daniela-high.onnx",
        "json": "es_AR-daniela-high.onnx.json",
        "url": _PIPER_VOICES_BASE_V1 + "es/es_AR/daniela/high/es_AR-daniela-high.onnx",
        "sha256": "7ceb1fc0dab349418c5b54a639ae9ee595212d7c9ea422220d8419163d5cc985",
        "size": 114199011,
        "json_url": _PIPER_VOICES_BASE_V1 + "es/es_AR/daniela/high/es_AR-daniela-high.onnx.json",
        "json_sha256": "aedbf69647e1d754c62ecf8e0366ca5f16af3e768e3c6b5329af6eb6bde3852b",
        "json_size": 7248,
    },
    "en_US-ryan-high": {
        "label": "Ryan · EE. UU. (alta)",
        "onnx": "en_US-ryan-high.onnx",
        "json": "en_US-ryan-high.onnx.json",
        "url": _PIPER_VOICES_BASE_V1 + "en/en_US/ryan/high/en_US-ryan-high.onnx",
        "sha256": "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca",
        "size": 120786792,
        "json_url": _PIPER_VOICES_BASE_V1 + "en/en_US/ryan/high/en_US-ryan-high.onnx.json",
        "json_sha256": "c6d3b98f08315cb4bebf0d49d50fc4ff491b503c64b940cd3d5ca28543b48011",
        "json_size": 4166,
    },
    "en_US-amy-medium": {
        "label": "Amy · EE. UU.",
        "onnx": "en_US-amy-medium.onnx",
        "json": "en_US-amy-medium.onnx.json",
        "url": _PIPER_VOICES_BASE_V1 + "en/en_US/amy/medium/en_US-amy-medium.onnx",
        "sha256": "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
        "size": 63201294,
        "json_url": _PIPER_VOICES_BASE_V1 + "en/en_US/amy/medium/en_US-amy-medium.onnx.json",
        "json_sha256": "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
        "json_size": 4882,
    },
    "en_US-lessac-medium": {
        "label": "Lessac · EE. UU.",
        "onnx": "en_US-lessac-medium.onnx",
        "json": "en_US-lessac-medium.onnx.json",
        "url": _PIPER_VOICES_BASE_V1 + "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        "size": 63201294,
        "json_url": _PIPER_VOICES_BASE_V1 + "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        "json_sha256": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
        "json_size": 4885,
    },
}

PIPER_DEFAULT_VOICE = "es_AR-daniela-high"

VOICES_META_FILE = ".piper-voices-meta.json"

_download_lock = asyncio.Lock()
_downloading_voices: set[str] = set()

def piper_voices_dir() -> Path:
    """Directorio de voces neuronales: <DATA_DIR>/runtime/tts/voices/."""
    return DATA_DIR / "runtime" / "tts" / "voices"


def piper_voice_paths(name: str) -> dict[str, Path]:
    """Rutas onnx/json de una voz del catálogo (raise ValueError si es ajena)."""
    spec = PIPER_VOICES.get(name)
    if spec is None:
        raise ValueError(f"Voz Piper desconocida: {name}")
    base = piper_voices_dir()
    return {"onnx": base / spec["onnx"], "json": base / spec["json"]}


_KNOWN_PHONEME_TYPES = (
    "espeak", "text", "pinyin", "hebrew", "japanese", "thai", "lithuanian",
)


def sanitize_voice_config(name: str) -> bool:
    """
    Sanea `phoneme_type` corrupto en el json descargado (idempotente).

    HuggingFace sirvió (2026-09) el json de es_MX-ald-medium con
    `"phoneme_type": "PhonemeType.ESPEAK"` (repr de un enum) en vez de
    `"espeak"`; piper-tts 1.7/1.8 lanzan ValueError al cargarla. El sha256 ya
    verificó la integridad contra upstream, así que reescribir el valor aquí
    es seguro. Devuelve True si reescribió.
    """
    paths = piper_voice_paths(name)
    json_path = paths["json"]
    if not json_path.is_file():
        return False
    try:
        config = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(config, dict):
        return False
    raw = config.get("phoneme_type")
    if raw is None or (isinstance(raw, str) and raw in _KNOWN_PHONEME_TYPES):
        return False
    fixed = str(raw).rsplit(".", 1)[-1].lower()
    if fixed not in _KNOWN_PHONEME_TYPES:
        return False  # Valor ajeno: no tocar, que piper reporte el error real.
    config["phoneme_type"] = fixed
    json_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("voz Piper %s: phoneme_type saneado %r -> %r", name, raw, fixed)
    return True


def _read_meta() -> dict:
    try:
        raw = (piper_voices_dir() / VOICES_META_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_meta(meta: dict) -> None:
    piper_voices_dir().mkdir(parents=True, exist_ok=True)
    (piper_voices_dir() / VOICES_META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _record_voice_state(name: str, state: str, error: str | None = None) -> None:
    meta = _read_meta()
    entry = meta.get(name, {}) if isinstance(meta.get(name), dict) else {}
    entry.update({"state": state, "error": error})
    meta[name] = entry
    try:
        _write_meta(meta)
    except OSError:
        pass  # El estado real sigue gobernando: archivos en disco.


def piper_voice_status(name: str) -> dict:
    """Estado de una voz del catálogo: ready | downloading | missing | error."""
    spec = PIPER_VOICES.get(name)
    if spec is None:
        raise ValueError(f"Voz Piper desconocida: {name}")
    paths = piper_voice_paths(name)
    base = piper_voices_dir()
    onnx_ready = base.is_dir() and paths["onnx"].is_file()
    json_ready = base.is_dir() and paths["json"].is_file()
    meta = _read_meta().get(name, {})
    if not isinstance(meta, dict):
        meta = {}
    if name in _downloading_voices or meta.get("state") == "downloading":
        state = "downloading"
    elif onnx_ready and json_ready:
        state = "ready"
    elif meta.get("state") == "error":
        state = "error"
    else:
        state = "missing"
    size_mb = 0.0
    for p in (paths["onnx"], paths["json"]):
        if p.is_file():
            size_mb += p.stat().st_size / (1024 * 1024)
    return {
        "name": name,
        "label": spec["label"],
        "state": state,
        "error": meta.get("error") if state == "error" else None,
        "size_mb": round(size_mb, 2),
        "download_mb": round((spec["size"] + spec["json_size"]) / (1024 * 1024), 1),
    }


def piper_voices_status() -> list[dict]:
    return [piper_voice_status(name) for name in PIPER_VOICES]


def piper_engine_status() -> dict:
    """
    Estado del paquete `piper` en el proceso actual (importable o no).

    Dev: piper-tts + onnxruntime en el venv. Empaquetado (pendiente): wheels
    provisionados en <DATA_DIR>/runtime/tts/piper-<pin>/ vía sys.path.
    """
    try:
        import piper  # noqa: F401

        try:
            from importlib.metadata import version as _pkg_version

            detail = f"piper-tts {_pkg_version('piper-tts')}"
        except Exception:  # noqa: BLE001
            detail = "piper importable"
        return {"available": True, "detail": detail}
    except ImportError as exc:
        return {"available": False, "detail": f"piper no importable: {exc}"}


def piper_runtime_status() -> dict:
    """Estado global para la UI: motor + catálogo de voces."""
    return {
        "engine": piper_engine_status(),
        "default_voice": PIPER_DEFAULT_VOICE,
        "can_download": True,
        "voices": piper_voices_status(),
    }


def _move_into(src: Path, base: Path, dest_name: str) -> Path:
    dest = base / dest_name
    if src.resolve() != dest.resolve():
        dest.unlink(missing_ok=True)
        shutil.move(str(src), str(dest))
    else:
        src.unlink(missing_ok=True)
    return dest


async def download_piper_voice(
    name: str, on_progress: Callable[[float, str], None]
) -> dict:
    """
    Descarga la voz (onnx + json) con sha256 y la mueve a voices/.

    on_progress(pct_global_0_100, detail) por cada chunk (mismo contrato que
    el runtime de STT). Devuelve el estado final de la voz.
    """
    spec = PIPER_VOICES.get(name)
    if spec is None:
        raise ValueError(f"Voz Piper desconocida: {name}")
    async with _download_lock:
        if name in _downloading_voices:
            raise RuntimeError(f"La voz {name} ya se está descargando")
        base = piper_voices_dir()
        base.mkdir(parents=True, exist_ok=True)
        work = DATA_DIR / "runtime" / "tts" / ".voice-download"
        work.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "name": spec["onnx"],
                "url": spec["url"],
                "sha256": spec["sha256"],
                "size": spec["size"],
            },
            {
                "name": spec["json"],
                "url": spec["json_url"],
                "sha256": spec["json_sha256"],
                "size": spec["json_size"],
            },
        ]
        _downloading_voices.add(name)
        _record_voice_state(name, "downloading")
        try:
            await runtime._download_stage(entries, work, on_progress, 0.0, 100.0)
            # El nombre en work es el basename de la URL (== nombre del archivo).
            for entry in entries:
                _move_into(work / entry["name"], base, entry["name"])
            sanitize_voice_config(name)
            _record_voice_state(name, "ready", None)
            shutil.rmtree(work, ignore_errors=True)
        except Exception as exc:
            _record_voice_state(name, "error", str(exc))
            raise
        finally:
            _downloading_voices.discard(name)
    return piper_voice_status(name)


def delete_piper_voice(name: str) -> None:
    """Elimina los archivos de una voz (vuelve a missing)."""
    paths = piper_voice_paths(name)  # ValueError si es ajena
    base = piper_voices_dir()
    for p in (paths["onnx"], paths["json"]):
        if base.is_dir() and p.is_file():
            p.unlink()
    meta = _read_meta()
    meta.pop(name, None)
    _write_meta(meta)


"""
stt_runtime.py — Runtime de STT (whisper.cpp) gestionado por la suite.

Mismo patrón que runtime.py (llama.cpp): la suite trae un pin PROBADO de
whisper.cpp; el binario NO vive en el repo (bloat de git, procedencia
verificable) y se descarga bajo demanda a <DATA_DIR>/runtime/stt/
whisper.cpp-<pin>/ — layout plano, exe + DLLs juntos, porque whisper-cli
busca sus DLLs en su propio directorio.

Política "solo versiones probadas": cada fuente declara URL + sha256; sin
sha256 la fuente no existe. Download explícito (botón), chunked, con
sha256 incremental y extract con strip de folder raíz (reutiliza los
helpers de runtime.py). Nunca automático.

T2.2 (Fase 2 del plan Tauri MSI, D4): whisper.cpp entra como binario nativo
con el patrón runtime en vez de faster-whisper en el bundle. El frontend
manda WAV PCM 16 kHz mono; el motor lo transcribe con whisper-cli.
POC (T2.1): b5130 (v1.9.4) + ggml-base.bin en CPU, RTF ~0.16, set mínimo
de 10 MB. Windows x64 solo en v1 (mismo gate que runtime.py).

Los modelos (ggml-*.bin) se gestionan aparte en T2.2b: catálogo con
sha256 y download bajo demanda a <dir runtime>/models/.

La API REST (/api/stt/runtime) vive en stt_runtime_api.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from paths import DATA_DIR
from runtime import _dir_size_mb, _download_stage, _extract_zip

logger = logging.getLogger("glyvex.stt_runtime")

# Pin probado en el POC (T2.1): release b5130 de ggml-org/whisper.cpp
# (v1.9.4). whisper-cli.exe + set mínimo de DLLs corre en CPU Windows x64
# sin dependencias externas (sin llama.dll ni SDL2).
STT_PIN = "b5130"

STT_BINARY_NAME = "whisper-cli.exe" if platform.system() == "Windows" else "whisper-cli"

# Set mínimo que funciona (POC T2.1, 10.0 MB): el cli, sus DLLs y al menos
# un backend ggml-cpu. El zip trae todos los backends de CPU según microarch;
# el check es: fijos presentes + ≥1 ggml-cpu-*.dll.
STT_REQUIRED_FILES = [
    "whisper-cli.exe",
    "whisper.dll",
    "ggml.dll",
    "ggml-base.dll",
]
STT_REQUIRED_GLOBS = ["ggml-cpu-*.dll"]

# Fuente única verificable (build oficial precompilada de la release b5130).
# sha256 verificado contra el zip descargado en el POC (8,573,270 bytes).
STT_SOURCES: list[dict] = [
    {
        "id": "official",
        "description": "Build oficial ggml-org/whisper.cpp b5130 (CPU, x64)",
        "files": [
            {
                "url": "https://github.com/ggml-org/whisper.cpp/releases/download/"
                       "b5130/whisper-bin-x64.zip",
                "sha256": "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c",
            },
        ],
    },
]


def _select_stt_source() -> dict | None:
    """Primera fuente con sha256 (no verificable = inexistente)."""
    for source in STT_SOURCES:
        if all(f.get("sha256") for f in source["files"]):
            return source
    return None


# ---------------------------------------------------------------------------
# Modelos STT (ggml-*.bin) — T2.2b
#
# Fuente: release GitHub NoMercy-Entertainment/nomercy-whisper-models
# v2026.08.23 (la misma del POC T2.1; HF está bloqueado en la IP del usuario).
# El catálogo cubre lo que esa fuente trae con manifest sha256 verificado:
# base / small / medium / large-v3. Desviación documentada de D6 (tiny y
# large-v3-turbo no existen en la fuente verificable).
#
# large-v3 viene partido en 2 (límite de 2 GB de assets de GitHub): se
# descarga cada parte, se verifica su sha256 individual y se concatenan
# en ggml-large-v3.bin.
# ---------------------------------------------------------------------------
STT_MODELS_SOURCE_ID = "nomercy-v2026.08.23"
_STT_MODELS_BASE_URL = (
    "https://github.com/NoMercy-Entertainment/nomercy-whisper-models/"
    "releases/download/v2026.08.23/"
)

STT_MODELS: dict[str, dict] = {
    "base": {
        "label": "Recomendado",
        "filename": "ggml-base.bin",
        "url": _STT_MODELS_BASE_URL + "ggml-base.bin",
        "sha256": "c2dad44135a48bc7c5a93564f4cd5cc8735ba8822999e358eb31f28d14d72a8b",
        "size": 147951482,
    },
    "small": {
        "label": "Mejor precisión",
        "filename": "ggml-small.bin",
        "url": _STT_MODELS_BASE_URL + "ggml-small.bin",
        "sha256": "cfd85d74dc730828cef4e13ae65898d9dc6f695fd00394f4399f7310e73cf505",
        "size": 487601984,
    },
    "medium": {
        "label": "Muy buena calidad",
        "filename": "ggml-medium.bin",
        "url": _STT_MODELS_BASE_URL + "ggml-medium.bin",
        "sha256": "a31f25a435c4f4c6577ccd883479273e5851372fc87ca7af9e663f47997f09ab",
        "size": 1533763076,
    },
    "large-v3": {
        "label": "Máxima calidad",
        "filename": "ggml-large-v3.bin",
        "url": None,
        "sha256": None,
        "size": 2097152000 + 997881483,
        "parts": [
            {
                "filename": "ggml-large-v3.bin.part-aa",
                "url": _STT_MODELS_BASE_URL + "ggml-large-v3.bin.part-aa",
                "sha256": "a7a22eff40a528eaf85c7baf72a1db1fc1cfb56ae7b004ec9f7d97f2579b7694",
                "size": 2097152000,
            },
            {
                "filename": "ggml-large-v3.bin.part-ab",
                "url": _STT_MODELS_BASE_URL + "ggml-large-v3.bin.part-ab",
                "sha256": "c13e5408c20ef80cc83d73baee09fb9c80725df169abd58732075efe4fbc93da",
                "size": 997881483,
            },
        ],
    },
}

STT_DEFAULT_MODEL = "base"


def stt_models_dir() -> Path:
    return stt_runtime_dir() / "models"


def stt_model_spec(name: str) -> dict:
    try:
        return STT_MODELS[name]
    except KeyError:
        raise ValueError(
            f"Modelo STT desconocido: {name} (disponibles: {', '.join(STT_MODELS)})"
        ) from None


def stt_model_path(name: str) -> Path:
    stt_model_spec(name)
    return stt_models_dir() / STT_MODELS[name]["filename"]


# ---------------------------------------------------------------------------
# Estado de modelos (T2.2b.2)
# ---------------------------------------------------------------------------
MODELS_META_FILENAME = ".stt-models-meta.json"

# Bandera transitoria por modelo (si la app muere a mitad de descarga, el
# próximo status lo dice "error" según la meta del modelo).
_downloading_models: set[str] = set()


def _read_models_meta() -> dict:
    path = stt_models_dir() / MODELS_META_FILENAME
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        return meta if isinstance(meta, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_models_meta(meta: dict) -> None:
    d = stt_models_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / MODELS_META_FILENAME).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def stt_model_status(name: str) -> dict:
    """Estado de UN modelo: missing · downloading · ready · error.

    Un archivo presente con tamaño distinto al del catálogo se marca
    "error" (descarga cortada / corrupta) en vez de "ready"."""
    spec = stt_model_spec(name)
    path = stt_model_path(name)
    meta = _read_models_meta().get(name, {})
    status = {
        "name": name,
        "label": spec["label"],
        "filename": spec["filename"],
        "size": spec["size"],
        "path": str(path),
        "state": "missing",
        "size_mb": None,
        "error": None,
    }
    if name in _downloading_models:
        status["state"] = "downloading"
    elif path.exists():
        actual = path.stat().st_size
        status["size_mb"] = round(actual / (1024 * 1024), 1)
        if actual == spec["size"]:
            status["state"] = "ready"
        else:
            status["state"] = "error"
            status["error"] = f"Modelo dañado: tamaño {actual} != esperado {spec['size']}"
    elif meta.get("state") == "error":
        status["state"] = "error"
        status["error"] = meta.get("error")
    return status


def stt_models_status() -> dict:
    """Catálogo completo con el estado de cada modelo (para la UI)."""
    return {
        "default": STT_DEFAULT_MODEL,
        "source": STT_MODELS_SOURCE_ID,
        "models": [stt_model_status(name) for name in STT_MODELS],
    }


# ---------------------------------------------------------------------------
# Download de modelos (T2.2b.3)
# ---------------------------------------------------------------------------
async def download_stt_model(name: str, on_progress=None) -> dict:
    """Descarga UN modelo a <dir runtime>/models/ (acto explícito).

    on_progress(pct 0-100, detail). single-file: una descarga; modelos
    partidos (large-v3): descarga por parte (sha256 individual) y
    concatenación al final con check de tamaño total. Un fallo deja la meta
    del modelo con state "error"; el modelo ya completo NO se re-descarga."""
    spec = stt_model_spec(name)
    if name in _downloading_models:
        raise RuntimeError(f"Ya hay una descarga del modelo STT {name} en curso")
    if on_progress is None:
        on_progress = lambda pct, detail: None  # noqa: E731
    target = stt_model_path(name)
    if target.exists() and target.stat().st_size == spec["size"]:
        return stt_model_status(name)

    work = DATA_DIR / "runtime" / "stt" / ".model-download"
    _downloading_models.add(name)
    meta = _read_models_meta()
    try:
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        entries = spec.get("parts") or [{
            "filename": spec["filename"],
            "url": spec["url"],
            "sha256": spec["sha256"],
        }]
        await _download_stage(
            [{"url": e["url"], "sha256": e["sha256"]} for e in entries],
            work, on_progress, 0.0, 90.0,
        )
        on_progress(90, f"Ensamblando {spec['filename']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        with open(target, "wb") as out:
            for part in sorted(work.glob("*")):
                with open(part, "rb") as fh:
                    shutil.copyfileobj(fh, out)
        actual = target.stat().st_size
        if actual != spec["size"]:
            raise RuntimeError(
                f"Tamaño final inesperado para {spec['filename']}: "
                f"{actual} != {spec['size']}"
            )
        shutil.rmtree(work, ignore_errors=True)
        meta.pop(name, None)
        if meta:
            _write_models_meta(meta)
        else:
            (target.parent / MODELS_META_FILENAME).unlink(missing_ok=True)
        on_progress(100, f"Modelo {name} listo")
        logger.info("modelo STT %s listo desde %s", name, STT_MODELS_SOURCE_ID)
    except BaseException as exc:
        shutil.rmtree(work, ignore_errors=True)
        meta[name] = {
            "state": "error",
            "error": str(exc),
            "date": datetime.now(timezone.utc).isoformat(),
        }
        _write_models_meta(meta)
        logger.error("descarga del modelo STT %s falló: %s", name, exc)
        raise
    finally:
        _downloading_models.discard(name)
    # Fuera del finally: con la bandera ya quitada, el status es "ready".
    return stt_model_status(name)


def delete_stt_model(name: str) -> bool:
    """Borra un modelo descargado (y su meta de error, si la tiene).

    True si se borró algo; False si no existía. Un modelo en descarga no
    se toca (RuntimeError)."""
    stt_model_spec(name)
    if name in _downloading_models:
        raise RuntimeError(f"No se puede borrar {name}: está descargando")
    path = stt_model_path(name)
    if not path.exists():
        return False
    path.unlink()
    meta = _read_models_meta()
    meta.pop(name, None)
    d = stt_models_dir()
    if meta:
        _write_models_meta(meta)
    else:
        (d / MODELS_META_FILENAME).unlink(missing_ok=True)
    if d.exists() and not any(d.iterdir()):
        d.rmdir()
    return True


META_FILENAME = ".stt-runtime-meta.json"

# Estado transitorio del proceso (no persiste: si la app muere a mitad de
# descarga, el próximo status dice "missing" o "error" según meta).
_downloading = False


def stt_runtime_dir() -> Path:
    return DATA_DIR / "runtime" / "stt" / f"whisper.cpp-{STT_PIN}"


def _read_meta() -> dict | None:
    path = stt_runtime_dir() / META_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _missing_required() -> list[str]:
    """Archivos del set mínimo ausentes (los fijos por nombre, los globs si
    no hay NINGUNA coincidencia). Vacío = set completo."""
    d = stt_runtime_dir()
    missing = [name for name in STT_REQUIRED_FILES if not (d / name).exists()]
    for pattern in STT_REQUIRED_GLOBS:
        if not any(d.glob(pattern)):
            missing.append(pattern)
    return missing


def stt_runtime_status() -> dict:
    """Estado del runtime de STT (para GET /api/stt/runtime y UI).

    Estados: unsupported (no Windows) · downloading · ready (binario + set
    mínimo completo) · error (meta falló o set incompleto) · missing."""
    status = {
        "pin": STT_PIN,
        "state": "missing",
        "binary_path": None,
        "size_mb": None,
        "source": None,
        "missing_files": [],
        "error": None,
    }
    if platform.system() != "Windows":
        status["state"] = "unsupported"
        return status
    if _downloading:
        status["state"] = "downloading"
        return status
    d = stt_runtime_dir()
    meta = _read_meta()
    binary = d / STT_BINARY_NAME
    # Sin directorio no hay nada que auditar: "missing" limpio (no "error").
    missing = _missing_required() if d.exists() else []
    if binary.exists() and not missing:
        status["state"] = "ready"
        status["binary_path"] = str(binary)
        status["source"] = (meta or {}).get("source")
        status["size_mb"] = _dir_size_mb(d)
    elif meta and meta.get("state") == "error":
        # Un download fallido deja la causa real; gana al "incompleto" genérico.
        status["state"] = "error"
        status["error"] = meta.get("error")
        status["missing_files"] = missing
    elif missing:
        status["state"] = "error"
        status["missing_files"] = missing
        status["error"] = f"Runtime STT incompleto: faltan {', '.join(missing)}"
    return status


def can_download() -> bool:
    """¿Se puede descargar el runtime de STT? (Windows + fuente verificable)."""
    return platform.system() == "Windows" and _select_stt_source() is not None


def _write_meta(meta: dict) -> None:
    d = stt_runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / META_FILENAME).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


async def download_stt_runtime(on_progress=None) -> dict:
    """Descarga, verifica y extrae el runtime de STT del pin activo (acto explícito).

    on_progress(pct: float 0-100, detail: str) — opcional. Un fallo deja la
    meta con state "error" y el próximo status lo muestra. Los modelos
    (T2.2b, <dir>/models/) sobreviven a una re-descarga del motor.
    """
    global _downloading
    if platform.system() != "Windows":
        raise RuntimeError("El runtime de STT es Windows-only en v1")
    if _downloading:
        raise RuntimeError("Ya hay una descarga de runtime STT en curso")
    source = _select_stt_source()
    if source is None:
        raise RuntimeError("No hay fuente de runtime STT con sha256 verificable")
    if on_progress is None:
        on_progress = lambda pct, detail: None  # noqa: E731

    work = DATA_DIR / "runtime" / "stt" / f".download-{STT_PIN}"
    _downloading = True
    try:
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        await _download_stage(source["files"], work, on_progress, 0.0, 90.0)
        target = stt_runtime_dir()
        models_backup = work / "models-backup"
        if (target / "models").exists():
            shutil.move(str(target / "models"), models_backup)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        on_progress(90, "Extrayendo whisper.cpp")
        for zip_path in sorted(work.glob("*.zip")):
            _extract_zip(zip_path, target)
            zip_path.unlink()
        if models_backup.exists():
            shutil.move(str(models_backup), target / "models")
        missing = _missing_required()
        if missing:
            raise RuntimeError(f"Archive incompleto: faltan {', '.join(missing)}")
        _write_meta({
            "pin": STT_PIN,
            "state": "ready",
            "source": source["id"],
            "date": datetime.now(timezone.utc).isoformat(),
            "size_mb": _dir_size_mb(target),
        })
        shutil.rmtree(work, ignore_errors=True)
        on_progress(100, "Runtime STT listo")
        logger.info("runtime STT %s listo desde %s", STT_PIN, source["id"])
        # La bandera se limpia ANTES de leer el status (mismo motivo que
        # runtime.py): el argumento se evalúa antes del finally.
        _downloading = False
        return stt_runtime_status()
    except BaseException as exc:
        shutil.rmtree(work, ignore_errors=True)
        _write_meta({
            "pin": STT_PIN,
            "state": "error",
            "error": str(exc),
            "source": source["id"],
            "date": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("descarga de runtime STT falló: %s", exc)
        raise
    finally:
        _downloading = False


async def probe_stt_binary(binary_path: str | None = None) -> bool:
    """Ejecuta `whisper-cli --help` con timeout corto; True si arranca.

    A diferencia del probe de llama-server no se parsean flags: whisper-cli
    es un binario fijo del pin (no hay variación entre builds que gestionar).
    """
    binary = binary_path or str(stt_runtime_dir() / STT_BINARY_NAME)
    if not Path(binary).exists():
        return False
    try:
        # whisper-cli --help escribe la ayuda por STDERR (verificado b5130);
        # se capturan los dos flujos y basta que uno tenga contenido.
        # Sin CREATE_NO_WINDOW el probe abriría una consola visible.
        kwargs = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if platform.system() == "Windows"
            else {}
        )
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        return proc.returncode == 0 and bool((out or b"").strip() or (err or b"").strip())
    except (OSError, asyncio.TimeoutError):
        return False


def reset_stt_runtime() -> bool:
    """Borra el runtime de STT gestionado (reinstala = reset + download)."""
    d = stt_runtime_dir()
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


__all__ = [
    "STT_PIN",
    "STT_BINARY_NAME",
    "STT_SOURCES",
    "STT_MODELS",
    "STT_MODELS_SOURCE_ID",
    "STT_DEFAULT_MODEL",
    "can_download",
    "download_stt_runtime",
    "download_stt_model",
    "probe_stt_binary",
    "reset_stt_runtime",
    "stt_runtime_dir",
    "stt_runtime_status",
    "stt_models_dir",
    "stt_model_spec",
    "stt_model_path",
    "stt_model_status",
    "stt_models_status",
    "delete_stt_model",
    "_select_stt_source",
]

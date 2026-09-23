"""
runtime.py — Runtime de inferencia (llama.cpp) gestionado por la suite.

La suite trae su propia build PROBADa de llama.cpp (pin): el usuario no
instala nada de inferencia. El binario NO vive en el repo (no bloat de git,
procedencia verificable): la app lo descarga bajo demanda a
<DATA_DIR>/runtime/llama.cpp-<pin>/ — layout plano, exe + DLLs en la misma
carpeta, porque llama-server.exe busca sus DLLs en su propio directorio.

Política "solo versiones probadas": cada fuente declara URL + sha256; una
fuente sin sha256 se saltea (no verificable = no existe para nosotros).
El download es explícito (botón), chunked, con verificación sha256 y
extract con strip de folder raíz si el zip la tiene. Nunca es automático.

RT-10 fase A (v0.6.0): el runtime se empaqueta en 2 niveles — motor BASE
(CPU, corre en cualquier hardware Windows x64) + aceleración por familia
de GPU (nvidia en fase A; vulkan/rocm entran en fase B). El download va en
2 etapas: 1º base, 2º la aceleración de la familia detectada. Sin
aceleración (familia sin paquete, download fallido) el runtime queda
"degradado" a CPU: operativo, nunca un error. Un fallo del motor base
SÍ es error.

v1: solo Windows x64. Otras plataformas → state "unsupported"; el modo
experto (binary_path) sigue funcionando igual.

La API REST (/api/runtime) vive en runtime_api.py; este módulo es el núcleo
que la alimenta (estado, descarga, reset, detección de GPU).
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from paths import DATA_DIR

logger = logging.getLogger("glyvex.runtime")

# Imports opcionales: pynvml puede no estar instalado y winreg solo existe en
# Windows. Nada de esto debe crashear el módulo (mismo patrón que metrics.py).
try:
    import pynvml
except ImportError:
    pynvml = None  # type: ignore[assignment]

try:
    import winreg
except ImportError:
    winreg = None

RUNTIME_PIN = "b11009"
META_FILENAME = ".runtime-meta.json"
BINARY_NAME = "llama-server.exe" if platform.system() == "Windows" else "llama-server"

# Split de RT-10: el archive único probado en NVIDIA
# (glyvex-runtime-win-x64-b11009.zip, 55 archivos) se repaqueta en 2 niveles,
# verificados byte-identicos (55/55). Cada nivel conserva el orden de
# preferencia "glyvex" (assets propios, release de la suite) → "official"
# (assets de la release ggml-org/llama.cpp b11009, ya probados en la máquina
# de desarrollo).

# Release de la suite (repo open source público) donde viven los archives
# propios del split.
RELEASE = "v0.5.0"
SUITE_RELEASE_REPO = "facebey/Glyvex-Group-AI-Suite-Open-Source"

# Nivel 1: motor base (CPU). Corre en cualquier hardware Windows x64; la
# aceleración, cuando existe para la familia, se extrae encima.
BASE_SOURCES: list[dict] = [
    {
        "id": "glyvex",
        "description": "Motor base glyvex-runtime-base-win-x64-b11009.zip (asset de la release de la suite)",
        "files": [
            {
                "url": f"https://github.com/{SUITE_RELEASE_REPO}/releases/download/"
                       f"{RELEASE}/glyvex-runtime-base-win-x64-b11009.zip",
                "sha256": "76d7c36366cee940662a2bcaf294517255517117b322a64123f64a5d01e3da79",
            },
        ],
    },
    {
        "id": "official",
        "description": "Build oficial ggml-org/llama.cpp b11009 (CPU)",
        "files": [
            {
                "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                       "b11009/llama-b11009-bin-win-cpu-x64.zip",
                "sha256": "39973a6c78303dd3cf0ab202a0ec24976579b80704320733e43b086b70af558d",
            },
        ],
    },
]

# Nivel 2: aceleración por familia de GPU, en orden de preferencia.
# Fase A: solo "nvidia". "vulkan" (AMD+Intel) y "rocm" (RDNA2/3) entran en
# fase B SOLO tras la aprobación del protocolo PRUEBA-VULKAN-AMD-b11009.md
# en hardware real. Familia ausente → runtime degradado a CPU, no error.
ACCEL_SOURCES: dict[str, list[dict]] = {
    "nvidia": [
        {
            "id": "glyvex",
            "description": "Aceleración NVIDIA glyvex-runtime-accel-nvidia-win-x64-b11009.zip (ggml-cuda + cuBLAS + cuDART)",
            "files": [
                {
                    "url": f"https://github.com/{SUITE_RELEASE_REPO}/releases/download/"
                           f"{RELEASE}/glyvex-runtime-accel-nvidia-win-x64-b11009.zip",
                    "sha256": "d61c6371fca42b018b7f2a40d35c95f78304c1fdc6015433c33e37b242ee30f4",
                },
            ],
        },
        {
            "id": "official",
            "description": "Build oficial ggml-org/llama.cpp b11009 (CUDA 13.4 + cuDART)",
            "files": [
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                           "b11009/llama-b11009-bin-win-cuda-13.4-x64.zip",
                    "sha256": "8e8432f924cd36ce038477907ed5533ad323242522feab9bcae267346c93b00a",
                },
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                           "b11009/cudart-llama-bin-win-cuda-13.4-x64.zip",
                    "sha256": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
                },
            ],
        },
    ],
}

# Estado transitorio del proceso (no persiste: si la app muere a mitad de
# descarga, el próximo runtime_status() dice "missing" o "error" según meta).
_downloading = False


def runtime_dir() -> Path:
    return DATA_DIR / "runtime" / f"llama.cpp-{RUNTIME_PIN}"


def _select_base_source() -> dict | None:
    """Primera fuente base con sha256 (no verificable = inexistente)."""
    for source in BASE_SOURCES:
        if all(f.get("sha256") for f in source["files"]):
            return source
    return None


def _select_accel_source(family: str) -> dict | None:
    """Primera fuente de aceleración verificable para la familia. None si la
    familia no tiene paquete → runtime degradado a CPU (no error)."""
    for source in ACCEL_SOURCES.get(family, []):
        if all(f.get("sha256") for f in source["files"]):
            return source
    return None


def _read_meta() -> dict | None:
    path = runtime_dir() / META_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _dir_size_mb(path: Path) -> float | None:
    if not path.exists():
        return None
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 1)


def runtime_status() -> dict:
    """Estado del runtime gestionado (para GET /api/runtime/status y UI)."""
    status = {
        "pin": RUNTIME_PIN,
        "state": "missing",
        "binary_path": None,
        "build": None,
        "size_mb": None,
        "source": None,
        "accel": None,
        "accel_error": None,
        "error": None,
    }
    if platform.system() != "Windows":
        status["state"] = "unsupported"
        return status
    if _downloading:
        status["state"] = "downloading"
        return status
    d = runtime_dir()
    meta = _read_meta()
    binary = d / BINARY_NAME
    if binary.exists():
        status["state"] = "ready"
        status["binary_path"] = str(binary)
        status["build"] = (meta or {}).get("build", RUNTIME_PIN)
        status["source"] = (meta or {}).get("source")
        status["accel"] = (meta or {}).get("accel")
        status["accel_error"] = (meta or {}).get("accel_error")
        status["size_mb"] = _dir_size_mb(d)
    elif meta and meta.get("state") == "error":
        status["state"] = "error"
        status["error"] = meta.get("error")
    return status


# Clase del registro donde Windows lista los video controllers.
_VIDEO_CONTROLLER_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


def _gpu_via_pynvml() -> str | None:
    """Nombre de la GPU NVIDIA vía pynvml, o None si no está disponible.

    No llama a nvmlShutdown: el ciclo de vida de NVML lo maneja el collector
    de metrics.py; si NVML aún no está inicializado, las llamadas fallan y se
    cae al fallback de video controller.
    """
    if pynvml is None:
        return None
    try:
        try:
            pynvml.nvmlInit()
        except Exception:
            return None
        if pynvml.nvmlDeviceGetCount() == 0:
            return None
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        return name.decode("utf-8") if isinstance(name, bytes) else str(name)
    except Exception:
        return None


def _gpu_via_video_controller() -> str | None:
    """Nombre del video controller desde el registro de Windows (rápido, sin
    subprocess). Si hay más de uno, gana el NVIDIA (el que corre CUDA)."""
    if winreg is None or platform.system() != "Windows":
        return None
    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VIDEO_CONTROLLER_CLASS_KEY) as key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        value, _ = winreg.QueryValueEx(subkey, "DriverDesc")
                        names.append(str(value))
                    finally:
                        winreg.CloseKey(subkey)
                except OSError:
                    continue
    except OSError:
        return None
    nvidia = next((n for n in names if "nvidia" in n.lower()), None)
    return nvidia or (names[0] if names else None)


def detect_gpu() -> str:
    """Cascada de detección de GPU: pynvml → video controller de Windows → "cpu"."""
    return _gpu_via_pynvml() or _gpu_via_video_controller() or "cpu"


def detect_gpu_family() -> str:
    """Familia para el gate del runtime: nvidia | amd | intel | cpu.

    Granularidad a propósito (fase A): solo nvidia cambia la oferta
    (aceleración CUDA). Distinguir AMD RDNA2/3 (ROCm) de AMD antigua (solo
    Vulkan) es fase B, bloqueada por la prueba en hardware real.
    """
    name = (detect_gpu() or "").lower()
    if "nvidia" in name:
        return "nvidia"
    if "amd" in name or "radeon" in name:
        return "amd"
    if "intel" in name or "iris" in name or "arc" in name:
        return "intel"
    return "cpu"


def resolve_binary(expert_path: str | None = None) -> str | None:
    """Cascada de resolución del binario de inferencia.

    1. binary_path del modo experto (gana siempre, como hoy).
    2. Runtime gestionado, si el binario está en su lugar.
    3. None → el llamador decide (400 con code runtime_missing, ver RT-2).
    """
    if expert_path:
        return expert_path
    if platform.system() != "Windows":
        return None
    binary = runtime_dir() / BINARY_NAME
    return str(binary) if binary.exists() else None


def reset_runtime() -> bool:
    """Borra el runtime gestionado (reinstala = reset + download)."""
    d = runtime_dir()
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def _extract_zip(zip_path: Path, target: Path) -> None:
    """Extrae un zip a target; si todas las entradas comparten UNA folder
    raíz, la descarta (el archive propio trae root; los upstream son planos)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        prefix = tops.pop() + "/" if len(tops) == 1 else ""
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = info.filename[len(prefix):] if prefix else info.filename
            if not rel:
                continue
            dest = (target / rel).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise RuntimeError(f"Entrada de zip fuera del destino: {info.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


async def _download_file(spec: dict, dest: Path, on_progress, index: int, total: int) -> None:
    """Descarga un archivo con sha256 incremental; borra el parcial si falla."""
    url = spec["url"]
    expected = spec["sha256"].lower()
    share = 1.0 / total
    base = (index - 1) * share
    hasher = hashlib.sha256()
    downloaded = 0
    total_bytes = 0
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total_bytes = int(resp.headers.get("content-length") or 0)
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        fh.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if total_bytes:
                            on_progress(
                                (base + share * downloaded / total_bytes) * 100,
                                f"Descargando {dest.name}",
                            )
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if hasher.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"sha256 inválido para {dest.name}: esperado {expected}, "
            f"obtenido {hasher.hexdigest()}"
        )
    on_progress((base + share) * 100, f"Verificado {dest.name}")


async def _download_stage(
    files: list[dict], work: Path, on_progress, pct_lo: float, pct_hi: float
) -> None:
    """Descarga todos los archivos de una etapa, escalando el progreso
    global entre pct_lo y pct_hi (cada archivo ocupa su slot dentro de la etapa)."""
    span = pct_hi - pct_lo
    for i, spec in enumerate(files, 1):
        await _download_file(
            spec,
            work / Path(spec["url"]).name,
            lambda pct, detail: on_progress(pct_lo + span * pct / 100, detail),
            i,
            len(files),
        )


def _write_meta(meta: dict) -> None:
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / META_FILENAME).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def can_download() -> bool:
    """¿Se puede descargar el runtime? (Windows + fuente BASE verificable;
    la aceleración es opcional y se decide por familia en el download)."""
    return platform.system() == "Windows" and _select_base_source() is not None


async def download_runtime(on_progress=None) -> dict:
    """Descarga y verifica el runtime del pin activo en 2 etapas (acto explícito).

    1. Motor BASE (0-50 %): siempre. Un fallo aquí ES error.
    2. Aceleración de la familia detectada (50-100 %): solo si existe
       paquete verificable para la familia (fase A: nvidia). Un fallo aquí
       NO rompe el runtime: queda degradado a CPU (meta con accel_error) y
       la descarga termina en "ready".

    on_progress(pct: float 0-100, detail: str) — opcional.
    """
    global _downloading
    if platform.system() != "Windows":
        raise RuntimeError("El runtime gestionado es Windows-only en v1")
    if _downloading:
        raise RuntimeError("Ya hay una descarga de runtime en curso")
    base = _select_base_source()
    if base is None:
        raise RuntimeError("No hay fuente de runtime base con sha256 verificable")
    family = detect_gpu_family()
    accel = _select_accel_source(family)
    if on_progress is None:
        on_progress = lambda pct, detail: None  # noqa: E731

    work = DATA_DIR / "runtime" / f".download-{RUNTIME_PIN}"
    _downloading = True
    try:
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        # Etapa 1: motor base.
        await _download_stage(base["files"], work, on_progress, 0.0, 50.0)
        target = runtime_dir()
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        on_progress(50, "Extrayendo motor base")
        for zip_path in sorted(work.glob("*.zip")):
            _extract_zip(zip_path, target)
            zip_path.unlink()
        if not (target / BINARY_NAME).exists():
            raise RuntimeError(f"El binario {BINARY_NAME} no está en el archive base")
        # Etapa 2: aceleración (degradación elegante, nunca un error).
        accel_id = None
        accel_error = None
        if accel is not None:
            try:
                await _download_stage(accel["files"], work, on_progress, 50.0, 100.0)
                on_progress(100, "Extrayendo aceleración GPU")
                for zip_path in sorted(work.glob("*.zip")):
                    _extract_zip(zip_path, target)
                    zip_path.unlink()
                accel_id = accel["id"]
            except BaseException as exc:
                accel_error = str(exc)
                logger.warning(
                    "aceleración %s no instalada; runtime degradado a CPU: %s",
                    family, exc,
                )
        _write_meta({
            "pin": RUNTIME_PIN,
            "state": "ready",
            "build": RUNTIME_PIN,
            "source": base["id"],
            "accel": family if accel_id else None,
            "accel_source": accel_id,
            "accel_error": accel_error,
            "date": datetime.now(timezone.utc).isoformat(),
            "size_mb": _dir_size_mb(target),
            "files": [Path(s["url"]).name for s in base["files"]]
                     + ([Path(s["url"]).name for s in accel["files"]] if accel else []),
        })
        shutil.rmtree(work, ignore_errors=True)
        logger.info(
            "runtime %s listo desde %s (accel: %s)",
            RUNTIME_PIN, base["id"], accel_id or "none",
        )
        # Limpiar la bandera ANTES de leer el status: un `return runtime_status()`
        # dentro del try evalúa el argumento antes de que el finally corra, y así
        # reportaría "downloading" en vez de "ready". El finally queda como red.
        _downloading = False
        return runtime_status()
    except BaseException as exc:
        shutil.rmtree(work, ignore_errors=True)
        _write_meta({
            "pin": RUNTIME_PIN,
            "state": "error",
            "error": str(exc),
            "source": base["id"],
            "date": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("descarga de runtime falló: %s", exc)
        raise
    finally:
        _downloading = False

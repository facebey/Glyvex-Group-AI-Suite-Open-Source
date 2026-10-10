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
(CPU, corre en cualquier hardware x64) + aceleración por familia
de GPU (nvidia en fase A; vulkan/rocm entran en fase B). El download va en
2 etapas: 1º base, 2º la aceleración de la familia detectada. Sin
aceleración (familia sin paquete, download fallido) el runtime queda
"degradado" a CPU: operativo, nunca un error. Un fallo del motor base
SÍ es error.

v1: Windows x64 + Linux x64 (RT-14). Otras plataformas → state
"unsupported"; el modo experto (binary_path) sigue funcionando igual.
En Linux los archives son .tar.gz (los de Windows, .zip) y el binario
lleva el bit executable (se garantiza al extraer).

La API REST (/api/runtime) vive en runtime_api.py; este módulo es el núcleo
que la alimenta (estado, descarga, reset, detección de GPU).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import tarfile
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

RUNTIME_PIN = "b11349"
META_FILENAME = ".runtime-meta.json"
BINARY_NAME = "llama-server.exe" if platform.system() == "Windows" else "llama-server"


def platform_supported() -> bool:
    """¿Hay runtime gestionado para esta máquina? v1: Windows x64 + Linux x64
    (RT-14). El modo experto (binary_path) no depende de esto."""
    if platform.system() not in ("Windows", "Linux"):
        return False
    return platform.machine() in ("AMD64", "x86_64", "x64")


def _platform_key() -> str:
    return "win" if platform.system() == "Windows" else "linux"


def _platform_files(files: list[dict]) -> list[dict]:
    """Archivos de una fuente para la plataforma actual. El catálogo es
    compartido: cada archivo declara "platforms" (sin la clave = win,
    compatibilidad con fixtures/tests antiguos)."""
    key = _platform_key()
    return [f for f in files if key in f.get("platforms", ["win"])]

# Split de RT-10 en 2 niveles: motor base (CPU) siempre, y aceleración de la
# familia detectada encima cuando existe paquete verificable. La fuente es la
# build oficial de la release ggml-org/llama.cpp del pin (b11349, familia
# v0.5.0), descargada y verificada en la máquina de desarrollo. El bump
# b11146 -> b11349 no cambió la superficie de flags: 329 long-flags idénticos
# en ambos (0 agregados / 0 removidos, verificado contra --help real de los
# dos binarios).

# Nivel 1: motor base (CPU). Corre en cualquier hardware x64 (Windows y
# Linux); la aceleración, cuando existe para la familia, se extrae encima.
BASE_SOURCES: list[dict] = [
    {
        "id": "official",
        "description": "Build oficial ggml-org/llama.cpp b11349 (CPU)",
        "files": [
            {
                "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                       "b11349/llama-b11349-bin-win-cpu-x64.zip",
                "sha256": "3f387c5877b66d078b89b52a2a59cf0f5aa10e69513f3956163edabacffa1c6e",
                "platforms": ["win"],
            },
            {
                # Builds "ubuntu" target glibc vieja → compatibles con
                # versiones nuevas (probado contra Ubuntu 26.04, RT-14).
                "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                       "b11349/llama-b11349-bin-ubuntu-x64.tar.gz",
                "sha256": "7efd2fb72db59f12b05614a709ba6d506b9859e867943955fc9bbd8ee13eccea",
                "platforms": ["linux"],
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
            # Windows, CUDA 12.4: soporta Maxwell (sm_50)→Hopper (sm_90) e
            # incluye Pascal (sm_61, p. ej. 1080 Ti), que CUDA 13.x TIRÓ.
            # Selección por arquitectura (RT-15): cc major < 10 → 12.x por
            # defecto (bundle autocontenido, el usuario no instala toolkit).
            "id": "official-cuda-12.4",
            "description": "Build oficial ggml-org/llama.cpp b11349 (CUDA 12.4 + cuDART, Windows)",
            "cuda_major": 12,
            "files": [
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                            "b11349/llama-b11349-bin-win-cuda-12.4-x64.zip",
                    "sha256": "1084a0a4c6567511c7f67d8c4db71979f837170ed22b2fcb72c0d8637a4eaf14",
                    "platforms": ["win"],
                },
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                            "b11349/cudart-llama-bin-win-cuda-12.4-x64.zip",
                    "sha256": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
                    "platforms": ["win"],
                },
            ],
        },
        {
            # Linux, CUDA 12.8: máxima compatibilidad — corre con driver
            # NVIDIA >=570 (p. ej. 580.x). CUDA 13.4 exige driver >=590, así
            # que en equipos con driver 580 la GPU jamás se inicializaría.
            "id": "official-cuda-12.8",
            "description": "Build oficial ggml-org/llama.cpp b11349 (CUDA 12.8 + cuDART, Linux)",
            "cuda_major": 12,
            "files": [
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                            "b11349/llama-b11349-bin-ubuntu-cuda-12.8-x64.tar.gz",
                    "sha256": "fa9f499e892e5388ec1a2250bc12478f539f6b155cae662d72f223d81d55d04b",
                    "platforms": ["linux"],
                },
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                            "b11349/cudart-llama-b11349-bin-ubuntu-cuda-12.8-x64.tar.gz",
                    "sha256": "056b65c841f6a54a36c1af38d2d47354bb4ac060f930c0941ce3f0b34423190f",
                    "platforms": ["linux"],
                },
            ],
        },
        {
            # CUDA 13.4: solo Blackwell (sm_100/120/121, cc major >= 10) o
            # elección explícita. TIRÓ Maxwell/Pascal/Volta (13.4 compila
            # 75/80/86/89/90/120a/121a; cero Pascal), por eso no es el
            # default para arquitecturas <= 9.x (RT-15).
            "id": "official",
            "description": "Build oficial ggml-org/llama.cpp b11349 (CUDA 13.4 + cuDART)",
            "cuda_major": 13,
            "files": [
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                           "b11349/llama-b11349-bin-win-cuda-13.4-x64.zip",
                    "sha256": "06f2efb5f54d845002972928fa879448491799a556741c185a1974b491d8b4f0",
                    "platforms": ["win"],
                },
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                           "b11349/cudart-llama-bin-win-cuda-13.4-x64.zip",
                    "sha256": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
                    "platforms": ["win"],
                },
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                           "b11349/llama-b11349-bin-ubuntu-cuda-13.4-x64.tar.gz",
                    "sha256": "4fbe049cfc3ecdc42a7f69816f6d6527562680f24de907e0996933495bf7eac3",
                    "platforms": ["linux"],
                },
                {
                    "url": "https://github.com/ggml-org/llama.cpp/releases/download/"
                           "b11349/cudart-llama-b11349-bin-ubuntu-cuda-13.4-x64.tar.gz",
                    "sha256": "0b6167c50599cc33184121c5491f1a75527a4aad13f3915d261fd520f51cd2a9",
                    "platforms": ["linux"],
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


def _orphaned_runtime_dirs() -> list[Path]:
    """Builds gestionadas anteriores bajo runtime/ (llama.cpp-*) que NO son la
    del pin activo. Un bump de pin deja la dir vieja huérfana y duplica el disco
    (~700 MB); el download las borra por defecto (reemplazo). No toca stt/tts ni
    los temporales de descarga (.download-*)."""
    root = DATA_DIR / "runtime"
    if not root.exists():
        return []
    current = runtime_dir().name
    return [
        p for p in root.iterdir()
        if p.is_dir() and p.name.startswith("llama.cpp-") and p.name != current
    ]


def _select_base_source() -> dict | None:
    """Primera fuente base verificable para la plataforma actual
    (sin archivos de la plataforma o sin sha256 = inexistente)."""
    for source in BASE_SOURCES:
        files = _platform_files(source["files"])
        if files and all(f.get("sha256") for f in files):
            return source
    return None


def _max_gpu_compute_capability() -> tuple[int, int] | None:
    """Mayor (major, minor) de compute capability entre TODAS las GPUs NVIDIA
    vía NVML. None si NVML no está disponible (sin pynvml, sin driver, GPU
    ausente o error). El bundle debe correr en todas las GPUs, así que el
    límite lo marca la más nueva (Blackwell → CUDA 13)."""
    if pynvml is None:
        return None
    try:
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return None
        maxcc: tuple[int, int] | None = None
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            cc = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            if maxcc is None or cc > maxcc:
                maxcc = cc
        return maxcc
    except Exception:
        return None


def _required_cuda_major() -> int:
    """Mayor de CUDA del bundle según la arquitectura detectada (RT-15):
    13 solo si alguna GPU es Blackwell (sm_100/120/121, cc major >= 10 —
    CUDA 12.x no la compila); 12 en caso contrario, que cubre Maxwell (5.0)→
    Hopper (9.0) e incluye Pascal (6.1), que CUDA 13.x tiró. Sin NVML (no se
    detecta la arch) → 12 por defecto: safe default, cubre el caso Pascal muy
    común y solo falla en Blackwell (raro)."""
    cc = _max_gpu_compute_capability()
    if cc is not None and cc[0] >= 10:
        return 13
    return 12


def _cuda_note() -> str | None:
    """(RT-15) Compute capability de la GPU cuando su arquitectura NO la
    soporta CUDA 13.x (Maxwell/Pascal/Volta, cc < 7.5): la UI lo usa para el
    aviso "se usó CUDA 12.x porque 13.x no soporta esta GPU". None en caso
    contrario (Turing+/Hopper/Blackwell sí corren en 13.x; sin NVML no hay
    dato y no hay qué asustar)."""
    cc = _max_gpu_compute_capability()
    if cc is None or cc >= (7, 5):
        return None
    return f"{cc[0]}.{cc[1]}"


def _select_accel_source(family: str) -> dict | None:
    """Fuente de aceleración verificable para la familia + plataforma, elegida
    por el mayor de CUDA que requiere la arquitectura de la GPU (RT-15).
    None si no hay paquete → runtime degradado a CPU (no error).

    Dos pasadas: 1º fuentes del mayor requerido (o sin tag cuda_major, para
    fixtures); 2º fallback a cualquier fuente verificable (catálogo legado).
    """
    sources = ACCEL_SOURCES.get(family, [])
    required = _required_cuda_major()

    def _verifiable(source: dict) -> bool:
        files = _platform_files(source["files"])
        return bool(files) and all(f.get("sha256") for f in files)

    for source in sources:
        if source.get("cuda_major") not in (required, None):
            continue
        if _verifiable(source):
            return source
    for source in sources:
        if _verifiable(source):
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


def _previous_builds() -> list[dict]:
    """Builds gestionadas anteriores (llama.cpp-*) con su pin y tamaño: la base
    del estado "outdated" (RT-12) — el pin actual no está descargado pero queda
    una build vieja en disco que se puede conservar (rollback) o reemplazar."""
    return [
        {"pin": p.name.removeprefix("llama.cpp-"), "size_mb": _dir_size_mb(p)}
        for p in _orphaned_runtime_dirs()
    ]


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
        "cuda_note": None,
        "error": None,
        "previous_builds": [],
    }
    if not platform_supported():
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
        status["cuda_note"] = (meta or {}).get("cuda_note")
        status["size_mb"] = _dir_size_mb(d)
    elif meta and meta.get("state") == "error":
        status["state"] = "error"
        status["error"] = meta.get("error")
    else:
        # El pin actual no está descargado: si queda una build gestionada
        # anterior en disco, el estado pasa de "missing" genérico a "outdated"
        # (RT-12) y la UI ofrece Actualizar / Reinstalar / Desinstalar.
        status["previous_builds"] = _previous_builds()
        if status["previous_builds"]:
            status["state"] = "outdated"
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
    if not platform_supported():
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
            # is_relative_to y no startswith de strings: un hermano cuyo nombre
            # empiece con el del target (out-evil vs out) pasaría el startswith
            # y escaparía del destino.
            if not dest.is_relative_to(target.resolve()):
                raise RuntimeError(f"Entrada de zip fuera del destino: {info.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def _extract_archive(archive: Path, target: Path) -> None:
    if archive.suffix == ".zip":
        _extract_zip(archive, target)
    else:
        _extract_targz(archive, target)


def _extract_targz(archive: Path, target: Path) -> None:
    """Extrae un .tar.gz a target; si todos los archivos comparten UNA folder
    raíz, la descarta (misma política que el zip). Conserva el bit executable
    de los entries (el binario llama-server lo trae puesto) y recrea los
    symlinks: los tarballs oficiales empaquetan los SONAME como symlinks
    (libllama-common.so.0 -> .so.0.5.0) y saltárselos rompe el loader
    dinámico con "cannot open shared object file" (RT-14)."""
    with tarfile.open(archive, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.isfile() or m.issym()]
        tops = {m.name.split("/", 1)[0] for m in members}
        prefix = tops.pop() + "/" if len(tops) == 1 else ""
        for m in members:
            rel = m.name[len(prefix):] if prefix else m.name
            if not rel:
                continue
            dest = (target / rel).resolve()
            if not dest.is_relative_to(target.resolve()):
                raise RuntimeError(f"Entrada de tarball fuera del destino: {m.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if m.issym():
                ln = m.linkname
                if ln.startswith("/") or ".." in ln.split("/"):
                    raise RuntimeError(f"Symlink de tarball fuera del destino: {m.name}")
                os.symlink(ln, dest)
                continue
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            if m.mode & 0o111:
                dest.chmod(0o755)


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
    """¿Se puede descargar el runtime? (plataforma soportada — Windows x64 o
    Linux x64 — + fuente BASE verificable; la aceleración es opcional y se
    decide por familia en el download)."""
    return platform_supported() and _select_base_source() is not None


async def download_runtime(on_progress=None, keep_previous: bool = False) -> dict:
    """Descarga y verifica el runtime del pin activo en 2 etapas (acto explícito).

    keep_previous=False (default) borra las builds gestionadas anteriores
    (llama.cpp-*) al terminar: un bump de pin no deja la dir vieja huérfana
    (~700 MB duplicados). keep_previous=True las conserva (base del rollback,
    feature empresarial — ver PENDIENTES).

    1. Motor BASE (0-50 %): siempre. Un fallo aquí ES error.
    2. Aceleración de la familia detectada (50-100 %): solo si existe
       paquete verificable para la familia (fase A: nvidia). Un fallo aquí
       NO rompe el runtime: queda degradado a CPU (meta con accel_error) y
       la descarga termina en "ready".

    on_progress(pct: float 0-100, detail: str) — opcional.
    """
    global _downloading
    if not platform_supported():
        raise RuntimeError(
            "El runtime gestionado soporta Windows x64 y Linux x64 (v1)"
        )
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
        base_files = _platform_files(base["files"])
        await _download_stage(base_files, work, on_progress, 0.0, 50.0)
        target = runtime_dir()
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        on_progress(50, "Extrayendo motor base")
        for archive in sorted(work.glob("*.zip")) + sorted(work.glob("*.tar.gz")):
            _extract_archive(archive, target)
            archive.unlink()
        if not (target / BINARY_NAME).exists():
            raise RuntimeError(f"El binario {BINARY_NAME} no está en el archive base")
        # En Linux el tarball lo trae; se garantiza igual (no hace daño en
        # Windows, donde solo afecta el flag de solo-lectura).
        (target / BINARY_NAME).chmod(0o755)
        # Etapa 2: aceleración (degradación elegante, nunca un error).
        accel_id = None
        accel_error = None
        if accel is not None:
            try:
                await _download_stage(_platform_files(accel["files"]), work, on_progress, 50.0, 100.0)
                on_progress(100, "Extrayendo aceleración GPU")
                for archive in sorted(work.glob("*.zip")) + sorted(work.glob("*.tar.gz")):
                    _extract_archive(archive, target)
                    archive.unlink()
                accel_id = accel["id"]
            except BaseException as exc:
                # La cancelación cooperativa NO es un fallo de aceleración:
                # re-lanzar para no dejar la task viva contra su cancelación.
                if isinstance(exc, asyncio.CancelledError):
                    raise
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
            "cuda_note": _cuda_note() if accel_id else None,
            "date": datetime.now(timezone.utc).isoformat(),
            "size_mb": _dir_size_mb(target),
            "files": [Path(s["url"]).name for s in base_files]
                     + ([Path(s["url"]).name for s in _platform_files(accel["files"])]
                        if accel else []),
        })
        shutil.rmtree(work, ignore_errors=True)
        if not keep_previous:
            for orphan in _orphaned_runtime_dirs():
                logger.info(
                    "build anterior %s reemplazada por %s (cleanup de huérfanos)",
                    orphan.name, RUNTIME_PIN,
                )
                shutil.rmtree(orphan, ignore_errors=True)
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

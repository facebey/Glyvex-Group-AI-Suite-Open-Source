"""
hardware_detection.py — Foto estática de hardware (M6 / A2; diseño W2.1 en
Files/Docs/design/a2-hardware-detection.md).

Responsabilidades:
- Detectar CPU (vendor Intel/AMD, núcleos, frecuencia best-effort), RAM, GPUs
  con VRAM y disco libre en los model_dirs configurados. Todo local, sin red.
- Cada sonda es una función discrete y mockeable que nunca levanta: si falla,
  el campo queda None y se registra un motivo en `degraded`.
- NO reemplaza la telemetría en vivo de metrics.py (M5): es una foto
  best-effort para el motor de recomendaciones (hardware_recommend.py) y el
  panel "Detectado automáticamente" de Config.

Trampa que este módulo evita a propósito: platform.machine() == "AMD64" es la
ISA, no el vendor — un CPU Intel también reporta "AMD64". El vendor se lee del
env PROCESSOR_IDENTIFIER (Windows) o /proc/cpuinfo (Linux). psutil solo se usa
para conteos de núcleos y RAM, que sí son confiables.

VRAM AMD: vía el sidecar LibreHardwareMonitor ya empaquetado (misma política
que MON-6 en metrics.py), sin introducir rocm-smi. Para no spawnearel sidecar
en equipos sin AMD, primero se leen los video controllers del registro y LHM
solo se intenta si aparece alguna Radeon/AMD.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore[assignment]
    PYNVML_AVAILABLE = False

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    PSUTIL_AVAILABLE = False

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore[assignment]

import lhm
from config import config
from paths import DATA_DIR

logger = logging.getLogger("glyvex.hardware")

# TTL de la foto: los probes (registro, NVML, HTTP a LHM) no se repiten por
# cada request. El frontend fuerza una refresh con ?refresh=1 tras un cambio
# de hardware o al abrir el panel.
SNAPSHOT_TTL_S = 60.0

# Clase del registro con los video controllers (misma que runtime.py).
_VIDEO_CONTROLLER_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


# --------------------------------------------------------------------------
# Schema (Pydantic v2)
# --------------------------------------------------------------------------


class CpuInfo(BaseModel):
    vendor: str | None = None          # "intel" | "amd" | None (desconocido)
    model: str | None = None           # texto libre informativo (no se recomienda con esto)
    logical_cores: int | None = None
    physical_cores: int | None = None  # psutil puede no poder determinarlo (VMs)
    max_mhz: float | None = None       # best-effort; None si el platform no lo expone
    source: str = "none"               # "env" | "proc" | "cim" (por qué ruta se llegó)


class GpuInfo(BaseModel):
    index: int
    name: str
    vendor: str                        # "nvidia" | "amd" | "intel" | "other"
    vram_total_mb: float | None = None  # None = sin proveedor local fiable (Intel WDDM)
    vram_used_mb: float | None = None
    compute_capability: str | None = None  # "12.0" etc.; solo NVIDIA


class RamInfo(BaseModel):
    total_mb: float | None = None
    available_mb: float | None = None
    swap_total_mb: float | None = None


class DiskInfo(BaseModel):
    path: str
    free_mb: float | None = None
    total_mb: float | None = None


class HardwareSnapshot(BaseModel):
    detected_at: str
    cpu: CpuInfo | None = None
    ram: RamInfo | None = None
    gpus: list[GpuInfo] = Field(default_factory=list)
    disks: list[DiskInfo] = Field(default_factory=list)
    # Misma cascada de familias que runtime.detect_gpu_family(): nvidia|amd|intel|cpu
    gpu_family: str = "cpu"
    # Motivos de sondas que fallaron (frontend los muestra como datos faltantes).
    degraded: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------


def _cpu_vendor_from_identifier(identifier: str) -> str | None:
    """PROCESSOR_IDENTIFIER trae 'GenuineIntel' o 'AuthenticAMD' al final."""
    text = (identifier or "").lower()
    if "genuineintel" in text or "intel" in text:
        return "intel"
    if "authenticamd" in text:
        return "amd"
    return None


def _probe_cpu_windows() -> tuple[CpuInfo | None, str | None]:
    identifier = os.environ.get("PROCESSOR_IDENTIFIER", "")
    vendor = _cpu_vendor_from_identifier(identifier)
    if vendor is None:
        # El env no basta (equipos muy raros o cortados): único camino con
        # vendor + nombre es CIM (subprocess PowerShell, mockeable en tests).
        return _probe_cpu_cim()
    return (
        CpuInfo(vendor=vendor, model=None, source="env"),
        None,
    )


def _probe_cpu_cim() -> tuple[CpuInfo | None, str | None]:
    """Fallback: Win32_Processor da vendor y modelo donde el env no llega."""
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1 "
                "| ConvertTo-Json -Compress)",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None, "cpu.cim"
        data = json.loads(proc.stdout)
        if not isinstance(data, dict):
            return None, "cpu.cim"
        name = str(data.get("Name") or "").strip() or None
        vendor_id = str(data.get("Vendor") or "").lower()
        vendor = "intel" if "intel" in name.lower() or "intel" in vendor_id else \
                 "amd" if "amd" in name.lower() or "amd" in vendor_id else None
        max_mhz = data.get("MaxClockSpeed")
        return (
            CpuInfo(
                vendor=vendor,
                model=name,
                max_mhz=float(max_mhz) if max_mhz else None,
                source="cim",
            ),
            None if vendor else "cpu.vendor",
        )
    except Exception:
        return None, "cpu.cim"


def _probe_cpu_linux(cpuinfo_path: str = "/proc/cpuinfo") -> tuple[CpuInfo | None, str | None]:
    try:
        vendor: str | None = None
        model: str | None = None
        with open(cpuinfo_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key == "vendor_id" and value:
                    low = value.lower()
                    vendor = "intel" if "intel" in low else "amd" if "amd" in low else vendor
                elif key == "model name" and value and model is None:
                    model = value
        if vendor is None:
            return None, "cpu.vendor"
        return CpuInfo(vendor=vendor, model=model, source="proc"), None
    except OSError:
        return None, "cpu.proc"


def _probe_cpu() -> tuple[CpuInfo | None, str | None]:
    """Vendor + modelo por la vía barata sin subprocess; núcleos y frecuencia
    con psutil (best-effort). El orden importa: la vía primaria NUNCA spawnea."""
    system = platform.system()
    info: CpuInfo | None = None
    degraded: str | None = None
    if system == "Windows":
        info, degraded = _probe_cpu_windows()
    elif system == "Linux":
        info, degraded = _probe_cpu_linux()
    else:
        return None, "cpu.unsupported"
    if info is None:
        return None, degraded or "cpu"
    if psutil is not None:
        try:
            info.logical_cores = psutil.cpu_count(logical=True)
            info.physical_cores = psutil.cpu_count(logical=False)
        except Exception:
            pass
        if info.max_mhz is None:
            try:
                freq = psutil.cpu_freq()
                if freq and (freq.max or freq.current):
                    info.max_mhz = float(freq.max or freq.current)
            except Exception:
                pass
    if info.logical_cores is None:
        info.logical_cores = os.cpu_count()
    if info.physical_cores is not None and info.logical_cores is not None:
        info.physical_cores = min(info.physical_cores, info.logical_cores)
    return info, degraded


# --------------------------------------------------------------------------
# GPU
# --------------------------------------------------------------------------


def _gpu_vendor_from_name(name: str) -> str:
    low = (name or "").lower()
    if "nvidia" in low or "geforce" in low or "rtx" in low or "gtx" in low:
        return "nvidia"
    if "radeon" in low or "amd" in low:
        return "amd"
    if "intel" in low or "iris" in low or "arc" in low:
        return "intel"
    return "other"


def _video_controller_names() -> list[str]:
    """DriverDesc de todos los video controllers (sin subprocess). También
    sirve de gate para no spawnearel sidecar LHM en equipos sin AMD."""
    if winreg is None or platform.system() != "Windows":
        return []
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
        return []
    return names


# Sidecar LHM propio del módulo, perezoso: LhmSidecar adopta un sidecar que ya
# está sirviendo en 8185 (p. ej. el del Monitor), así que nunca hay dos procesos.
_lhm_sidecar: lhm.LhmSidecar | None = None


def _probe_gpus_nvml() -> tuple[list[GpuInfo], str | None]:
    if not PYNVML_AVAILABLE:
        return [], "gpu.nvml"
    try:
        try:
            pynvml.nvmlInit()
        except Exception:
            return [], "gpu.nvml"
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return [], "gpu.nvml"
        gpus: list[GpuInfo] = []
        for index in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            name = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
            mem = None
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            except Exception:
                pass
            capability = None
            try:
                major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                if major >= 0:
                    capability = f"{major}.{minor}"
            except Exception:
                pass
            gpus.append(GpuInfo(
                index=index,
                name=name,
                vendor="nvidia",
                vram_total_mb=round(mem.total / (1024 ** 2), 1) if mem else None,
                vram_used_mb=round(mem.used / (1024 ** 2), 1) if mem else None,
                compute_capability=capability,
            ))
        return gpus, None
    except Exception:
        return [], "gpu.nvml"


def _probe_gpus_amd_lhm() -> tuple[list[GpuInfo], str | None]:
    global _lhm_sidecar
    if not lhm.resource_available():
        return [], "gpu.lhm.unavailable"
    try:
        if _lhm_sidecar is None:
            _lhm_sidecar = lhm.LhmSidecar()
        entries, error = _lhm_sidecar.collect()
        if not entries:
            return [], "gpu.lhm"
        gpus = [
            GpuInfo(
                index=int(entry.get("index", 0)),
                name=str(entry.get("name") or "GPU AMD"),
                vendor="amd",
                # LHM trae vram_total_mb=0.0 cuando no expone el sensor: None
                # deja claro que no hay dato (vs. una GPU de 0 MB imposible).
                vram_total_mb=float(entry["vram_total_mb"]) or None,
                vram_used_mb=float(entry.get("vram_used_mb") or 0.0) or None,
            )
            for entry in entries
        ]
        return gpus, error
    except Exception:
        return [], "gpu.lhm"


def _probe_gpus() -> tuple[list[GpuInfo], str | None]:
    """NVML primero (NVIDIA con VRAM exacta). Sin NVIDIA, gate de registro:
    LHM solo si hay una AMD por nombre; Intel/otros quedan listados SIN VRAM
    (limitación documentada en el diseño A2)."""
    gpus, error = _probe_gpus_nvml()
    if gpus:
        return gpus, None

    controller_names = _video_controller_names()
    amd_present = any(_gpu_vendor_from_name(n) == "amd" for n in controller_names)
    if amd_present:
        amd_gpus, amd_error = _probe_gpus_amd_lhm()
        if amd_gpus:
            return amd_gpus, None
        # Gate AMD pero el sidecar no trajo VRAM: listar por nombre al menos.
        fallback = [
            GpuInfo(index=i, name=n, vendor="amd")
            for i, n in enumerate(controller_names) if _gpu_vendor_from_name(n) == "amd"
        ]
        return fallback, amd_error or "gpu.lhm"

    if controller_names:
        gpus = [
            GpuInfo(index=i, name=n, vendor=_gpu_vendor_from_name(n))
            for i, n in enumerate(controller_names)
        ]
        # Sin NVML y sin gate AMD: no hay proveedor local fiable de VRAM.
        return gpus, "gpu.vram.unknown"
    return [], error or "gpu.none"


# --------------------------------------------------------------------------
# RAM / disco
# --------------------------------------------------------------------------


def _probe_ram() -> tuple[RamInfo | None, str | None]:
    if psutil is None:
        return None, "ram.psutil"
    try:
        vm = psutil.virtual_memory()
        swap_total: float | None = None
        try:
            swap_total = round(psutil.swap_memory().total / (1024 ** 2), 1)
        except Exception:
            pass
        return (
            RamInfo(
                total_mb=round(vm.total / (1024 ** 2), 1),
                available_mb=round(vm.available / (1024 ** 2), 1),
                swap_total_mb=swap_total,
            ),
            None,
        )
    except Exception:
        return None, "ram"


def _probe_disks() -> tuple[list[DiskInfo], str | None]:
    """Disco libre en los model_dirs configurados (allí se cargan los GGUF).
    Sin model_dirs se sondea DATA_DIR, que es donde irían de todos modos."""
    raw_dirs = config.get("model_dirs", []) or []
    if not raw_dirs:
        raw_dirs = [str(DATA_DIR)]
    disks: list[DiskInfo] = []
    seen: set[str] = set()
    for raw in raw_dirs:
        try:
            probe_target = Path(raw)
            if not probe_target.exists():
                # Un model_dir borrado no invalida la foto: se usa su drive.
                probe_target = Path(probe_target.anchor)
            usage = shutil.disk_usage(probe_target)
        except Exception:
            continue
        key = str(probe_target.anchor)
        if key in seen:
            continue
        seen.add(key)
        disks.append(DiskInfo(
            path=str(raw),
            free_mb=round(usage.free / (1024 ** 2), 1),
            total_mb=round(usage.total / (1024 ** 2), 1),
        ))
    if not disks:
        return [], "disk"
    return disks, None


# --------------------------------------------------------------------------
# Composición con caché TTL
# --------------------------------------------------------------------------

_cache: tuple[float, HardwareSnapshot] | None = None


def _gpu_family(gpus: list[GpuInfo]) -> str:
    if any(g.vendor == "nvidia" for g in gpus):
        return "nvidia"
    if any(g.vendor == "amd" for g in gpus):
        return "amd"
    if any(g.vendor == "intel" for g in gpus):
        return "intel"
    return "cpu"


def detect_hardware(force: bool = False) -> HardwareSnapshot:
    """Compone las sondas. Ninguna levanta: un fallo deja el campo None y
    registra el motivo en `degraded` (la respuesta sigue siendo válida)."""
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < SNAPSHOT_TTL_S:
        return _cache[1]

    degraded: list[str] = []

    cpu, cpu_reason = _probe_cpu()
    if cpu is None:
        degraded.append(cpu_reason or "cpu")

    ram, ram_reason = _probe_ram()
    if ram is None:
        degraded.append(ram_reason or "ram")

    gpus, gpu_reason = _probe_gpus()
    if gpu_reason:
        degraded.append(gpu_reason)

    disks, disk_reason = _probe_disks()
    if not disks:
        degraded.append(disk_reason or "disk")

    snapshot = HardwareSnapshot(
        detected_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        cpu=cpu,
        ram=ram,
        gpus=gpus,
        disks=disks,
        gpu_family=_gpu_family(gpus),
        degraded=degraded,
    )
    _cache = (now, snapshot)
    return snapshot

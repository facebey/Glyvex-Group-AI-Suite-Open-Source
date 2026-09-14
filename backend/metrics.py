"""
metrics.py — Monitor de recursos de hardware en tiempo real (módulo M5).

Responsabilidades:
- Recolectar métricas de GPU (pynvml), CPU/RAM (psutil) y procesos del
  stack (llama-server, ollama) sin crashear si pynvml/psutil no están
  disponibles o no hay GPU NVIDIA — en ese caso el campo correspondiente
  viaja como null pero la respuesta sigue siendo 200.
- Mantener un buffer circular (collections.deque(maxlen=300)) alimentado
  por el WebSocket de streaming.
- WS /api/metrics/stream: emite un snapshot por segundo mientras haya al
  menos un cliente conectado, y detiene el loop cuando el último cliente
  se desconecta (nunca corre en el vacío).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

HISTORY_MAXLEN = 300
STREAM_INTERVAL_S = 1.0

TARGET_PROCESS_NAMES = ("llama-server", "ollama")

# --------------------------------------------------------------------------
# Imports opcionales: pynvml y psutil pueden no estar instalados, o puede no
# haber una GPU NVIDIA presente. Nada de esto debe crashear el módulo.
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Schema (Pydantic v2)
# --------------------------------------------------------------------------


class GpuMetrics(BaseModel):
    index: int
    name: str
    vram_used_mb: float
    vram_total_mb: float
    vram_free_mb: float
    vram_percent: float
    gpu_utilization: int | None = None
    memory_utilization: int | None = None
    temperature_c: int | None = None
    power_draw_w: float | None = None
    power_limit_w: float | None = None
    clock_graphics_mhz: int | None = None
    clock_memory_mhz: int | None = None


class CpuMetrics(BaseModel):
    percent_per_core: list[float] = Field(default_factory=list)
    percent_total: float = 0.0
    frequency_mhz: float | None = None
    temperature_c: float | None = None


class RamMetrics(BaseModel):
    used_gb: float
    total_gb: float
    available_gb: float
    percent: float
    swap_used_gb: float = 0.0
    swap_total_gb: float = 0.0


class ProcessMetrics(BaseModel):
    pid: int
    name: str
    cpu_percent: float
    ram_rss_mb: float
    threads: int
    uptime_s: float


class MetricsSnapshot(BaseModel):
    timestamp: str
    gpu: list[GpuMetrics] | None = None
    gpu_error: str | None = None
    cpu: CpuMetrics | None = None
    cpu_error: str | None = None
    ram: RamMetrics | None = None
    processes: list[ProcessMetrics] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Recolección de métricas
# --------------------------------------------------------------------------


class MetricsCollector:
    def __init__(self) -> None:
        self._nvml_ready = False
        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._nvml_ready = True
            except Exception:
                self._nvml_ready = False

        if PSUTIL_AVAILABLE:
            # cpu_percent (global y per-core) necesita una primera llamada
            # "de referencia" para que las siguientes devuelvan deltas reales
            # en vez de 0.0.
            try:
                psutil.cpu_percent(percpu=True)
                psutil.cpu_percent()
                for proc in psutil.process_iter(["pid"]):
                    try:
                        proc.cpu_percent()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception:
                pass

    def __del__(self) -> None:
        if self._nvml_ready:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass

    # -- GPU -------------------------------------------------------------

    @staticmethod
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    def collect_gpu(self) -> tuple[list[GpuMetrics] | None, str | None]:
        if not PYNVML_AVAILABLE:
            return None, "pynvml no está instalado"
        if not self._nvml_ready:
            return None, "No se pudo inicializar NVML (¿hay una GPU NVIDIA?)"

        try:
            count = pynvml.nvmlDeviceGetCount()
        except Exception as exc:
            return None, f"Error consultando GPUs: {exc}"

        if count == 0:
            return None, "GPU NVIDIA no detectada"

        gpus: list[GpuMetrics] = []
        for index in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)

            name = self._safe(lambda: pynvml.nvmlDeviceGetName(handle), b"GPU")
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")

            mem = self._safe(lambda: pynvml.nvmlDeviceGetMemoryInfo(handle))
            util = self._safe(lambda: pynvml.nvmlDeviceGetUtilizationRates(handle))
            temp = self._safe(lambda: pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
            power_draw = self._safe(lambda: pynvml.nvmlDeviceGetPowerUsage(handle))
            power_limit = self._safe(lambda: pynvml.nvmlDeviceGetPowerManagementLimit(handle))
            clock_g = self._safe(lambda: pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS))
            clock_m = self._safe(lambda: pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM))

            vram_total_mb = round(mem.total / (1024**2), 1) if mem else 0.0
            vram_used_mb = round(mem.used / (1024**2), 1) if mem else 0.0
            vram_free_mb = round(mem.free / (1024**2), 1) if mem else 0.0
            vram_percent = round((mem.used / mem.total) * 100, 1) if mem and mem.total else 0.0

            gpus.append(GpuMetrics(
                index=index,
                name=name,
                vram_used_mb=vram_used_mb,
                vram_total_mb=vram_total_mb,
                vram_free_mb=vram_free_mb,
                vram_percent=vram_percent,
                gpu_utilization=util.gpu if util else None,
                memory_utilization=util.memory if util else None,
                temperature_c=temp,
                power_draw_w=round(power_draw / 1000, 1) if power_draw is not None else None,
                power_limit_w=round(power_limit / 1000, 1) if power_limit is not None else None,
                clock_graphics_mhz=clock_g,
                clock_memory_mhz=clock_m,
            ))

        return gpus, None

    # -- CPU / RAM ---------------------------------------------------------

    def collect_cpu(self) -> tuple[CpuMetrics | None, str | None]:
        if not PSUTIL_AVAILABLE:
            return None, "psutil no está instalado"
        try:
            per_core = psutil.cpu_percent(percpu=True)
            total = psutil.cpu_percent()
            freq = self._safe(lambda: psutil.cpu_freq())
            temp = self._read_cpu_temperature()
            return CpuMetrics(
                percent_per_core=per_core,
                percent_total=total,
                frequency_mhz=round(freq.current, 0) if freq else None,
                temperature_c=temp,
            ), None
        except Exception as exc:
            return None, f"Error leyendo CPU: {exc}"

    @staticmethod
    def _read_cpu_temperature() -> float | None:
        # sensors_temperatures() solo existe en Linux y puede no estar
        # implementada en todas las plataformas/kernels.
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is None:
            return None
        try:
            sensors = getter()
        except Exception:
            return None
        for label in ("coretemp", "k10temp", "cpu_thermal", "zenpower"):
            entries = sensors.get(label)
            if entries:
                return round(entries[0].current, 1)
        # Fallback: cualquier sensor disponible.
        for entries in sensors.values():
            if entries:
                return round(entries[0].current, 1)
        return None

    def collect_ram(self) -> RamMetrics | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return RamMetrics(
                used_gb=round(vm.used / (1024**3), 2),
                total_gb=round(vm.total / (1024**3), 2),
                available_gb=round(vm.available / (1024**3), 2),
                percent=vm.percent,
                swap_used_gb=round(swap.used / (1024**3), 2),
                swap_total_gb=round(swap.total / (1024**3), 2),
            )
        except Exception:
            return None

    # -- Procesos del stack -------------------------------------------------

    def collect_processes(self) -> list[ProcessMetrics]:
        if not PSUTIL_AVAILABLE:
            return []
        results: list[ProcessMetrics] = []
        now = time.time()
        try:
            for proc in psutil.process_iter(["pid", "name", "num_threads", "create_time"]):
                try:
                    info = proc.info
                    name = (info.get("name") or "").lower()
                    if not any(target in name for target in TARGET_PROCESS_NAMES):
                        continue
                    rss_bytes = proc.memory_info().rss
                    results.append(ProcessMetrics(
                        pid=info["pid"],
                        name=info.get("name") or "",
                        cpu_percent=proc.cpu_percent(),
                        ram_rss_mb=round(rss_bytes / (1024**2), 1),
                        threads=info.get("num_threads") or 0,
                        uptime_s=round(now - info["create_time"], 1) if info.get("create_time") else 0.0,
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            pass
        return results

    # -- Snapshot completo ---------------------------------------------------

    def snapshot(self) -> MetricsSnapshot:
        gpu, gpu_error = self.collect_gpu()
        cpu, cpu_error = self.collect_cpu()
        ram = self.collect_ram()
        processes = self.collect_processes()
        return MetricsSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            gpu=gpu, gpu_error=gpu_error,
            cpu=cpu, cpu_error=cpu_error,
            ram=ram,
            processes=processes,
        )


# --------------------------------------------------------------------------
# Manager: buffer circular + fan-out por WebSocket
# --------------------------------------------------------------------------


class MetricsManager:
    def __init__(self) -> None:
        self.collector = MetricsCollector()
        self.history: deque[MetricsSnapshot] = deque(maxlen=HISTORY_MAXLEN)
        self._clients: set[WebSocket] = set()
        self._broadcast_task: asyncio.Task | None = None

    def record_snapshot(self) -> MetricsSnapshot:
        snap = self.collector.snapshot()
        self.history.append(snap)
        return snap

    async def _broadcast_loop(self) -> None:
        try:
            while self._clients:
                snap = self.record_snapshot()
                payload = snap.model_dump_json()
                dead: list[WebSocket] = []
                for ws in list(self._clients):
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self._clients.discard(ws)
                await asyncio.sleep(STREAM_INTERVAL_S)
        finally:
            self._broadcast_task = None

    def add_client(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    def remove_client(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        # No hace falta cancelar el Task explícitamente: la próxima iteración
        # del loop ve self._clients vacío y termina solo (ver _broadcast_loop).


manager = MetricsManager()

# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("/snapshot", response_model=MetricsSnapshot)
async def get_snapshot() -> MetricsSnapshot:
    # record_snapshot (no collector.snapshot() directo) para que cada snapshot
    # puntual también quede en el buffer circular de /api/metrics/history.
    return manager.record_snapshot()


@router.get("/gpu")
async def get_gpu() -> dict[str, Any]:
    gpu, error = manager.collector.collect_gpu()
    return {"gpu": [g.model_dump() for g in gpu] if gpu else None, "error": error}


@router.get("/cpu")
async def get_cpu() -> dict[str, Any]:
    cpu, error = manager.collector.collect_cpu()
    ram = manager.collector.collect_ram()
    return {
        "cpu": cpu.model_dump() if cpu else None,
        "cpu_error": error,
        "ram": ram.model_dump() if ram else None,
    }


@router.get("/history", response_model=list[MetricsSnapshot])
async def get_history() -> list[MetricsSnapshot]:
    return list(manager.history)


@router.websocket("/stream")
async def metrics_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    manager.add_client(websocket)
    try:
        while True:
            # Este WebSocket es de push únicamente (el server empuja
            # snapshots desde _broadcast_loop); este receive_text() solo
            # existe para detectar la desconexión del cliente.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_client(websocket)

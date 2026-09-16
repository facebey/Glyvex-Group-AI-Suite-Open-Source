"""
metrics.py — Monitor de recursos de hardware en tiempo real (módulo M5).

Responsabilidades:
- Recolectar métricas de GPU (pynvml), CPU/RAM (psutil) y procesos del
  stack (llama-server, ollama) sin crashear si pynvml/psutil no están
  disponibles o no hay GPU NVIDIA — en ese caso el campo correspondiente
  viaja como null pero la respuesta sigue siendo 200.
- Mantener un buffer circular (collections.deque(maxlen=300)) alimentado
  por el WebSocket de streaming.
- WS /api/metrics/stream: emite un snapshot por segundo a los clientes
  conectados.

MetricsService (histórico persistente):
- `manager.start()` se llama desde el lifespan de main.py y deja el poller
  corriendo en segundo plano aunque nadie mire el Monitor: así el dashboard
  tiene histórico real. Sin start() (tests, o monitor.history_enabled=false)
  el loop se comporta como antes: corre solo mientras haya clientes WS.
- Cada snapshot se aplana en series ("gpu.0.temp_c", "cpu.total_pct"...),
  se agrega en ventanas de 5 s (promedio/mínimo/máximo) y se vuelca a
  data/metrics.db. Cada minuto se compacta a 1 min y 1 h y se aplica la
  retención (ver metrics_store.py).
- GET /api/metrics/series y /api/metrics/query leen ese histórico eligiendo
  la resolución según el rango pedido.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from config import config
from metrics_store import RAW_STEP_S, MetricsStore, Retention, SampleWindow

logger = logging.getLogger("glyvex.metrics")

HISTORY_MAXLEN = 300
STREAM_INTERVAL_S = 1.0
MAINTENANCE_INTERVAL_S = 60.0

BASE_DIR = Path(__file__).resolve().parent.parent
METRICS_DB_PATH = BASE_DIR / "data" / "metrics.db"

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
# Series: aplanado del snapshot y agregación en ventanas
# --------------------------------------------------------------------------


def flatten_snapshot(snap: MetricsSnapshot) -> tuple[dict[str, float], dict[str, str]]:
    """
    Valores numéricos del snapshot como series planas, más la unidad de cada
    una. Los campos en null (sensor no disponible) simplemente no aparecen:
    una serie sin datos es un hueco en el gráfico, no un cero falso.

    Los procesos no se guardan: su pid cambia en cada lanzamiento y el
    histórico útil de un servidor LLM llega con sus propias métricas.
    """
    values: dict[str, float] = {}
    units: dict[str, str] = {}

    def put(key: str, value: float | int | None, unit: str) -> None:
        if value is None:
            return
        values[key] = float(value)
        units[key] = unit

    for gpu in snap.gpu or []:
        prefix = f"gpu.{gpu.index}"
        put(f"{prefix}.vram_used_mb", gpu.vram_used_mb, "MB")
        put(f"{prefix}.vram_pct", gpu.vram_percent, "%")
        put(f"{prefix}.util_pct", gpu.gpu_utilization, "%")
        put(f"{prefix}.mem_util_pct", gpu.memory_utilization, "%")
        put(f"{prefix}.temp_c", gpu.temperature_c, "°C")
        put(f"{prefix}.power_w", gpu.power_draw_w, "W")

    if snap.cpu:
        put("cpu.total_pct", snap.cpu.percent_total, "%")
        put("cpu.freq_mhz", snap.cpu.frequency_mhz, "MHz")
        put("cpu.temp_c", snap.cpu.temperature_c, "°C")

    if snap.ram:
        put("ram.used_gb", snap.ram.used_gb, "GB")
        put("ram.pct", snap.ram.percent, "%")
        put("swap.used_gb", snap.ram.swap_used_gb, "GB")

    return values, units


class WindowAggregator:
    """
    Acumula muestras de 1 s en ventanas alineadas a RAW_STEP_S y devuelve las
    ventanas cerradas con promedio, mínimo y máximo. Alinear al reloj (y no
    al arranque) hace que dos arranques no generen ventanas solapadas.
    """

    def __init__(self, step_s: int = RAW_STEP_S) -> None:
        self.step_s = step_s
        # {window_ts: {key: [sum, count, min, max]}}
        self._open: dict[int, dict[str, list[float]]] = {}
        self.units: dict[str, str] = {}

    def add(self, ts: float, values: dict[str, float], units: dict[str, str]) -> None:
        window = int(ts // self.step_s) * self.step_s
        bucket = self._open.setdefault(window, {})
        for key, value in values.items():
            acc = bucket.get(key)
            if acc is None:
                bucket[key] = [value, 1, value, value]
            else:
                acc[0] += value
                acc[1] += 1
                acc[2] = min(acc[2], value)
                acc[3] = max(acc[3], value)
        self.units.update(units)

    @property
    def has_open_windows(self) -> bool:
        """Hay ventanas sin cerrar (llm_metrics lo usa para no soltar agregadores a medio flush)."""
        return bool(self._open)

    def pop_closed(self, now: float, *, force: bool = False) -> list[SampleWindow]:
        closed: list[SampleWindow] = []
        for window in sorted(self._open):
            if not force and now < window + self.step_s:
                continue
            for key, (total, count, mn, mx) in self._open.pop(window).items():
                closed.append(SampleWindow(key=key, ts=window, avg=total / count, min=mn, max=mx))
        return closed


# --------------------------------------------------------------------------
# Manager: buffer circular, fan-out por WebSocket y persistencia
# --------------------------------------------------------------------------


def _retention_from_config() -> Retention:
    return Retention(
        raw_s=int(config.get("monitor.retention_raw_h", 48)) * 3600,
        m1_s=int(config.get("monitor.retention_1m_d", 30)) * 86400,
        h1_s=int(config.get("monitor.retention_1h_d", 365)) * 86400,
    )


class MetricsManager:
    def __init__(self) -> None:
        self.collector = MetricsCollector()
        self.history: deque[MetricsSnapshot] = deque(maxlen=HISTORY_MAXLEN)
        self._clients: set[WebSocket] = set()
        self._broadcast_task: asyncio.Task | None = None
        # Servicio de fondo: True entre start() y stop().
        self._running = False
        self.store: MetricsStore | None = None
        self._aggregator = WindowAggregator()
        self._last_maintenance = 0.0

    # -- snapshot ---------------------------------------------------------------

    def record_snapshot(self) -> MetricsSnapshot:
        snap = self.collector.snapshot()
        self.history.append(snap)
        return snap

    # -- servicio de fondo --------------------------------------------------------

    def configure_store(self, path: Path | str) -> MetricsStore:
        """Abre (o reemplaza) el store. Los tests lo apuntan a tmp_path."""
        if self.store is not None:
            self.store.close()
        self.store = MetricsStore(path, _retention_from_config())
        self.store.open()
        self._aggregator = WindowAggregator()
        return self.store

    async def start(self, db_path: Path | str | None = None) -> None:
        if self._running:
            return
        if not bool(config.get("monitor.history_enabled", True)):
            logger.info("histórico de métricas deshabilitado (monitor.history_enabled=false)")
            return
        try:
            await asyncio.to_thread(self.configure_store, db_path or METRICS_DB_PATH)
        except Exception as exc:  # noqa: BLE001 — sin histórico, el Monitor en vivo sigue andando
            logger.warning("no se pudo abrir la base de métricas: %s", exc)
            self.store = None
            return
        self._running = True
        self._ensure_loop()
        logger.info("servicio de métricas iniciado: %s", self.store.path)

    async def stop(self) -> None:
        self._running = False
        task = self._broadcast_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._broadcast_task = None
        if self.store is not None:
            store = self.store
            # Lo que quedó en ventanas abiertas también se guarda.
            try:
                pending = self._aggregator.pop_closed(time.time(), force=True)
                await asyncio.to_thread(store.write_raw, pending, units=self._aggregator.units)
            except Exception as exc:  # noqa: BLE001
                logger.warning("no se pudieron guardar las últimas métricas: %s", exc)
            await asyncio.to_thread(store.close)
            self.store = None

    async def _persist(self, snap: MetricsSnapshot, now: float) -> None:
        store = self.store
        if store is None:
            return
        values, units = flatten_snapshot(snap)
        self._aggregator.add(now, values, units)
        closed = self._aggregator.pop_closed(now)
        if closed:
            await asyncio.to_thread(store.write_raw, closed, units=self._aggregator.units)

        if now - self._last_maintenance >= MAINTENANCE_INTERVAL_S:
            self._last_maintenance = now
            store.retention = _retention_from_config()
            ts = int(now)
            await asyncio.to_thread(store.rollup, ts)
            await asyncio.to_thread(store.purge, ts)

    async def _broadcast_loop(self) -> None:
        try:
            while self._running or self._clients:
                started = time.monotonic()
                # psutil y NVML son llamadas bloqueantes: fuera del event loop.
                snap = await asyncio.to_thread(self.record_snapshot)

                if self._clients:
                    payload = snap.model_dump_json()
                    dead: list[WebSocket] = []
                    for ws in list(self._clients):
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self._clients.discard(ws)

                if self._running:
                    try:
                        await self._persist(snap, time.time())
                    except Exception as exc:  # noqa: BLE001 — un error de disco no corta el Monitor
                        logger.warning("error guardando métricas: %s", exc)

                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, STREAM_INTERVAL_S - elapsed))
        finally:
            self._broadcast_task = None

    def _ensure_loop(self) -> None:
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    def add_client(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)
        self._ensure_loop()

    def remove_client(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        # Sin servicio de fondo, la próxima iteración ve self._clients vacío y
        # el loop termina solo (ver _broadcast_loop).


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


@router.get("/series")
async def list_series(scope: str | None = None) -> dict[str, Any]:
    """Series con histórico guardado y el rango de fechas que cubre cada una."""
    store = manager.store
    if store is None:
        return {"enabled": False, "series": []}
    return {"enabled": True, "series": await asyncio.to_thread(store.list_series, scope)}


@router.get("/query")
async def query_series(
    key: list[str] = Query(..., description="Una o más series, ej. key=gpu.0.temp_c&key=cpu.total_pct"),
    start: int | None = Query(None, description="Epoch en segundos; por defecto, hace 1 hora"),
    end: int | None = Query(None, description="Epoch en segundos; por defecto, ahora"),
    process_id: str = "",
    max_points: int = 600,
) -> dict[str, Any]:
    """
    Histórico de una o varias series. La resolución la elige el backend según
    el rango (5 s, 1 min o 1 h) para devolver como mucho `max_points` puntos
    por serie; cada punto trae promedio, mínimo y máximo.
    """
    store = manager.store
    now = int(time.time())
    end = end if end is not None else now
    start = start if start is not None else end - 3600
    if store is None:
        return {"enabled": False, "tier": None, "resolution_s": None,
                "series": {k: [] for k in key}}
    result = await asyncio.to_thread(
        store.query, key, start, end, now=now, process_id=process_id, max_points=max_points
    )
    return {"enabled": True, "start": start, "end": end, **result}


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

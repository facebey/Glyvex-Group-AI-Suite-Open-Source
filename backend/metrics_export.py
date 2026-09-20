"""
metrics_export.py — Salidas opcionales de métricas: Prometheus e InfluxDB.

Las dos se apagan y prenden desde Config (sección `exports`) y exportan TODAS
las métricas, también las que el usuario ocultó en pantalla: lo que se ve y
lo que se manda a Grafana son decisiones distintas.

Prometheus (pull):
    GET /api/metrics/prometheus — formato de exposición de texto. Toma la
    última lectura de hardware y la de cada llama-server en ejecución. Apagado,
    responde 404; con `prometheus_token`, exige "Authorization: Bearer <token>".

InfluxDB (push):
    Recibe las mismas ventanas de 5 s (promedio/mínimo/máximo) que guarda el
    histórico, en line protocol:

        glyvex_hw,series=gpu.0.temp_c,unit=°C avg=65.2,min=64,max=68 1789525000
        glyvex_llm,series=llm.tg_tps,process_id=…,model=…,unit=t/s avg=… 1789525000

    Una cola acotada acumula las líneas; si el destino no responde, reintenta
    con espera creciente (hasta 5 min) y avisa en el log una vez por racha de
    fallos. Sirve para InfluxDB 1.x (/write), 2.x y 3 (/api/v2/write),
    VictoriaMetrics y QuestDB.

Este módulo no importa metrics ni llm_metrics (ellos lo importan para
encolar): el endpoint de Prometheus los importa recién al atender el request.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from collections import deque
from datetime import datetime
from typing import Any, Iterable

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from config import config

logger = logging.getLogger("glyvex.metrics_export")

INFLUX_MAX_QUEUE = 50_000
INFLUX_BATCH = 5_000
INFLUX_MAX_BACKOFF_S = 300.0
INFLUX_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
INFLUX_TOKEN_ENV = "GLYVEX_INFLUX_TOKEN"


# --------------------------------------------------------------------------
# Line protocol
# --------------------------------------------------------------------------


def _escape_measurement(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _escape_tag(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=")
        .replace(" ", "\\ ").replace("\n", "\\n")
    )


def _format_float(value: float) -> str:
    # repr da la representación más corta que vuelve al mismo float.
    return repr(float(value))


def windows_to_lines(
    windows: Iterable[Any],
    *,
    measurement: str,
    units: dict[str, str],
    tags: dict[str, str | None] | None = None,
) -> list[str]:
    """
    Ventanas (key, ts, avg, min, max) a líneas de line protocol. Los tags con
    valor vacío se omiten: InfluxDB rechaza tags vacíos.
    """
    extra = {k: v for k, v in (tags or {}).items() if v}
    lines: list[str] = []
    for w in windows:
        line_tags = {"series": w.key, **extra}
        unit = units.get(w.key)
        if unit:
            line_tags["unit"] = unit
        tag_str = ",".join(f"{_escape_tag(k)}={_escape_tag(str(v))}" for k, v in line_tags.items())
        fields = f"avg={_format_float(w.avg)},min={_format_float(w.min)},max={_format_float(w.max)}"
        lines.append(f"{_escape_measurement(measurement)},{tag_str} {fields} {int(w.ts)}")
    return lines


def build_write_request(influx: dict[str, Any]) -> dict[str, Any]:
    """
    URL, parámetros y headers para escribir con la versión elegida. El token
    de la variable de entorno tiene prioridad sobre el de config.json.
    """
    base = str(influx.get("url") or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("la URL de InfluxDB tiene que empezar con http:// o https://")
    version = influx.get("version") or "v2"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    auth = None
    if version == "v1":
        params = {"db": influx.get("database") or "", "precision": "s"}
        if not params["db"]:
            raise ValueError("falta el nombre de la base (database) para InfluxDB 1.x")
        if influx.get("username"):
            auth = (influx.get("username") or "", influx.get("password") or "")
        url = f"{base}/write"
    else:
        params = {"org": influx.get("org") or "", "bucket": influx.get("bucket") or "", "precision": "s"}
        if not params["bucket"]:
            raise ValueError("falta el bucket para InfluxDB 2.x/3")
        token = os.environ.get(INFLUX_TOKEN_ENV) or influx.get("token") or ""
        if token:
            headers["Authorization"] = f"Token {token}"
        url = f"{base}/api/v2/write"
    return {"url": url, "params": params, "headers": headers, "auth": auth}


# --------------------------------------------------------------------------
# Exportador a InfluxDB
# --------------------------------------------------------------------------


class InfluxExporter:
    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self._task: asyncio.Task | None = None
        self._running = False
        self._failures = 0
        self._next_attempt = 0.0
        self._failing = False
        self._overflow_warned = False
        self.sent_lines = 0

    # -- estado -----------------------------------------------------------------

    @staticmethod
    def settings() -> dict[str, Any]:
        value = config.get("exports.influx", {})
        return value if isinstance(value, dict) else {}

    def enabled(self) -> bool:
        return bool(self.settings().get("enabled", False))

    @property
    def queued(self) -> int:
        return len(self._queue)

    # -- entrada ------------------------------------------------------------------

    def enqueue(
        self,
        windows: Iterable[Any],
        *,
        scope: str,
        units: dict[str, str],
        process_id: str = "",
        model_name: str | None = None,
    ) -> int:
        if not self.enabled():
            return 0
        prefix = str(self.settings().get("measurement_prefix") or "glyvex")
        lines = windows_to_lines(
            windows, measurement=f"{prefix}_{scope}", units=units,
            tags={"process_id": process_id, "model": model_name},
        )
        if not lines:
            return 0
        overflow = len(self._queue) + len(lines) - INFLUX_MAX_QUEUE
        if overflow > 0:
            for _ in range(min(overflow, len(self._queue))):
                self._queue.popleft()
            if not self._overflow_warned:
                logger.warning(
                    "cola de InfluxDB llena (%d líneas): se descartan las más viejas hasta "
                    "que el destino vuelva a aceptar escrituras", INFLUX_MAX_QUEUE,
                )
                self._overflow_warned = True
        self._queue.extend(lines)
        return len(lines)

    # -- envío ----------------------------------------------------------------

    async def flush_once(self, client: httpx.AsyncClient | None = None) -> bool:
        """Envía un lote. Las líneas salen de la cola solo si el destino las aceptó."""
        if not self._queue:
            return True
        batch = [self._queue[i] for i in range(min(INFLUX_BATCH, len(self._queue)))]
        own_client = client is None
        client = client or httpx.AsyncClient(timeout=INFLUX_TIMEOUT)
        problem: str | None = None
        try:
            req = build_write_request(self.settings())
            res = await client.post(
                req["url"], params=req["params"], headers=req["headers"],
                auth=req["auth"], content="\n".join(batch).encode("utf-8"),
            )
            if 200 <= res.status_code < 300:
                for _ in batch:
                    self._queue.popleft()
                self.sent_lines += len(batch)
                if self._failing:
                    logger.info("InfluxDB vuelve a aceptar escrituras (%d líneas en cola)", len(self._queue))
                self._failing = False
                self._failures = 0
                self._overflow_warned = False
                self._next_attempt = 0.0
                return True
            problem = f"HTTP {res.status_code}: {res.text[:200]}"
        except ValueError as exc:
            problem = str(exc)
        except httpx.HTTPError as exc:
            problem = f"{type(exc).__name__}: {exc}"
        finally:
            if own_client:
                await client.aclose()

        self._failures += 1
        interval = float(self.settings().get("interval_s") or 10)
        delay = min(interval * 2 ** (self._failures - 1), INFLUX_MAX_BACKOFF_S)
        self._next_attempt = time.monotonic() + delay
        if not self._failing:
            logger.warning(
                "no se pudo escribir en InfluxDB (%s): se reintenta con espera creciente, "
                "las líneas quedan en cola", problem,
            )
        self._failing = True
        return False

    async def _loop(self) -> None:
        client = httpx.AsyncClient(timeout=INFLUX_TIMEOUT)
        try:
            while self._running:
                interval = float(self.settings().get("interval_s") or 10)
                if self.enabled() and self._queue and time.monotonic() >= self._next_attempt:
                    try:
                        # Un envío exitoso sigue con el próximo lote sin esperar
                        # (vaciar lo acumulado tras una caída).
                        while self._queue and await self.flush_once(client):
                            if len(self._queue) < INFLUX_BATCH:
                                break
                    except Exception as exc:  # noqa: BLE001 — el exportador no tumba la app
                        logger.warning("error inesperado exportando a InfluxDB: %s", exc)
                elif not self.enabled() and self._queue:
                    # Se apagó desde Config: lo pendiente ya no tiene destino.
                    self._queue.clear()
                await asyncio.sleep(max(1.0, interval))
        finally:
            await client.aclose()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        if self.enabled() and self._queue:
            try:
                await asyncio.wait_for(self.flush_once(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass


exporter = InfluxExporter()


# --------------------------------------------------------------------------
# Prometheus
# --------------------------------------------------------------------------


def _escape_label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Exposition:
    def __init__(self) -> None:
        self._families: dict[str, tuple[str, str, list[str]]] = {}

    def add(self, name: str, help_text: str, kind: str, value: float | int | None,
            labels: dict[str, object] | None = None) -> None:
        if value is None:
            return
        fam = self._families.setdefault(name, (help_text, kind, []))
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{_escape_label(v)}"' for k, v in labels.items()) + "}"
        fam[2].append(f"{name}{label_str} {_format_float(value)}")

    def render(self) -> str:
        out: list[str] = []
        for name, (help_text, kind, samples) in self._families.items():
            out.append(f"# HELP {name} {help_text}")
            out.append(f"# TYPE {name} {kind}")
            out.extend(samples)
        return "\n".join(out) + "\n"


MB = 1024 * 1024
GB = 1024 ** 3


def prometheus_text(hw: Any | None, llm_snaps: list[Any]) -> str:
    """Formato de exposición de Prometheus para un snapshot de hardware y los del LLM."""
    e = _Exposition()
    e.add("glyvex_up", "1 si el backend de Glyvex responde", "gauge", 1)

    if hw is not None:
        for gpu in hw.gpu or []:
            lb = {"gpu": gpu.index, "name": gpu.name}
            e.add("glyvex_gpu_memory_used_bytes", "VRAM en uso", "gauge",
                  gpu.vram_used_mb * MB if gpu.vram_used_mb is not None else None, lb)
            e.add("glyvex_gpu_memory_total_bytes", "VRAM total", "gauge",
                  gpu.vram_total_mb * MB if gpu.vram_total_mb is not None else None, lb)
            e.add("glyvex_gpu_utilization_ratio", "Utilización de la GPU (0-1)", "gauge",
                  gpu.gpu_utilization / 100 if gpu.gpu_utilization is not None else None, lb)
            e.add("glyvex_gpu_temperature_celsius", "Temperatura de la GPU", "gauge", gpu.temperature_c, lb)
            e.add("glyvex_gpu_power_watts", "Consumo de la GPU", "gauge", gpu.power_draw_w, lb)
            e.add("glyvex_gpu_power_limit_watts", "Límite de potencia de la GPU", "gauge", gpu.power_limit_w, lb)
        if hw.cpu is not None:
            e.add("glyvex_cpu_utilization_ratio", "Utilización total de la CPU (0-1)", "gauge",
                  hw.cpu.percent_total / 100 if hw.cpu.percent_total is not None else None)
            e.add("glyvex_cpu_frequency_hertz", "Frecuencia actual de la CPU", "gauge",
                  hw.cpu.frequency_mhz * 1_000_000 if hw.cpu.frequency_mhz else None)
            e.add("glyvex_cpu_temperature_celsius", "Temperatura de la CPU", "gauge", hw.cpu.temperature_c)
        if hw.ram is not None:
            e.add("glyvex_memory_used_bytes", "RAM en uso", "gauge", hw.ram.used_gb * GB)
            e.add("glyvex_memory_total_bytes", "RAM total", "gauge", hw.ram.total_gb * GB)
            e.add("glyvex_swap_used_bytes", "Swap en uso", "gauge", hw.ram.swap_used_gb * GB)

    for s in llm_snaps:
        lb = {"process_id": s.process_id, "model": s.model_name}
        e.add("glyvex_llm_up", "1 si /metrics de llama-server respondió en la última lectura", "gauge",
              1 if s.scrape_ok else 0, lb)
        e.add("glyvex_llm_generation_tokens_per_second", "Velocidad de generación actual (sin valor si está ocioso)",
              "gauge", s.tg_tps, lb)
        e.add("glyvex_llm_generation_tokens_per_second_last", "Última velocidad de generación medida",
              "gauge", s.tg_tps_last, lb)
        e.add("glyvex_llm_prompt_tokens_per_second", "Velocidad de procesamiento de prompt", "gauge", s.pp_tps, lb)
        e.add("glyvex_llm_throughput_tokens_per_second", "Tokens generados por segundo de reloj", "gauge",
              s.throughput_tps, lb)
        e.add("glyvex_llm_context_tokens", "Tokens de contexto ocupados", "gauge", s.ctx_used, lb)
        e.add("glyvex_llm_context_size_tokens", "Tamaño de contexto configurado", "gauge", s.ctx_total, lb)
        e.add("glyvex_llm_context_peak_tokens", "Pico de contexto observado", "gauge", s.ctx_peak, lb)
        e.add("glyvex_llm_requests_processing", "Requests en curso", "gauge", s.requests_processing, lb)
        e.add("glyvex_llm_requests_deferred", "Requests esperando un slot", "gauge", s.requests_deferred, lb)
        e.add("glyvex_llm_prompt_cache_hit_ratio", "Proporción de tokens de prompt reutilizados del caché (0-1)",
              "gauge", s.cache_hit_pct_total / 100 if s.cache_hit_pct_total is not None else None, lb)
        e.add("glyvex_llm_speculative_accept_ratio", "Proporción de tokens del draft aceptados (0-1)",
              "gauge", s.spec_accept_pct_total / 100 if s.spec_accept_pct_total is not None else None, lb)
        e.add("glyvex_llm_tokens_predicted_total", "Tokens generados desde que arrancó el proceso", "counter",
              s.tokens_predicted_total, lb)
        e.add("glyvex_llm_prompt_tokens_total", "Tokens de prompt procesados desde que arrancó el proceso",
              "counter", s.prompt_tokens_total, lb)
    return e.render()


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return max(0.0, time.time() - datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

router = APIRouter()


def _check_prometheus_token(request: Request) -> None:
    expected = str(config.get("exports.prometheus_token", "") or "")
    if not expected:
        return
    header = request.headers.get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="token inválido")


@router.get("/prometheus", response_class=PlainTextResponse)
async def prometheus_endpoint(request: Request) -> PlainTextResponse:
    if not bool(config.get("exports.prometheus_enabled", False)):
        raise HTTPException(status_code=404, detail="exportación a Prometheus deshabilitada en Config")
    _check_prometheus_token(request)

    import llm_metrics
    import metrics

    hw = metrics.manager.history[-1] if metrics.manager.history else None
    age = _age_seconds(hw.timestamp) if hw is not None else None
    if hw is None or age is None or age > 5:
        # Sin poller en marcha (histórico apagado) o lectura vieja: se mide ahora.
        hw = await asyncio.to_thread(metrics.manager.collector.snapshot)

    llm_snaps = []
    for proc in llm_metrics.manager.targets():
        buf = llm_metrics.manager.buffers.get(proc.process_id)
        if buf:
            last = buf[-1]
            snap_age = _age_seconds(last.timestamp)
            if snap_age is not None and snap_age <= 30:
                llm_snaps.append(last)

    return PlainTextResponse(prometheus_text(hw, llm_snaps), media_type="text/plain; version=0.0.4; charset=utf-8")


class InfluxTestRequest(BaseModel):
    """Valores del formulario de Config, todavía sin guardar."""

    model_config = {"extra": "allow"}


@router.post("/export/influx/test")
async def influx_test(body: InfluxTestRequest) -> dict[str, Any]:
    settings = {**InfluxExporter.settings(), **body.model_dump()}
    try:
        req = build_write_request(settings)
    except ValueError as exc:
        return {"ok": False, "status_code": None, "detail": str(exc)}
    prefix = str(settings.get("measurement_prefix") or "glyvex")
    line = f"{_escape_measurement(prefix + '_test')},source=glyvex ok=1i {int(time.time())}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            res = await client.post(req["url"], params=req["params"], headers=req["headers"],
                                    auth=req["auth"], content=line.encode("utf-8"))
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "detail": f"no hubo respuesta: {type(exc).__name__}: {exc}"}
    ok = 200 <= res.status_code < 300
    detail = "escritura aceptada" if ok else (res.text[:300] or f"HTTP {res.status_code}")
    return {"ok": ok, "status_code": res.status_code, "detail": detail}

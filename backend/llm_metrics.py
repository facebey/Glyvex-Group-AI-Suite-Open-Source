"""
llm_metrics.py — Métricas en vivo e histórico de los procesos llama-server.

Mismo esqueleto que metrics.py:
- Un poller async lee `http://{host}:{port}/metrics` (formato Prometheus,
  habilitado con `--metrics` en launcher.py) de cada proceso llama_server en
  estado "running": cada 1 s con clientes WS conectados, cada 5 s en segundo
  plano (se despierta al instante al conectarse alguien).
- Buffer circular deque(maxlen=300) por process_id.
- WS /api/llm-metrics/{process_id}/stream emite los snapshots de ese proceso.
- Con `manager.start()` (lifespan de main.py) el poller corre en segundo
   plano aunque nadie mire, y guarda histórico en <DATA_DIR>/metrics.db a
   través del store de metrics_store.py (scope="llm", separado por
   process_id). Sin start()
  (tests) corre solo mientras haya clientes WS, como antes.

Particularidades de llama-server que definen el cálculo:
- `kv_cache_usage_ratio` y `kv_cache_tokens` fueron removidas en builds
  recientes. Se leen si existen; si no, el uso actual sale de GET /slots
  (ver abajo). El pico observado se informa siempre, marcado como pico:
  `n_tokens_max` en builds actuales (prompt + generación), `n_past_max` en
  anteriores.
- `prompt_tokens_total` excluye los tokens reutilizados del caché, que van en
  `prompt_tokens_cached_total`: con eso sale la tasa de reuso del caché.
- `spec_decode_num_*` dan la aceptación de la decodificación especulativa
  (MTP/draft): accepted / draft tokens.
- Contexto ocupado ahora: sin kv_cache_tokens, sale de GET /slots. En el build
  2026-09, `n_prompt_tokens` de cada slot es el largo de su secuencia (prompt +
  tokens ya generados): durante una generación crece a la par de
  `next_token.n_decoded`. Con el slot ocioso conserva la secuencia de la última
  conversación, que sigue en el KV cache para reutilizarse. Se suman los slots.
  /slots es opcional (se desactiva con --no-slots): si falla, queda el pico.
- Los contadores son acumulados desde que arrancó el proceso. `restart`
  reutiliza el mismo process_id, así que un delta negativo es un reinicio:
  esa muestra no produce tasa en vez de inventar un pico falso.
- tg t/s = Δtokens_predicted_total / Δtokens_predicted_seconds_total: la
  velocidad real mientras genera. throughput = Δtokens / Δtiempo de reloj: lo
  que sale del servidor en el tiempo (0 cuando está ocioso).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import metrics as hw_metrics
import metrics_export
import session_tokens
from config import get_cloud_pricing
from launcher import ProcessInfo
from launcher import manager as launcher_manager

logger = logging.getLogger("glyvex.llm_metrics")

HISTORY_MAXLEN = 300
# Tope global de entradas por process_id en buffers/_aggregators/_models:
# crecen un entry por proceso distinto a lo largo de la vida de la app. 64
# sobra (cada buffer ya está acotado a HISTORY_MAXLEN snaps).
MAX_TRACKED_PROCESSES = 64
# 1 s mientras alguien mira la tira o el Monitor por WS; 5 s en segundo plano.
# Cada lectura hace que llama-server procese una tarea (y loguee una línea de
# "slots idle"); para el histórico alcanza con 5 s, que es el tamaño de ventana.
POLL_INTERVAL_S = 1.0
BACKGROUND_INTERVAL_S = 5.0
SCRAPE_TIMEOUT = httpx.Timeout(connect=1.0, read=1.5, write=1.0, pool=1.0)
# El selector del Monitor solo lista procesos guardados que duraron al menos
# esto: un arranque que vivió 30 segundos no merece una entrada en la lista.
MIN_STORED_LIFETIME_S = 120
# Proceso muerto que duró menos de esto: se borra del histórico (metrics.db).
# Fueron pruebas de arranque y sus series solo generan basura.
PURGE_SHORT_LIFETIME_S = 300
# Tope de procesos guardados que muestra el selector (más recientes primero).
MAX_STORED_PROCESSES = 10

# Series que se guardan en metrics.db (scope="llm") y su unidad.
PERSISTED_UNITS: dict[str, str] = {
    "llm.tg_tps": "t/s",
    "llm.pp_tps": "t/s",
    "llm.throughput_tps": "t/s",
    "llm.ctx_used": "tokens",
    "llm.ctx_pct": "%",
    "llm.ctx_peak": "tokens",
    "llm.requests_processing": "",
    "llm.requests_deferred": "",
    "llm.cache_hit_pct": "%",
    "llm.spec_accept_pct": "%",
    "llm.cache_hit_pct_total": "%",
    "llm.spec_accept_pct_total": "%",
    # Totales absolutos por corrida (MON-4): store.consumption_totals los
    # mergea a través de reinicios del mismo process_id.
    "llm.prompt_tokens_total": "tokens",
    "llm.prompt_tokens_cached_total": "tokens",
    "llm.tokens_predicted_total": "tokens",
}


# --------------------------------------------------------------------------
# Parseo del formato Prometheus
# --------------------------------------------------------------------------


def parse_prometheus(text: str) -> dict[str, float]:
    """
    Métricas de texto Prometheus a {nombre: valor}, sin el prefijo
    `llamacpp:`. Si una métrica aparece con varias etiquetas (modo router con
    varios modelos), se suman: para este uso interesa el total del proceso.
    Líneas de comentario, valores no numéricos y NaN se ignoran.
    """
    values: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # nombre{etiquetas} valor [timestamp]
        if "{" in line:
            name, _, rest = line.partition("{")
            _, _, rest = rest.partition("}")
            parts = rest.split()
        else:
            name, *parts = line.split()
        if not parts:
            continue
        try:
            value = float(parts[0])
        except ValueError:
            continue
        if math.isnan(value):
            continue
        key = name.split(":", 1)[1] if name.startswith("llamacpp:") else name
        values[key] = values.get(key, 0.0) + value
    return values


# --------------------------------------------------------------------------
# Schema del payload del WS
# --------------------------------------------------------------------------


class LlmMetricsSnapshot(BaseModel):
    timestamp: str
    process_id: str
    model_name: str
    state: str
    scrape_ok: bool
    scrape_error: str | None = None

    # Tasas derivadas de deltas entre scrapes (null en el 1er scrape, tras un
    # reinicio o, para tg/pp, si no hubo generación en el intervalo).
    tg_tps: float | None = None
    pp_tps: float | None = None
    throughput_tps: float | None = None

    tokens_predicted_total: int | None = None
    prompt_tokens_total: int | None = None
    # Caché reutilizado en valor absoluto (MON-4): más abajo solo existe el
    # porcentaje, y para el consumo total del server hace falta el número.
    prompt_tokens_cached_total: int | None = None

    ctx_total: int | None = None
    ctx_used: int | None = None
    # De dónde salió ctx_used: "kv_cache" (/metrics, builds viejos) o "slots".
    ctx_source: str | None = None
    slots_total: int | None = None
    slots_busy: int | None = None
    ctx_usage_ratio: float | None = None
    ctx_peak: int | None = None

    requests_processing: int | None = None
    requests_deferred: int | None = None

    # Caché de prompt: % de tokens de prompt reutilizados. "interval" es el
    # del último intervalo (null si no hubo prompts); "total" desde el arranque.
    cache_hit_pct: float | None = None
    cache_hit_pct_total: float | None = None

    # Decodificación especulativa (MTP/draft): % de tokens propuestos aceptados.
    spec_accept_pct: float | None = None
    spec_accept_pct_total: float | None = None

    # Última velocidad de generación medida y cuándo, aunque haya sido hace
    # rato: con el servidor ocioso tg_tps es null y la tira necesita algo que
    # mostrar sin depender de cuántos snapshots entren en un buffer.
    tg_tps_last: float | None = None
    tg_tps_last_at: str | None = None


# --------------------------------------------------------------------------
# Tasas entre scrapes
# --------------------------------------------------------------------------


@dataclass
class _Previous:
    wall: float
    counters: dict[str, float] = field(default_factory=dict)


def _rate(delta_num: float | None, delta_den: float | None) -> float | None:
    if delta_num is None or delta_den is None or delta_den <= 0 or delta_num < 0:
        return None
    return round(delta_num / delta_den, 1)


def _pct(part: float | None, whole: float | None) -> float | None:
    if part is None or whole is None or whole <= 0 or part < 0:
        return None
    return round(min(100.0, part / whole * 100), 1)


EMPTY_RATES: dict[str, float | None] = {
    "tg_tps": None, "pp_tps": None, "throughput_tps": None,
    "cache_hit_pct": None, "spec_accept_pct": None,
}


class RateTracker:
    """Guarda el scrape anterior por proceso y calcula tasas."""

    COUNTERS = (
        "tokens_predicted_total",
        "tokens_predicted_seconds_total",
        "prompt_tokens_total",
        "prompt_seconds_total",
        "prompt_tokens_cached_total",
        "spec_decode_num_draft_tokens_total",
        "spec_decode_num_accepted_tokens_total",
    )

    def __init__(self) -> None:
        self._prev: dict[str, _Previous] = {}

    def forget(self, process_id: str) -> None:
        self._prev.pop(process_id, None)

    def update(self, process_id: str, values: dict[str, float], wall: float) -> dict[str, float | None]:
        current = {k: values[k] for k in self.COUNTERS if k in values}
        prev = self._prev.get(process_id)
        # Mismo tope que los dicts del manager: un entry por proceso distinto.
        while len(self._prev) >= MAX_TRACKED_PROCESSES:
            self._prev.pop(next(iter(self._prev)))
        self._prev[process_id] = _Previous(wall=wall, counters=current)

        rates = dict(EMPTY_RATES)
        if prev is None:
            return rates

        def delta(name: str) -> float | None:
            if name not in current or name not in prev.counters:
                return None
            return current[name] - prev.counters[name]

        d_tokens = delta("tokens_predicted_total")
        # Cualquier contador que baja indica reinicio del proceso con el mismo id.
        if any((d := delta(n)) is not None and d < 0 for n in self.COUNTERS):
            return rates

        d_wall = wall - prev.wall
        rates["throughput_tps"] = _rate(d_tokens, d_wall)
        # Sin generación en el intervalo no hay velocidad que informar: null,
        # no 0, así el histórico de t/s no se aplana con los ratos ociosos.
        if d_tokens:
            rates["tg_tps"] = _rate(d_tokens, delta("tokens_predicted_seconds_total"))
        d_prompt = delta("prompt_tokens_total")
        if d_prompt:
            rates["pp_tps"] = _rate(d_prompt, delta("prompt_seconds_total"))
        d_cached = delta("prompt_tokens_cached_total")
        if d_cached is not None and d_prompt is not None and (d_cached + d_prompt) > 0:
            rates["cache_hit_pct"] = _pct(d_cached, d_cached + d_prompt)
        rates["spec_accept_pct"] = _pct(
            delta("spec_decode_num_accepted_tokens_total"),
            delta("spec_decode_num_draft_tokens_total"),
        )
        return rates


def parse_slots(payload: object) -> dict[str, int] | None:
    """
    Resumen de GET /slots: tokens en secuencia (suma de slots), capacidad y
    slots ocupados. None si el formato no es el esperado: mejor sin dato que
    un número inventado.
    """
    if not isinstance(payload, list) or not payload:
        return None
    used = 0
    n_ctx_max = 0
    busy = 0
    counted = 0
    for slot in payload:
        if not isinstance(slot, dict):
            continue
        tokens = slot.get("n_prompt_tokens")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            continue
        counted += 1
        used += tokens
        n_ctx = slot.get("n_ctx")
        if isinstance(n_ctx, int) and not isinstance(n_ctx, bool):
            n_ctx_max = max(n_ctx_max, n_ctx)
        if slot.get("is_processing") is True:
            busy += 1
    if counted == 0:
        return None
    return {"ctx_used": used, "n_ctx": n_ctx_max, "slots_total": counted, "slots_busy": busy}


def build_snapshot(
    info: ProcessInfo,
    values: dict[str, float] | None,
    rates: dict[str, float | None],
    error: str | None,
    slots: dict[str, int] | None = None,
) -> LlmMetricsSnapshot:
    ctx_total = info.launch_config.get("n_ctx") if isinstance(info.launch_config, dict) else None
    ctx_total = int(ctx_total) if isinstance(ctx_total, (int, float)) and ctx_total > 0 else None

    snap = LlmMetricsSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        process_id=info.process_id,
        model_name=info.model_name,
        state=info.state,
        scrape_ok=values is not None,
        scrape_error=error,
        ctx_total=ctx_total,
        **rates,
    )
    if values is None:
        return snap

    def as_int(name: str) -> int | None:
        return int(values[name]) if name in values else None

    snap.tokens_predicted_total = as_int("tokens_predicted_total")
    snap.prompt_tokens_total = as_int("prompt_tokens_total")
    snap.prompt_tokens_cached_total = as_int("prompt_tokens_cached_total")
    snap.requests_processing = as_int("requests_processing")
    snap.requests_deferred = as_int("requests_deferred")
    # n_tokens_max en builds actuales; n_past_max en anteriores.
    snap.ctx_peak = as_int("n_tokens_max") if "n_tokens_max" in values else as_int("n_past_max")

    cached_total = values.get("prompt_tokens_cached_total")
    processed_total = values.get("prompt_tokens_total")
    if cached_total is not None and processed_total is not None:
        snap.cache_hit_pct_total = _pct(cached_total, cached_total + processed_total)
    snap.spec_accept_pct_total = _pct(
        values.get("spec_decode_num_accepted_tokens_total"),
        values.get("spec_decode_num_draft_tokens_total"),
    )

    if slots is not None:
        snap.slots_total = slots["slots_total"]
        snap.slots_busy = slots["slots_busy"]
        if snap.ctx_total is None and slots["n_ctx"]:
            snap.ctx_total = slots["n_ctx"]

    # Prioridad: kv_cache_tokens (exacto, builds viejos) y después /slots.
    ratio = values.get("kv_cache_usage_ratio")
    if "kv_cache_tokens" in values:
        snap.ctx_used = as_int("kv_cache_tokens")
        snap.ctx_source = "kv_cache"
    elif slots is not None:
        snap.ctx_used = slots["ctx_used"]
        snap.ctx_source = "slots"

    if ratio is not None:
        snap.ctx_usage_ratio = round(ratio, 4)
    elif snap.ctx_used is not None and snap.ctx_total:
        snap.ctx_usage_ratio = round(min(1.0, snap.ctx_used / snap.ctx_total), 4)
    return snap


def flatten_llm_snapshot(snap: LlmMetricsSnapshot) -> dict[str, float]:
    """Valores a persistir. Los null no generan muestra (hueco en el gráfico)."""
    out: dict[str, float] = {}

    def put(key: str, value: float | int | None) -> None:
        if value is not None:
            out[key] = float(value)

    put("llm.tg_tps", snap.tg_tps)
    put("llm.pp_tps", snap.pp_tps)
    put("llm.throughput_tps", snap.throughput_tps)
    put("llm.ctx_used", snap.ctx_used)
    put("llm.ctx_pct", snap.ctx_usage_ratio * 100 if snap.ctx_usage_ratio is not None else None)
    put("llm.ctx_peak", snap.ctx_peak)
    put("llm.requests_processing", snap.requests_processing)
    put("llm.requests_deferred", snap.requests_deferred)
    put("llm.cache_hit_pct", snap.cache_hit_pct)
    put("llm.spec_accept_pct", snap.spec_accept_pct)
    put("llm.cache_hit_pct_total", snap.cache_hit_pct_total)
    put("llm.spec_accept_pct_total", snap.spec_accept_pct_total)
    # Totales absolutos de la corrida actual (MON-4): acumulados por el propio
    # llama-server, así cuentan también a los clientes externos a la app.
    put("llm.prompt_tokens_total", snap.prompt_tokens_total)
    put("llm.prompt_tokens_cached_total", snap.prompt_tokens_cached_total)
    put("llm.tokens_predicted_total", snap.tokens_predicted_total)
    return out


def describe_slots_status(status: int) -> str:
    if status == 501:
        return "HTTP 501: /slots deshabilitado (llama-server con --no-slots)"
    if status in (401, 403):
        return f"HTTP {status}: rechazó el API key"
    return f"HTTP {status}"


def scrape_host(host: str) -> str:
    # Un servidor escuchando en todas las interfaces se consulta por loopback.
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def describe_scrape_error(status: int | None, exc: Exception | None) -> str:
    if status == 501:
        return "llama-server sin métricas: relanzá el proceso (se agrega --metrics)"
    if status in (401, 403):
        return "llama-server rechazó el API key al leer /metrics"
    if status is not None:
        return f"/metrics respondió HTTP {status}"
    if isinstance(exc, httpx.ConnectError):
        return "no responde (¿todavía cargando el modelo?)"
    if isinstance(exc, httpx.TimeoutException):
        return "/metrics no respondió a tiempo"
    return f"error leyendo /metrics: {exc}"


# --------------------------------------------------------------------------
# Manager
# --------------------------------------------------------------------------


class LlmMetricsManager:
    def __init__(self) -> None:
        self.buffers: dict[str, deque[LlmMetricsSnapshot]] = {}
        self.rates = RateTracker()
        self._clients: dict[str, set[WebSocket]] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self._aggregators: dict[str, hw_metrics.WindowAggregator] = {}
        self._models: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None
        # Se crea dentro de _loop(): un Event queda atado al event loop que lo
        # usa primero, y en los tests cada test corre en un loop distinto.
        self._wake: asyncio.Event | None = None
        # Última tg medida por proceso: (valor, timestamp ISO).
        self._last_tg: dict[str, tuple[float, str]] = {}
        # Último estado de /slots por proceso (None = OK) para loguear solo
        # los cambios y no una línea por lectura.
        self._slots_status: dict[str, str | None] = {}
        # Primer/último contacto con cada proceso (time.time()): con eso se
        # mide cuánto duró, para no llenar el Monitor de pruebas de arranque.
        self._first_seen: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}

    # -- procesos a leer --------------------------------------------------------

    @staticmethod
    def targets() -> list[ProcessInfo]:
        return [
            p for p in launcher_manager.status_all()
            if p.backend == "llama_server" and p.state == "running"
        ]

    # -- scrape ---------------------------------------------------------------

    async def scrape(self, info: ProcessInfo) -> LlmMetricsSnapshot:
        url = f"http://{scrape_host(info.host)}:{info.port}/metrics"
        headers = {}
        api_key = info.launch_config.get("api_key") if isinstance(info.launch_config, dict) else None
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        values: dict[str, float] | None = None
        error: str | None = None
        slots: dict[str, int] | None = None
        slots_problem: str | None = None
        client = self._client or httpx.AsyncClient(timeout=SCRAPE_TIMEOUT)

        async def get_slots() -> tuple[dict[str, int] | None, str | None]:
            # Opcional: sin /slots (--no-slots, 501, formato distinto) sigue el
            # pico. Devuelve también el motivo si falla, para el log.
            try:
                res = await client.get(url.rsplit("/", 1)[0] + "/slots", headers=headers)
            except httpx.HTTPError as exc:
                return None, f"{type(exc).__name__}: {exc}"
            if res.status_code != 200:
                return None, describe_slots_status(res.status_code)
            try:
                parsed = parse_slots(res.json())
            except ValueError:
                return None, "respuesta que no es JSON"
            if parsed is None:
                return None, "formato inesperado (¿cambió en este build de llama-server?)"
            return parsed, None

        try:
            # return_exceptions: las dos lecturas terminan siempre antes de
            # seguir (y antes de cerrar un cliente temporal).
            metrics_res, slots_res = await asyncio.gather(
                client.get(url, headers=headers), get_slots(), return_exceptions=True
            )
            if isinstance(slots_res, tuple):
                slots, slots_problem = slots_res
            else:
                slots, slots_problem = None, f"{type(slots_res).__name__}: {slots_res}"
            if isinstance(metrics_res, httpx.HTTPError):
                error = describe_scrape_error(None, metrics_res)
            elif isinstance(metrics_res, BaseException):
                raise metrics_res
            elif metrics_res.status_code == 200:
                values = parse_prometheus(metrics_res.text)
            else:
                error = describe_scrape_error(metrics_res.status_code, None)
        finally:
            if client is not self._client:
                await client.aclose()

        wall = time.time()
        if values is None:
            # Sin lectura no hay contadores: la próxima tasa arranca de cero.
            self.rates.forget(info.process_id)
            rates = dict(EMPTY_RATES)
        else:
            rates = self.rates.update(info.process_id, values, wall)
            # Solo con /metrics OK: si el servidor no responde, ese error ya
            # se informa y /slots fallaría por lo mismo.
            self.note_slots_status(info.process_id, slots_problem)
        snap = build_snapshot(info, values, rates, error, slots)
        self.apply_last_tg(snap)
        return snap

    def apply_last_tg(self, snap: LlmMetricsSnapshot) -> None:
        if snap.tg_tps is not None:
            self._last_tg[snap.process_id] = (snap.tg_tps, snap.timestamp)
        last = self._last_tg.get(snap.process_id)
        if last is not None:
            snap.tg_tps_last, snap.tg_tps_last_at = last

    def note_slots_status(self, process_id: str, problem: str | None) -> bool:
        """
        Loguea cuando /slots empieza a fallar o cambia el motivo, y cuando se
        recupera. Devuelve True si escribió en el log. Sin esto el fallo era
        silencioso: la tira mostraba el pico y no había forma de saber por qué.
        """
        previous = self._slots_status.get(process_id, None)
        known = process_id in self._slots_status
        self._slots_status[process_id] = problem
        if problem is not None and problem != previous:
            logger.warning(
                "no se pudo leer /slots del proceso %s (%s): el contexto actual queda "
                "vacío y se muestra el pico", process_id, problem,
            )
            return True
        if problem is None and known and previous is not None:
            logger.info("/slots del proceso %s vuelve a responder", process_id)
            return True
        return False

    def _evict_if_full(self, d: dict, name: str) -> None:
        while len(d) >= MAX_TRACKED_PROCESSES:
            oldest = next(iter(d))
            d.pop(oldest)
            logger.debug("tope de %s alcanzado: se suelta %s", name, oldest)

    def record(self, snap: LlmMetricsSnapshot) -> None:
        self._evict_if_full(self.buffers, "buffers")
        buf = self.buffers.setdefault(snap.process_id, deque(maxlen=HISTORY_MAXLEN))
        buf.append(snap)
        self._evict_if_full(self._models, "_models")
        self._models[snap.process_id] = snap.model_name
        now = time.time()
        self._first_seen.setdefault(snap.process_id, now)
        self._last_seen[snap.process_id] = now

    # -- limpieza de procesos muertos -------------------------------------------

    @staticmethod
    def _is_dead(process_id: str) -> bool:
        info = launcher_manager.status(process_id)
        return info is None or info.state in ("stopped", "error")

    def _forget_process(self, process_id: str) -> None:
        self.buffers.pop(process_id, None)
        self._models.pop(process_id, None)
        self._aggregators.pop(process_id, None)
        self.rates.forget(process_id)
        self._last_tg.pop(process_id, None)
        self._slots_status.pop(process_id, None)
        self._first_seen.pop(process_id, None)
        self._last_seen.pop(process_id, None)

    async def _cleanup_dead(self) -> None:
        """
        Suelta de memoria los procesos que ya no corren y borra del histórico
        los que duraron un momento (pruebas de arranque): sin esto cada
        arranque muerto dejaba series en metrics.db y una entrada permanente
        en el selector del Monitor.
        """
        live = {p.process_id for p in self.targets()}
        store = hw_metrics.manager.store
        for process_id in list(self._first_seen):
            if process_id in live or not self._is_dead(process_id):
                continue
            lifetime = self._last_seen.get(process_id, 0.0) - self._first_seen[process_id]
            if store is not None and lifetime < PURGE_SHORT_LIFETIME_S:
                try:
                    deleted = await asyncio.to_thread(store.delete_process, process_id)
                    logger.info(
                        "proceso corto %s (%.0f s): %d series borradas del histórico",
                        process_id, lifetime, deleted,
                    )
                except Exception as exc:  # noqa: BLE001 — la limpieza no corta el poller
                    logger.warning("no se pudo borrar del histórico el proceso %s: %s", process_id, exc)
            self._forget_process(process_id)

    async def _purge_leftovers(self) -> None:
        """
        Procesos cortos dejados por sesiones anteriores: el poller solo conoce
        los que vio en esta sesión, así que al arrancar se recorre el
        histórico y se borran los que duraron un momento.
        """
        store = hw_metrics.manager.store
        if store is None:
            return
        try:
            series = await asyncio.to_thread(store.list_series, "llm")
        except Exception as exc:  # noqa: BLE001
            logger.warning("no se pudo revisar el histórico al arrancar: %s", exc)
            return
        live = {p.process_id for p in self.targets()}
        bounds: dict[str, list[int]] = {}
        for s in series:
            pid = s["process_id"]
            if not pid:
                continue
            values = [v for v in (s["first_ts"], s["last_ts"]) if v is not None]
            if not values:
                continue
            bound = bounds.setdefault(pid, [min(values), max(values)])
            bound[0] = min(bound[0], min(values))
            bound[1] = max(bound[1], max(values))
        for pid, (first, last) in bounds.items():
            if pid in live or last - first >= PURGE_SHORT_LIFETIME_S:
                continue
            try:
                deleted = await asyncio.to_thread(store.delete_process, pid)
                logger.info("proceso corto de sesión anterior %s: %d series borradas", pid, deleted)
            except Exception as exc:  # noqa: BLE001
                logger.warning("no se pudo borrar del histórico el proceso %s: %s", pid, exc)

    async def poll_once(self) -> list[LlmMetricsSnapshot]:
        targets = self.targets()
        if not targets:
            return []
        snaps = await asyncio.gather(*(self.scrape(info) for info in targets))
        for snap in snaps:
            self.record(snap)
        return list(snaps)

    # -- WS ---------------------------------------------------------------------

    async def _broadcast(self, snap: LlmMetricsSnapshot) -> None:
        clients = self._clients.get(snap.process_id)
        if not clients:
            return
        payload = snap.model_dump_json()
        dead = []
        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    def add_client(self, process_id: str, websocket: WebSocket) -> None:
        self._clients.setdefault(process_id, set()).add(websocket)
        if self._wake is not None:
            self._wake.set()
        self._ensure_loop()

    def remove_client(self, process_id: str, websocket: WebSocket) -> None:
        clients = self._clients.get(process_id)
        if clients is not None:
            clients.discard(websocket)
            if not clients:
                self._clients.pop(process_id, None)

    def has_clients(self) -> bool:
        return any(self._clients.values())

    # -- persistencia -------------------------------------------------------------

    async def _persist(self, snaps: list[LlmMetricsSnapshot], now: float) -> None:
        # Las ventanas se arman si hay a dónde mandarlas: histórico o InfluxDB.
        if hw_metrics.manager.store is None and not metrics_export.exporter.enabled():
            return
        self._evict_if_full(self._aggregators, "_aggregators")
        for snap in snaps:
            agg = self._aggregators.setdefault(snap.process_id, hw_metrics.WindowAggregator())
            agg.add(now, flatten_llm_snapshot(snap), PERSISTED_UNITS)
        await self._flush(now, force=False)

    async def _flush(self, now: float, *, force: bool) -> None:
        store = hw_metrics.manager.store
        for process_id, agg in list(self._aggregators.items()):
            closed = agg.pop_closed(now, force=force)
            if not closed:
                continue
            model_name = self._models.get(process_id)
            metrics_export.exporter.enqueue(
                closed, scope="llm", units=PERSISTED_UNITS,
                process_id=process_id, model_name=model_name,
            )
            if store is not None:
                await asyncio.to_thread(
                    store.write_raw, closed, scope="llm", units=PERSISTED_UNITS,
                    process_id=process_id, model_name=model_name,
                )
        # Procesos que ya no se leen y no tienen ventanas abiertas: se sueltan.
        live = {p.process_id for p in self.targets()}
        for process_id in [pid for pid, agg in self._aggregators.items()
                           if pid not in live and not agg.has_open_windows]:
            self._aggregators.pop(process_id, None)

    # -- loop -----------------------------------------------------------------

    async def _loop(self) -> None:
        self._wake = asyncio.Event()
        self._client = httpx.AsyncClient(timeout=SCRAPE_TIMEOUT)
        try:
            while self._running or self.has_clients():
                started = time.monotonic()
                try:
                    snaps = await self.poll_once()
                    for snap in snaps:
                        await self._broadcast(snap)
                    if self._running:
                        await self._persist(snaps, time.time())
                        await self._cleanup_dead()
                except Exception as exc:  # noqa: BLE001 — un error no corta el poller
                    logger.warning("error leyendo métricas de llama-server: %s", exc)
                elapsed = time.monotonic() - started
                interval = POLL_INTERVAL_S if self.has_clients() else BACKGROUND_INTERVAL_S
                await self._sleep_or_wake(max(0.0, interval - elapsed))
        finally:
            client, self._client = self._client, None
            if client is not None:
                await client.aclose()
            self._task = None

    async def _sleep_or_wake(self, seconds: float) -> None:
        """
        Duerme hasta el próximo ciclo, pero se despierta apenas se conecta un
        cliente: sin esto, abrir el Launcher con el poller en 5 s tardaría
        hasta 5 s en mostrar el primer valor.
        """
        if self._wake is None:
            await asyncio.sleep(seconds)
            return
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _ensure_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._purge_leftovers()
        self._ensure_loop()
        logger.info("poller de métricas de llama-server iniciado")

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
        try:
            await self._flush(time.time(), force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("no se pudieron guardar las últimas métricas de llama-server: %s", exc)


manager = LlmMetricsManager()


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("/processes")
async def list_processes() -> dict[str, Any]:
    """
    Procesos para el selector del Monitor: los que están corriendo ahora y los
    que tienen histórico guardado (aunque ya no existan), con tope de
    MAX_STORED_PROCESSES (más recientes primero). Con la lista saturada se
    descarta además a los procesos que solo dejaron un momento de muestras
    (menos de MIN_STORED_LIFETIME_S): pruebas de arranque que sin eso dejarían
    una entrada permanente por cada intento.
    """
    live = [
        {"process_id": p.process_id, "model_name": p.model_name, "state": p.state,
         "host": p.host, "port": p.port}
        for p in manager.targets()
    ]
    stored: dict[str, dict[str, Any]] = {}
    store = hw_metrics.manager.store
    if store is not None:
        for s in await asyncio.to_thread(store.list_series, "llm"):
            pid = s["process_id"]
            if not pid:
                continue
            entry = stored.setdefault(pid, {"process_id": pid, "model_name": s["model_name"],
                                            "first_ts": s["first_ts"], "last_ts": s["last_ts"]})
            for bound, pick in (("first_ts", min), ("last_ts", max)):
                values = [v for v in (entry[bound], s[bound]) if v is not None]
                entry[bound] = pick(values) if values else None
    ordered = sorted(stored.values(), key=lambda e: e["last_ts"] or 0, reverse=True)
    if len(ordered) > MAX_STORED_PROCESSES:
        ordered = [
            e for e in ordered
            if e["first_ts"] is not None
            and e["last_ts"] is not None
            and e["last_ts"] - e["first_ts"] >= MIN_STORED_LIFETIME_S
        ]
    return {"live": live, "stored": ordered[:MAX_STORED_PROCESSES]}


# Series de consumo total por instancia (MON-4): las keys que persiste
# flatten_llm_snapshot, cuyos valores consumption_totals mergea por process_id.
CONSUMPTION_KEYS = (
    "llm.prompt_tokens_total",
    "llm.prompt_tokens_cached_total",
    "llm.tokens_predicted_total",
)


@router.get("/{process_id}/consumption")
async def get_consumption(process_id: str) -> dict[str, Any]:
    """
    MON-4: tokens acumulados por una instancia de llama-server desde su arranque,
    de TODOS los clientes. Los contadores Prometheus del server cuentan el chat
    de la app, OpenCode y cualquier consumidor de la API por igual; se leen del
    metrics.db (la misma frescura que los gráficos del Monitor) y se mergean a
    través de reinicios del mismo process_id. Funciona también para procesos
    muertos con histórico. El pricing va embebido, patrón session_tokens, para
    que la comparativa de nube se calcule en el frontend sin pedir permiso.
    """
    store = hw_metrics.manager.store
    totals: dict[str, Any] = {}
    if store is not None:
        totals = await asyncio.to_thread(store.consumption_totals, process_id, list(CONSUMPTION_KEYS))

    def part(key: str) -> dict[str, Any]:
        e = totals.get(key) or {}
        return {"tokens": e.get("total"), "first_ts": e.get("first_ts"),
                "last_ts": e.get("last_ts"), "runs": e.get("runs")}

    prompt = part("llm.prompt_tokens_total")
    cached = part("llm.prompt_tokens_cached_total")
    predicted = part("llm.tokens_predicted_total")
    entries = [e for e in totals.values() if e]
    return {
        "process_id": process_id,
        "model_name": entries[0]["model_name"] if entries else None,
        # Un llama-server reiniciado resetea todos sus contadores a la vez: el
        # máximo entre series es el número de arranques de la instancia.
        "runs": max((e["runs"] for e in entries), default=0),
        "first_ts": min((e["first_ts"] for e in entries), default=None),
        "last_ts": max((e["last_ts"] for e in entries), default=None),
        "prompt_tokens": prompt["tokens"],
        "cached_tokens": cached["tokens"],
        "predicted_tokens": predicted["tokens"],
        "pricing": get_cloud_pricing().model_dump(),
    }


# Mapeo de keys de Prometheus a campos de la respuesta de consumo.
CONSUMPTION_FIELDS = {
    "llm.prompt_tokens_total": "prompt_tokens",
    "llm.prompt_tokens_cached_total": "cached_tokens",
    "llm.tokens_predicted_total": "predicted_tokens",
}


@router.get("/consumption/all")
async def get_consumption_all() -> dict[str, Any]:
    """
    MON-5: total de tokens de POR VIDA de todas las instancias de llama-server
    registradas en el metrics.db, de todos los clientes. Es la suma de los
    totales por proceso (mismo merge de reinicios, serie por serie) con un
    desglose por modelo para comparar contra qué modelo se habría pagado en la
    nube. Para una instalación nueva esto empieza en su primer launch: el
    histórico cubre desde que la app monitoreó el server. Pricing embebido,
    patrón session_tokens.
    """
    store = hw_metrics.manager.store
    rows: list[dict[str, Any]] = []
    if store is not None:
        rows = await asyncio.to_thread(store.consumption_series_all, list(CONSUMPTION_KEYS))

    # None significa "ninguna serie aportó ese contador", no 0: la UI lo muestra
    # como sin datos, igual que el endpoint por proceso.
    models: dict[str, dict[str, Any]] = {}
    totals: dict[str, int | None] = {f: None for f in CONSUMPTION_FIELDS.values()}
    processes: set[str] = set()
    for r in rows:
        field = CONSUMPTION_FIELDS[r["key"]]
        model = models.setdefault(r["model_name"] or "", {
            "model_name": r["model_name"] or "",
            "process_ids": set(),
            **{f: None for f in CONSUMPTION_FIELDS.values()},
            "first_ts": None,
            "last_ts": None,
        })
        model[field] = (model[field] or 0) + r["total"]
        model["first_ts"] = min(filter(None, [model["first_ts"], r["first_ts"]]), default=None)
        model["last_ts"] = max(filter(None, [model["last_ts"], r["last_ts"]]), default=None)
        model["process_ids"].add(r["process_id"])
        totals[field] = (totals[field] or 0) + r["total"]
        processes.add(r["process_id"])

    by_model = sorted(
        (
            {**m, "processes": len(m.pop("process_ids"))}
            for m in models.values()
        ),
        key=lambda m: (m["last_ts"] is None, -(m["last_ts"] or 0)),
    )

    # Desglose chat vs externo (MON-5): los contadores del motor cuentan a
    # TODOS los clientes por igual; restando el total de chat propio de la app
    # (lifetime de session_tokens, que no se reinicia con el botón Reset) el
    # resto es consumo externo (OpenCode, API, benchmarks). Se convierte el
    # usage estilo OpenAI (prompt INCLUYE caché) al formato llama.cpp (fresh
    # y caché separados), las mismas unidades que los totales del motor.
    life_chat = session_tokens.session_tokens.snapshot().get("lifetime") or {}
    chat: dict[str, Any] | None = None
    if life_chat.get("prompt_tokens") or life_chat.get("completion_tokens"):
        cached_chat = max(0, int(life_chat.get("cached_tokens") or 0))
        chat = {
            "prompt_tokens": max(0, int(life_chat.get("prompt_tokens") or 0) - cached_chat),
            "cached_tokens": cached_chat,
            "predicted_tokens": max(0, int(life_chat.get("completion_tokens") or 0)),
            "turns": int(life_chat.get("turns") or 0),
            "since": life_chat.get("started_at"),
        }
    external: dict[str, Any] | None = None
    if chat is not None and totals["prompt_tokens"] is not None:
        external = {
            "prompt_tokens": max(0, int(totals["prompt_tokens"]) - chat["prompt_tokens"]),
            "cached_tokens": max(0, int(totals["cached_tokens"] or 0) - chat["cached_tokens"]),
            "predicted_tokens": max(
                0, int(totals["predicted_tokens"] or 0) - chat["predicted_tokens"]
            ),
        }

    return {
        "servers": len(processes),
        "first_ts": min((r["first_ts"] for r in rows), default=None),
        "last_ts": max((r["last_ts"] for r in rows), default=None),
        "prompt_tokens": totals["prompt_tokens"],
        "cached_tokens": totals["cached_tokens"],
        "predicted_tokens": totals["predicted_tokens"],
        "models": by_model,
        "split": {"chat": chat, "external": external},
        "pricing": get_cloud_pricing().model_dump(),
    }


@router.get("/{process_id}/history", response_model=list[LlmMetricsSnapshot])
async def get_history(process_id: str) -> list[LlmMetricsSnapshot]:
    return list(manager.buffers.get(process_id, []))


@router.websocket("/{process_id}/stream")
async def llm_metrics_stream(websocket: WebSocket, process_id: str) -> None:
    await websocket.accept()
    info = launcher_manager.status(process_id)
    if info is None:
        await websocket.send_json({"error": "Proceso no encontrado"})
        await websocket.close()
        return
    if info.backend != "llama_server":
        await websocket.send_json({"error": f"{info.backend} no expone métricas de servidor"})
        await websocket.close()
        return

    manager.add_client(process_id, websocket)
    try:
        while True:
            # Push únicamente; receive_text() solo detecta la desconexión.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_client(process_id, websocket)

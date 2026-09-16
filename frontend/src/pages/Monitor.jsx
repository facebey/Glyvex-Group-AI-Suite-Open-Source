import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Cpu, MemoryStick, Gauge, History, RefreshCw, Server } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from "recharts";
import { useLlmStream } from "../hooks/useLlmStream.js";

const SPARKLINE_POINTS = 60;

// -- Histórico (GET /api/metrics/query) --------------------------------------
//
// Series con avg/min/max por ventana; la resolución (5s/1min/1h) la elige el
// backend según el rango pedido (ver metrics_store.choose_tier). El rango
// elegido acá decide cuántos segundos de "start" pedir.
const RANGES = [
  { id: "1h", label: "1 h", seconds: 3600 },
  { id: "24h", label: "24 h", seconds: 24 * 3600 },
  { id: "7d", label: "7 días", seconds: 7 * 86400 },
  { id: "30d", label: "30 días", seconds: 30 * 86400 },
];

const TIER_LABEL = { raw: "cada 5 s", "1m": "cada 1 min", "1h": "cada 1 h" };

// Series y colores de cada gráfico de histórico. Los colores repiten la
// paleta que ya usa Sparkline para GPU/VRAM/CPU/RAM, así el histórico se
// lee como una continuación de los gráficos en vivo, no algo aparte.
const TEMP_LEGEND = [
  { key: "gpu.0.temp_c", name: "GPU", color: "#3b82f6" },
  { key: "cpu.temp_c", name: "CPU", color: "#f59e0b" },
];
const LOAD_LEGEND = [
  { key: "gpu.0.util_pct", name: "GPU", color: "#3b82f6" },
  { key: "gpu.0.vram_pct", name: "VRAM", color: "#06b6d4" },
  { key: "cpu.total_pct", name: "CPU", color: "#f59e0b" },
];
const RAM_LEGEND = [{ key: "ram.pct", name: "RAM", color: "#06b6d4" }];

// -- LLM Server (series scope="llm" de metrics.db, por process_id) -----------
const LLM_KEYS = [
  "llm.tg_tps", "llm.throughput_tps", "llm.ctx_pct", "llm.ctx_peak",
  "llm.requests_processing", "llm.requests_deferred",
];
const LLM_SPEED_LEGEND = [
  { key: "llm.tg_tps", name: "Generación", color: "#22c55e" },
  { key: "llm.throughput_tps", name: "Throughput", color: "#7c3aed" },
];
const LLM_CTX_PCT_LEGEND = [{ key: "llm.ctx_pct", name: "Contexto usado", color: "#06b6d4" }];
const LLM_CTX_PEAK_LEGEND = [{ key: "llm.ctx_peak", name: "Pico de contexto", color: "#06b6d4" }];
const LLM_QUEUE_LEGEND = [
  { key: "llm.requests_processing", name: "En curso", color: "#3b82f6" },
  { key: "llm.requests_deferred", name: "En espera", color: "#f59e0b" },
];
// En vivo: WS /api/llm-metrics/{id}/stream (1 s mientras hay un cliente).
const LLM_LIVE_WINDOW_S = 300;
const LLM_LIVE_BUFFER = 330; // algo más que 5 min a 1 s

function contextTitle(snap) {
  if (!snap || snap.ctx_used == null) {
    return "Este build de llama-server no expone el contexto actual: se muestra el pico observado";
  }
  if (snap.ctx_source === "kv_cache") return "Tokens en el KV cache ahora";
  if (snap.slots_busy === 0) {
    return "Secuencia de la última conversación, que sigue en el KV cache para reutilizarse (dato de /slots)";
  }
  return "Tokens en la secuencia actual: prompt + generados hasta ahora (dato de /slots)";
}

function formatDateShort(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatAxisTime(ts, rangeSeconds) {
  const d = new Date(ts * 1000);
  if (rangeSeconds > 36 * 3600) return d.toLocaleDateString([], { day: "2-digit", month: "2-digit" });
  if (rangeSeconds <= 600) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatTooltipTime(ts) {
  return new Date(ts * 1000).toLocaleString([], {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/**
 * Junta varias series de /api/metrics/query (mismo tier: se pidieron juntas)
 * en filas por timestamp, con avg/min/max de cada una bajo su propio prefijo,
 * así el tooltip puede mostrar el rango sin que las líneas se dupliquen.
 */
function mergeSeries(seriesMap, keys) {
  const rows = new Map();
  for (const key of keys) {
    for (const point of seriesMap[key] || []) {
      const row = rows.get(point.t) || { t: point.t };
      row[`${key}__avg`] = point.avg;
      row[`${key}__min`] = point.min;
      row[`${key}__max`] = point.max;
      rows.set(point.t, row);
    }
  }
  return Array.from(rows.values()).sort((a, b) => a.t - b.t);
}

// -- Datos en vivo del LLM --------------------------------------------------

function snapTimestamp(snap) {
  // El backend manda ISO con microsegundos; Date.parse no garantiza aceptar
  // más de 3 decimales, así que se recortan.
  const iso = String(snap?.timestamp || "").replace(/(\.\d{3})\d+/, "$1");
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

/** Mismas series y cálculos que flatten_llm_snapshot en llm_metrics.py. */
function snapValues(snap) {
  return {
    "llm.tg_tps": snap.tg_tps,
    "llm.throughput_tps": snap.throughput_tps,
    "llm.ctx_pct": snap.ctx_usage_ratio != null ? snap.ctx_usage_ratio * 100 : null,
    "llm.ctx_peak": snap.ctx_peak,
    "llm.requests_processing": snap.requests_processing,
    "llm.requests_deferred": snap.requests_deferred,
  };
}

/**
 * Snapshots del WS al mismo formato que /api/metrics/query, para dibujarlos
 * con HistoryChart. Un null no genera punto (hueco, no cero).
 */
function liveSnapsToSeries(snaps, sinceTs = null) {
  const series = {};
  for (const snap of snaps) {
    const t = snapTimestamp(snap);
    if (t == null || (sinceTs != null && t < sinceTs)) continue;
    for (const [key, value] of Object.entries(snapValues(snap))) {
      if (value == null) continue;
      (series[key] ||= []).push({ t, avg: value, min: value, max: value });
    }
  }
  return series;
}

/** Agrupa puntos en intervalos alineados al reloj, igual que metrics_store.query. */
function bucketize(points, bucketS) {
  const buckets = new Map();
  for (const p of points) {
    const t = Math.floor(p.t / bucketS) * bucketS;
    const acc = buckets.get(t);
    if (!acc) buckets.set(t, { t, sum: p.avg, n: 1, min: p.min, max: p.max });
    else {
      acc.sum += p.avg;
      acc.n += 1;
      acc.min = Math.min(acc.min, p.min);
      acc.max = Math.max(acc.max, p.max);
    }
  }
  return [...buckets.values()]
    .sort((a, b) => a.t - b.t)
    .map((a) => ({ t: a.t, avg: a.sum / a.n, min: a.min, max: a.max }));
}

/**
 * Completa el histórico con los datos en vivo, agrupados con la misma
 * resolución para que el tramo final no cambie de nivel de detalle.
 *
 * - Un intervalo que el buffer en vivo cubre desde su inicio se REEMPLAZA:
 *   es más nuevo que el guardado. Si el intervalo ya cerró, ese promedio es
 *   completo; si es el bucket abierto (el actual) es parcial, y aun así es el
 *   mejor valor disponible (trade-off aceptado). Es lo que hace que el borde
 *   derecho se mueva cada segundo en 1 h y 24 h (intervalos de 10 s y 145 s,
 *   más cortos que los ~5 min del buffer).
 * - Un intervalo que el buffer cubre a medias solo se usa si el histórico no
 *   lo tiene (proceso recién lanzado, o intervalo que empezó después de la
 *   última consulta). En 7 y 30 días los intervalos son más largos que el
 *   buffer: ahí se conserva el guardado y solo se agregan los nuevos.
 */
function appendLiveTail(historySeries, liveSeries, resolutionS) {
  if (!resolutionS) return historySeries;
  const out = { ...historySeries };
  for (const [key, livePoints] of Object.entries(liveSeries)) {
    if (!livePoints.length) continue;
    const hist = historySeries[key] || [];
    const lastHistT = hist.length ? hist[hist.length - 1].t : -Infinity;
    const firstLiveT = livePoints[0].t;
    const coveredFrom = Math.ceil(firstLiveT / resolutionS) * resolutionS;
    const live = bucketize(livePoints, resolutionS).filter((p) => p.t >= coveredFrom || p.t > lastHistT);
    if (!live.length) continue;
    const firstLiveBucket = live[0].t;
    out[key] = [...hist.filter((p) => p.t < firstLiveBucket), ...live];
  }
  return out;
}

/** Junta el buffer inicial (/history) con lo que ya llegó por WS, sin repetir. */
function mergeLiveSnaps(prev, incoming) {
  const byTs = new Map();
  for (const snap of [...prev, ...incoming]) byTs.set(snap.timestamp, snap);
  return [...byTs.values()]
    .sort((a, b) => (snapTimestamp(a) ?? 0) - (snapTimestamp(b) ?? 0))
    .slice(-LLM_LIVE_BUFFER);
}

/** Tooltip que muestra "avg (min–max)" por serie, no solo el promedio. */
function HistoryTooltip({ active, payload, label, legend, unit }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  return (
    <div
      className="rounded-md border px-2.5 py-1.5 text-xs"
      style={{ background: "#1a1a1a", borderColor: "rgba(255,255,255,0.1)" }}
    >
      <p className="text-glyvex-muted mb-1">{formatTooltipTime(label)}</p>
      {legend.map((l) => {
        const avg = row[`${l.key}__avg`];
        if (avg == null) return null;
        const min = row[`${l.key}__min`];
        const max = row[`${l.key}__max`];
        const showRange = min != null && max != null && max - min > 0.05;
        return (
          <p key={l.key} style={{ color: l.color }}>
            {l.name}: {avg.toFixed(1)}{unit}
            {showRange && <span className="text-glyvex-muted"> ({min.toFixed(1)}–{max.toFixed(1)})</span>}
          </p>
        );
      })}
    </div>
  );
}

/**
 * Gráfico de histórico: una o más series contra el tiempo real (no los
 * últimos N puntos como Sparkline). Eje Y automático salvo que se fije
 * `domain` (para porcentajes, [0,100]). `legend` es [{key, name, color}],
 * en el mismo orden en que las series se pidieron a /api/metrics/query.
 */
function HistoryChart({
  series, legend, unit = "", domain, rangeSeconds, height = 180,
  emptyText = "Sin datos guardados todavía para este rango.",
  // false en vivo: un rato sin generar se ve como hueco, no como una recta.
  connectGaps = true,
}) {
  const keys = useMemo(() => legend.map((l) => l.key), [legend]);
  const data = useMemo(() => mergeSeries(series, keys), [series, keys]);
  const hasData = data.length > 0;

  return (
    <div style={{ height }}>
      {!hasData ? (
        <div className="h-full flex items-center justify-center text-xs text-glyvex-muted">
          {emptyText}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <XAxis
              dataKey="t"
              tickFormatter={(t) => formatAxisTime(t, rangeSeconds)}
              stroke="#5b6472"
              fontSize={11}
              minTickGap={40}
            />
            <YAxis domain={domain || ["auto", "auto"]} stroke="#5b6472" fontSize={11} width={36} />
            <Tooltip content={<HistoryTooltip legend={legend} unit={unit} />} />
            {legend.map((l) => (
              <Line
                key={l.key}
                type="monotone"
                dataKey={`${l.key}__avg`}
                name={l.name}
                stroke={l.color}
                strokeWidth={1.75}
                dot={false}
                isAnimationActive={false}
                connectNulls={connectGaps}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

/** Leyenda manual (bolita + nombre): recharts <Legend/> no vale la pena para 2-3 series. */
function ChartLegend({ legend }) {
  return (
    <div className="flex flex-wrap gap-3 text-xs text-glyvex-muted mb-1">
      {legend.map((l) => (
        <span key={l.key} className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: l.color }} />
          {l.name}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function wsUrlFor(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

function tempClasses(temp) {
  if (temp == null) return "text-glyvex-muted";
  if (temp < 70) return "text-emerald-400";
  if (temp <= 85) return "text-amber-400";
  return "text-red-400";
}

function pctBarColor(pct) {
  if (pct == null) return "#9ca3af";
  if (pct < 50) return "#22c55e";
  if (pct <= 80) return "#f59e0b";
  return "#ef4444";
}

function ProgressBar({ value, max, color }) {
  const pct = max ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="w-full bg-black/30 rounded-full h-2.5 overflow-hidden">
      <div className="h-2.5 rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color || "#3b82f6" }} />
    </div>
  );
}

function Sparkline({ data, dataKeys, colors, height = 80, domain = [0, 100] }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <XAxis dataKey="t" hide />
        <YAxis hide domain={domain} />
        <Tooltip
          contentStyle={{ background: "#1a1a1a", border: "1px solid rgba(255,255,255,0.1)", fontSize: 11 }}
          labelFormatter={() => ""}
        />
        {dataKeys.map((key, i) => (
          <Line key={key} type="monotone" dataKey={key} stroke={colors[i]} strokeWidth={2} dot={false} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function CoreHeatmap({ cores }) {
  const cells = useMemo(() => {
    const cols = Math.min(8, cores.length) || 1;
    const size = 22;
    const gap = 4;
    return cores.map((pct, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const color = pct < 50 ? "#22c55e" : pct <= 80 ? "#f59e0b" : "#ef4444";
      return (
        <rect
          key={i}
          x={col * (size + gap)}
          y={row * (size + gap)}
          width={size}
          height={size}
          rx={3}
          fill={color}
          opacity={0.85}
        >
          <title>{`core ${i}: ${pct.toFixed(0)}%`}</title>
        </rect>
      );
    });
  }, [cores]);

  const cols = Math.min(8, cores.length) || 1;
  const rows = Math.ceil(cores.length / cols);
  const size = 22;
  const gap = 4;

  return (
    <svg width={cols * (size + gap) - gap} height={rows * (size + gap) - gap}>
      {cells}
    </svg>
  );
}

// ---------------------------------------------------------------------------

// Refresco automático del histórico mientras la sección está a la vista.
// 30s alcanza: el rango más corto (1h) igual se sirve en ventanas de 5s.
const HISTORY_REFRESH_MS = 30_000;

export default function Monitor() {
  const [snapshot, setSnapshot] = useState(null);
  const [history, setHistory] = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

  // -- Histórico persistente (data/metrics.db vía MetricsService) -----------
  const [range, setRange] = useState("24h");
  const [historyEnabled, setHistoryEnabled] = useState(true);
  const [historySeries, setHistorySeries] = useState({});
  const [historyTier, setHistoryTier] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(null);

  const historyKeys = useMemo(
    () => ["gpu.0.temp_c", "cpu.temp_c", "gpu.0.util_pct", "gpu.0.vram_pct", "cpu.total_pct", "ram.pct"],
    []
  );

  const fetchHistory = useMemo(
    () => async (signal) => {
      const rangeSeconds = RANGES.find((r) => r.id === range)?.seconds ?? 3600;
      const params = new URLSearchParams({ max_points: "600" });
      historyKeys.forEach((k) => params.append("key", k));
      params.set("start", String(Math.floor(Date.now() / 1000) - rangeSeconds));
      try {
        const res = await fetch(`/api/metrics/query?${params}`, { signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setHistoryEnabled(data.enabled !== false);
        setHistorySeries(data.series || {});
        setHistoryTier(data.tier || null);
        setHistoryError(null);
      } catch (err) {
        if (err.name !== "AbortError") setHistoryError("No se pudo cargar el histórico.");
      } finally {
        setHistoryLoading(false);
      }
    },
    [range, historyKeys]
  );

  useEffect(() => {
    const controller = new AbortController();
    setHistoryLoading(true);
    fetchHistory(controller.signal);
    const id = setInterval(() => fetchHistory(controller.signal), HISTORY_REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(id);
    };
  }, [fetchHistory]);

  const rangeSeconds = RANGES.find((r) => r.id === range)?.seconds ?? 3600;

  // -- LLM Server -------------------------------------------------------------
  const [llmProcesses, setLlmProcesses] = useState({ live: [], stored: [] });
  const [llmSelected, setLlmSelected] = useState(null);
  const [llmSeries, setLlmSeries] = useState({});
  const [llmTier, setLlmTier] = useState(null);
  const [llmResolution, setLlmResolution] = useState(null);
  const [llmLive, setLlmLive] = useState([]);

  // Opciones del selector: los que corren ahora primero, después el histórico.
  const llmOptions = useMemo(() => {
    const byId = new Map();
    for (const p of llmProcesses.live) byId.set(p.process_id, { ...p, live: true });
    for (const p of llmProcesses.stored) {
      const prev = byId.get(p.process_id);
      byId.set(p.process_id, { ...p, ...(prev || {}), first_ts: p.first_ts, last_ts: p.last_ts, live: Boolean(prev) });
    }
    return Array.from(byId.values());
  }, [llmProcesses]);

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      fetch("/api/llm-metrics/processes", { signal: controller.signal })
        .then((r) => (r.ok ? r.json() : { live: [], stored: [] }))
        .then((data) => setLlmProcesses({ live: data.live || [], stored: data.stored || [] }))
        .catch(() => {});
    load();
    const id = setInterval(load, HISTORY_REFRESH_MS);
    return () => { controller.abort(); clearInterval(id); };
  }, []);

  // Selección por defecto: el primero en vivo; si no hay, el más reciente
  // guardado. Si la lista vuelve vacía de paso (p. ej. un fetch falla), no se
  // pisa la selección actual: se conserva hasta que la lista vuelva.
  useEffect(() => {
    if (!llmOptions.length) return;
    if (llmSelected && llmOptions.some((o) => o.process_id === llmSelected)) return;
    setLlmSelected(llmOptions[0]?.process_id ?? null);
  }, [llmOptions, llmSelected]);

  const llmSelectedOption = llmOptions.find((o) => o.process_id === llmSelected) || null;

  useEffect(() => {
    if (!llmSelected) { setLlmSeries({}); return undefined; }
    const controller = new AbortController();
    const load = async () => {
      const params = new URLSearchParams({ max_points: "600", process_id: llmSelected });
      LLM_KEYS.forEach((k) => params.append("key", k));
      params.set("start", String(Math.floor(Date.now() / 1000) - rangeSeconds));
      try {
        const res = await fetch(`/api/metrics/query?${params}`, { signal: controller.signal });
        if (!res.ok) return;
        const data = await res.json();
        setLlmSeries(data.series || {});
        setLlmTier(data.tier || null);
        setLlmResolution(data.resolution_s || null);
      } catch { /* abortado o sin backend: se reintenta en el próximo ciclo */ }
    };
    load();
    const id = setInterval(load, HISTORY_REFRESH_MS);
    return () => { controller.abort(); clearInterval(id); };
  }, [llmSelected, rangeSeconds]);

  // En vivo: useLlmStream (buffer inicial por /history + WS con reconexión,
  // compartido con la tira del Launcher). Tener un cliente conectado hace
  // que el poller pase de 5 s a 1 s mientras el Monitor está abierto.
  const { connected: llmStreamConnected } = useLlmStream({
    processId: llmSelected,
    active: Boolean(llmSelectedOption?.live),
    onHistory: (data) => setLlmLive((prev) => mergeLiveSnaps(data, prev)),
    onSnap: (snap) => {
      if (!snap.timestamp) return;
      setLlmLive((prev) => mergeLiveSnaps(prev, [snap]));
    },
  });

  useEffect(() => {
    setLlmLive([]);
  }, [llmSelected]);

  const llmLast = llmLive[llmLive.length - 1] || null;
  const llmLastTg = [...llmLive].reverse().find((s) => s.tg_tps != null)?.tg_tps ?? null;

  // Últimos 5 minutos, medidos desde el último dato (no desde el reloj del navegador).
  const llmLiveWindow = useMemo(() => {
    const lastTs = snapTimestamp(llmLive[llmLive.length - 1]);
    return lastTs == null ? {} : liveSnapsToSeries(llmLive, lastTs - LLM_LIVE_WINDOW_S);
  }, [llmLive]);

  // Histórico + cola en vivo: el extremo derecho llega hasta ahora.
  const llmSeriesWithTail = useMemo(
    () => appendLiveTail(llmSeries, liveSnapsToSeries(llmLive), llmResolution),
    [llmSeries, llmLive, llmResolution]
  );

  const llmHasCtxPct =
    (llmSeriesWithTail["llm.ctx_pct"] || []).length > 0 || (llmLiveWindow["llm.ctx_pct"] || []).length > 0;
  const llmCtxPct = llmLast?.ctx_usage_ratio != null
    ? llmLast.ctx_usage_ratio * 100
    : llmLast?.ctx_peak != null && llmLast?.ctx_total
      ? (llmLast.ctx_peak / llmLast.ctx_total) * 100
      : null;

  useEffect(() => {
    let cancelled = false;

    fetch("/api/metrics/history")
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        const points = data.slice(-SPARKLINE_POINTS).map((snap, i) => ({
          t: i,
          gpu: snap.gpu?.[0]?.gpu_utilization ?? null,
          vram: snap.gpu?.[0]?.vram_percent ?? null,
          cpu: snap.cpu?.percent_total ?? null,
          ram: snap.ram?.percent ?? null,
        }));
        setHistory(points);
        if (data.length > 0) setSnapshot(data[data.length - 1]);
      })
      .catch(() => {});

    const ws = new WebSocket(wsUrlFor("/api/metrics/stream"));
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (event) => {
      const snap = JSON.parse(event.data);
      setSnapshot(snap);
      setHistory((prev) => {
        const next = [...prev, {
          t: prev.length,
          gpu: snap.gpu?.[0]?.gpu_utilization ?? null,
          vram: snap.gpu?.[0]?.vram_percent ?? null,
          cpu: snap.cpu?.percent_total ?? null,
          ram: snap.ram?.percent ?? null,
        }];
        return next.length > SPARKLINE_POINTS ? next.slice(next.length - SPARKLINE_POINTS) : next;
      });
    };

    return () => {
      cancelled = true;
      ws.close();
    };
  }, []);

  const gpu = snapshot?.gpu?.[0] ?? null;
  const cpu = snapshot?.cpu ?? null;
  const ram = snapshot?.ram ?? null;
  const processes = snapshot?.processes ?? [];

  const gpuTempAlert = gpu && gpu.temperature_c != null && gpu.temperature_c > 85;
  const vramAlert = gpu && gpu.vram_percent != null && gpu.vram_percent > 95;
  const anyAlert = gpuTempAlert || vramAlert;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Monitor</h1>
        <span className={`text-xs px-2 py-1 rounded-full border ${connected ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/10" : "text-glyvex-muted border-white/10"}`}>
          {connected ? "● en vivo" : "○ desconectado"}
        </span>
      </div>

      {/* Barra de alertas */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={`text-xs px-2.5 py-1 rounded-full border bg-glyvex-card ${tempClasses(gpu?.temperature_c)} border-white/10`}>
          Temp GPU: {gpu?.temperature_c != null ? `${gpu.temperature_c}°C` : "—"}
        </span>
        <span className="text-xs px-2.5 py-1 rounded-full border bg-glyvex-card border-white/10 text-glyvex-text">
          VRAM libre: {gpu ? `${(gpu.vram_free_mb / 1024).toFixed(1)} GB` : "—"}
        </span>
        {anyAlert && (
          <span className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-red-500/15 text-red-400 border border-red-500/30 font-medium">
            <AlertTriangle size={12} /> ALERTA
          </span>
        )}
      </div>

      {/* Card GPU */}
      <div className="bg-glyvex-card rounded-lg border border-white/10 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Gauge size={16} className="text-glyvex-muted" />
          <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">GPU</h2>
        </div>

        {!gpu ? (
          <p className="text-sm text-glyvex-muted">
            {snapshot?.gpu_error || "GPU NVIDIA no detectada"}
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-glyvex-text">{gpu.name}</p>
              <p className={`text-sm font-medium ${tempClasses(gpu.temperature_c)}`}>
                {gpu.temperature_c != null ? `${gpu.temperature_c}°C` : "—"}
              </p>
            </div>

            <div>
              <div className="flex justify-between text-xs text-glyvex-muted mb-1">
                <span>VRAM</span>
                <span>{(gpu.vram_used_mb / 1024).toFixed(1)} GB / {(gpu.vram_total_mb / 1024).toFixed(1)} GB ({gpu.vram_percent}%)</span>
              </div>
              <ProgressBar value={gpu.vram_used_mb} max={gpu.vram_total_mb} color={pctBarColor(gpu.vram_percent)} />
            </div>

            <div>
              <div className="flex justify-between text-xs text-glyvex-muted mb-1">
                <span>Utilización GPU</span>
                <span>{gpu.gpu_utilization ?? "—"}%</span>
              </div>
              <ProgressBar value={gpu.gpu_utilization ?? 0} max={100} color={pctBarColor(gpu.gpu_utilization)} />
            </div>

            <div className="flex flex-wrap gap-4 text-xs text-glyvex-muted">
              <span>Power: {gpu.power_draw_w ?? "—"}W / {gpu.power_limit_w ?? "—"}W</span>
              <span>Clock graphics: {gpu.clock_graphics_mhz ?? "—"} MHz</span>
              <span>Clock memory: {gpu.clock_memory_mhz ?? "—"} MHz</span>
            </div>

            <div>
              <p className="text-xs text-glyvex-muted mb-1">Últimos 60s — GPU util % / VRAM %</p>
              <Sparkline data={history} dataKeys={["gpu", "vram"]} colors={["#3b82f6", "#06b6d4"]} height={100} />
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Card CPU */}
        <div className="bg-glyvex-card rounded-lg border border-white/10 p-5 space-y-4">
          <div className="flex items-center gap-2">
            <Cpu size={16} className="text-glyvex-muted" />
            <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">CPU</h2>
          </div>
          {!cpu ? (
            <p className="text-sm text-glyvex-muted">{snapshot?.cpu_error || "psutil no disponible"}</p>
          ) : (
            <>
              <div className="flex justify-between text-sm">
                <span>Utilización total: <b>{cpu.percent_total.toFixed(1)}%</b></span>
                <span className="text-glyvex-muted">{cpu.frequency_mhz ? `${cpu.frequency_mhz} MHz` : ""}</span>
              </div>
              <div>
                <p className="text-xs text-glyvex-muted mb-2">Cores ({cpu.percent_per_core.length})</p>
                <CoreHeatmap cores={cpu.percent_per_core} />
              </div>
              <div>
                <p className="text-xs text-glyvex-muted mb-1">Últimos 60s — CPU %</p>
                <Sparkline data={history} dataKeys={["cpu"]} colors={["#3b82f6"]} height={80} />
              </div>
            </>
          )}
        </div>

        {/* Card RAM */}
        <div className="bg-glyvex-card rounded-lg border border-white/10 p-5 space-y-4">
          <div className="flex items-center gap-2">
            <MemoryStick size={16} className="text-glyvex-muted" />
            <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">RAM</h2>
          </div>
          {!ram ? (
            <p className="text-sm text-glyvex-muted">No disponible</p>
          ) : (
            <>
              <div>
                <div className="flex justify-between text-xs text-glyvex-muted mb-1">
                  <span>RAM</span>
                  <span>{ram.used_gb} GB / {ram.total_gb} GB ({ram.percent}%)</span>
                </div>
                <ProgressBar value={ram.used_gb} max={ram.total_gb} color={pctBarColor(ram.percent)} />
              </div>
              <p className="text-xs text-glyvex-muted">Swap: {ram.swap_used_gb} GB / {ram.swap_total_gb} GB</p>
              <div>
                <p className="text-xs text-glyvex-muted mb-1">Últimos 60s — RAM %</p>
                <Sparkline data={history} dataKeys={["ram"]} colors={["#06b6d4"]} height={80} />
              </div>
            </>
          )}
        </div>
      </div>

      {/* Histórico */}
      <div className="bg-glyvex-card rounded-lg border border-white/10 p-5 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <History size={16} className="text-glyvex-muted" />
            <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">Histórico</h2>
            {historyTier && (
              <span className="text-xs text-glyvex-muted">· {TIER_LABEL[historyTier] || historyTier}</span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setRange(r.id)}
                className={
                  "px-2.5 py-1 rounded-md text-xs border transition-colors " +
                  (range === r.id
                    ? "border-glyvex-accent/50 bg-glyvex-accent/15 text-glyvex-accent"
                    : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30")
                }
              >
                {r.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => { setHistoryLoading(true); fetchHistory(); }}
              title="Actualizar"
              className="p-1.5 rounded-md text-glyvex-muted hover:text-glyvex-text hover:bg-black/30"
            >
              <RefreshCw size={13} className={historyLoading ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        {!historyEnabled ? (
          <p className="text-sm text-glyvex-muted">
            El histórico está deshabilitado (<code className="font-mono">monitor.history_enabled</code> en Config).
          </p>
        ) : historyError ? (
          <p className="text-sm text-red-400">{historyError}</p>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div>
              <p className="text-xs text-glyvex-muted mb-1">Temperaturas (°C)</p>
              <ChartLegend legend={TEMP_LEGEND} />
              <HistoryChart
                series={historySeries}
                legend={TEMP_LEGEND}
                unit="°C"
                rangeSeconds={rangeSeconds}
              />
            </div>
            <div>
              <p className="text-xs text-glyvex-muted mb-1">Carga (%)</p>
              <ChartLegend legend={LOAD_LEGEND} />
              <HistoryChart
                series={historySeries}
                legend={LOAD_LEGEND}
                unit="%"
                domain={[0, 100]}
                rangeSeconds={rangeSeconds}
              />
            </div>
            <div>
              <p className="text-xs text-glyvex-muted mb-1">RAM (%)</p>
              <HistoryChart
                series={historySeries}
                legend={RAM_LEGEND}
                unit="%"
                domain={[0, 100]}
                rangeSeconds={rangeSeconds}
                height={140}
              />
            </div>
          </div>
        )}
      </div>

      {/* LLM Server */}
      <div className="bg-glyvex-card rounded-lg border border-white/10 p-5 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Server size={16} className="text-glyvex-muted" />
            <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">LLM Server</h2>
            {llmTier && llmSelected && (
              <span className="text-xs text-glyvex-muted">· {TIER_LABEL[llmTier] || llmTier} · rango {RANGES.find((r) => r.id === range)?.label}</span>
            )}
          </div>
          {llmOptions.length > 1 && (
            <select
              value={llmSelected || ""}
              onChange={(e) => setLlmSelected(e.target.value)}
              className="text-xs bg-black/30 border border-white/10 rounded-md px-2 py-1 text-glyvex-text max-w-full"
              aria-label="Proceso a mostrar"
            >
              {llmOptions.map((o) => (
                <option key={o.process_id} value={o.process_id}>
                  {o.model_name || o.process_id}
                  {o.live ? " — en vivo" : o.last_ts ? ` — hasta ${formatDateShort(o.last_ts)}` : ""}
                </option>
              ))}
            </select>
          )}
        </div>

        {llmOptions.length === 0 ? (
          <p className="text-sm text-glyvex-muted">
            Lanzá un modelo con llama-server desde el Launcher para ver sus métricas. Si el proceso ya estaba
            corriendo antes de esta versión, relanzalo: necesita el flag <code className="font-mono">--metrics</code>.
          </p>
        ) : (
          <>
            {llmSelectedOption?.live && !llmStreamConnected && llmLast && (
              <p className="text-xs text-amber-400" title="Los valores mostrados son del último snap recibido">
                Stream de métricas caído: reconectando… (valores obsoletos)
              </p>
            )}
            {llmSelectedOption?.live && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <div className="flex justify-between text-xs text-glyvex-muted mb-1">
                    <span>Generación</span>
                    <span
                      className={`font-medium tabular-nums ${llmLast?.tg_tps != null ? "text-glyvex-text" : "text-glyvex-muted"}`}
                      title={llmLast?.tg_tps != null ? "Velocidad actual" : "Sin generación ahora: último valor medido"}
                    >
                      {llmLastTg != null ? `${llmLastTg.toFixed(1)} t/s` : "—"}
                    </span>
                  </div>
                  <p className="text-xs text-glyvex-muted tabular-nums">
                    Throughput {llmLast?.throughput_tps != null ? `${llmLast.throughput_tps.toFixed(1)} t/s` : "—"}
                  </p>
                </div>
                <div>
                  <div className="flex justify-between text-xs text-glyvex-muted mb-1" title={contextTitle(llmLast)}>
                    <span>{llmLast?.ctx_used != null ? "Contexto" : "Contexto (pico)"}</span>
                    <span className="text-glyvex-text font-medium tabular-nums">
                      {llmLast?.ctx_used != null
                        ? `${llmLast.ctx_used.toLocaleString()} / ${llmLast.ctx_total?.toLocaleString() ?? "—"}`
                        : llmLast?.ctx_peak != null
                          ? `${llmLast.ctx_peak.toLocaleString()} / ${llmLast.ctx_total?.toLocaleString() ?? "—"}`
                          : "—"}
                    </span>
                  </div>
                  <ProgressBar value={llmCtxPct ?? 0} max={100} color={pctBarColor(llmCtxPct)} />
                </div>
                <div>
                  <div className="flex justify-between text-xs text-glyvex-muted mb-1">
                    <span>Cola</span>
                    <span className="text-glyvex-text font-medium tabular-nums">
                      {llmLast?.requests_processing ?? "—"} en curso
                      {llmLast?.requests_deferred ? <span className="text-amber-400"> · {llmLast.requests_deferred} en espera</span> : null}
                    </span>
                  </div>
                  {(llmLast?.cache_hit_pct_total != null || llmLast?.spec_accept_pct_total != null) && (
                    <p className="text-xs text-glyvex-muted mt-1.5 tabular-nums">
                      {llmLast.cache_hit_pct_total != null && (
                        <span title="Tokens de prompt reutilizados del caché desde el arranque">
                          Caché {llmLast.cache_hit_pct_total.toFixed(1)} %
                        </span>
                      )}
                      {llmLast.cache_hit_pct_total != null && llmLast.spec_accept_pct_total != null && " · "}
                      {llmLast.spec_accept_pct_total != null && (
                        <span title="Tokens del draft (MTP) aceptados desde el arranque">
                          MTP {llmLast.spec_accept_pct_total.toFixed(1)} % aceptado
                        </span>
                      )}
                    </p>
                  )}
                  {llmLast && !llmLast.scrape_ok && llmLast.scrape_error && (
                    <p className="text-xs text-amber-400">{llmLast.scrape_error}</p>
                  )}
                </div>
              </div>
            )}

            {llmSelectedOption?.live && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
                <div>
                  <p className="text-xs text-glyvex-muted mb-1">Últimos 5 min · velocidad (t/s)</p>
                  <ChartLegend legend={LLM_SPEED_LEGEND} />
                  <HistoryChart
                    series={llmLiveWindow} legend={LLM_SPEED_LEGEND} unit=" t/s"
                    rangeSeconds={LLM_LIVE_WINDOW_S} height={130} connectGaps={false}
                    emptyText="Esperando datos en vivo…"
                  />
                </div>
                <div>
                  <p className="text-xs text-glyvex-muted mb-1">
                    Últimos 5 min · {llmHasCtxPct ? "contexto (%)" : "pico de contexto"}
                  </p>
                  <ChartLegend legend={llmHasCtxPct ? LLM_CTX_PCT_LEGEND : LLM_CTX_PEAK_LEGEND} />
                  <HistoryChart
                    series={llmLiveWindow}
                    legend={llmHasCtxPct ? LLM_CTX_PCT_LEGEND : LLM_CTX_PEAK_LEGEND}
                    unit={llmHasCtxPct ? "%" : ""}
                    domain={llmHasCtxPct ? [0, 100] : undefined}
                    rangeSeconds={LLM_LIVE_WINDOW_S} height={130} connectGaps={false}
                    emptyText="Esperando datos en vivo…"
                  />
                </div>
                <div>
                  <p className="text-xs text-glyvex-muted mb-1">Últimos 5 min · cola</p>
                  <ChartLegend legend={LLM_QUEUE_LEGEND} />
                  <HistoryChart
                    series={llmLiveWindow} legend={LLM_QUEUE_LEGEND}
                    rangeSeconds={LLM_LIVE_WINDOW_S} height={130} connectGaps={false}
                    emptyText="Esperando datos en vivo…"
                  />
                </div>
              </div>
            )}

            <p className="text-xs text-glyvex-muted uppercase tracking-wide pt-1">
              Histórico · {RANGES.find((r) => r.id === range)?.label}
              {llmSelectedOption?.live ? " · el último tramo se completa en vivo" : ""}
            </p>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <div>
                <p className="text-xs text-glyvex-muted mb-1">Velocidad (t/s)</p>
                <ChartLegend legend={LLM_SPEED_LEGEND} />
                <HistoryChart series={llmSeriesWithTail} legend={LLM_SPEED_LEGEND} unit=" t/s" rangeSeconds={rangeSeconds} />
              </div>
              <div>
                <p className="text-xs text-glyvex-muted mb-1">
                  {llmHasCtxPct ? "Uso de contexto (%)" : "Pico de contexto (tokens)"}
                </p>
                <ChartLegend legend={llmHasCtxPct ? LLM_CTX_PCT_LEGEND : LLM_CTX_PEAK_LEGEND} />
                <HistoryChart
                  series={llmSeriesWithTail}
                  legend={llmHasCtxPct ? LLM_CTX_PCT_LEGEND : LLM_CTX_PEAK_LEGEND}
                  unit={llmHasCtxPct ? "%" : ""}
                  domain={llmHasCtxPct ? [0, 100] : undefined}
                  rangeSeconds={rangeSeconds}
                />
              </div>
              <div>
                <p className="text-xs text-glyvex-muted mb-1">Cola de requests</p>
                <ChartLegend legend={LLM_QUEUE_LEGEND} />
                <HistoryChart series={llmSeriesWithTail} legend={LLM_QUEUE_LEGEND} rangeSeconds={rangeSeconds} height={140} />
              </div>
            </div>
          </>
        )}
      </div>

      {/* Card procesos */}
      <div className="bg-glyvex-card rounded-lg border border-white/10 p-5">
        <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide mb-3">Procesos activos</h2>
        {processes.length === 0 ? (
          <p className="text-sm text-glyvex-muted">No hay procesos de llama-server/ollama corriendo.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-glyvex-muted">
                  <th className="px-3 py-1.5 font-medium">PID</th>
                  <th className="px-3 py-1.5 font-medium">Nombre</th>
                  <th className="px-3 py-1.5 font-medium">CPU %</th>
                  <th className="px-3 py-1.5 font-medium">RAM MB</th>
                  <th className="px-3 py-1.5 font-medium">Threads</th>
                  <th className="px-3 py-1.5 font-medium">Uptime</th>
                </tr>
              </thead>
              <tbody>
                {processes.map((p) => (
                  <tr key={p.pid} className="border-t border-white/10">
                    <td className="px-3 py-1.5 text-glyvex-muted">{p.pid}</td>
                    <td className="px-3 py-1.5">{p.name}</td>
                    <td className="px-3 py-1.5 text-glyvex-muted">{p.cpu_percent.toFixed(1)}</td>
                    <td className="px-3 py-1.5 text-glyvex-muted">{p.ram_rss_mb.toFixed(0)}</td>
                    <td className="px-3 py-1.5 text-glyvex-muted">{p.threads}</td>
                    <td className="px-3 py-1.5 text-glyvex-muted">{Math.round(p.uptime_s)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

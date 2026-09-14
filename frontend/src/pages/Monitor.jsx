import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Cpu, MemoryStick, Gauge } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from "recharts";

const SPARKLINE_POINTS = 60;

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

function Sparkline({ data, dataKeys, colors, height = 80 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <XAxis dataKey="t" hide />
        <YAxis hide domain={[0, 100]} />
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

export default function Monitor() {
  const [snapshot, setSnapshot] = useState(null);
  const [history, setHistory] = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

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

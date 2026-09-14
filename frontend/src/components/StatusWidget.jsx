import { useEffect, useState } from "react";
import { Thermometer } from "lucide-react";

const POLL_INTERVAL_MS = 5000;

function tempColorClass(temp) {
  if (temp == null) return "text-glyvex-muted";
  if (temp < 70) return "text-emerald-400";
  if (temp <= 85) return "text-amber-400";
  return "text-red-400";
}

/**
 * Widget compacto para la navbar: modelo activo (launcher) + mini resumen de
 * GPU (metrics). Pollea GET /api/state cada 5s con cleanup en useEffect —
 * nunca deja un setInterval corriendo después de desmontarse.
 */
export default function StatusWidget() {
  const [state, setState] = useState(null);

  useEffect(() => {
    let cancelled = false;

    function poll() {
      fetch("/api/state")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) setState(data);
        })
        .catch(() => {});
    }

    poll();
    const intervalId = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const runningProcess = state?.active_processes?.find((p) => p.state === "running");
  const gpu = state?.gpu?.[0] ?? null;

  return (
    <div className="flex items-center gap-4 text-xs">
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${runningProcess ? "bg-emerald-400" : "bg-glyvex-muted/50"}`} />
        <span className={runningProcess ? "text-glyvex-text" : "text-glyvex-muted"}>
          {runningProcess ? `${runningProcess.model_name} — :${runningProcess.port}` : "Sin modelo activo"}
        </span>
      </div>

      {gpu && (
        <div className="flex items-center gap-1.5 text-glyvex-muted">
          <Thermometer size={12} className={tempColorClass(gpu.temperature_c)} />
          <span className={tempColorClass(gpu.temperature_c)}>
            {gpu.temperature_c != null ? `${gpu.temperature_c}°C` : "—"}
          </span>
          <span>
            VRAM {(gpu.vram_used_mb / 1024).toFixed(1)}/{(gpu.vram_total_mb / 1024).toFixed(0)}GB
          </span>
        </div>
      )}
    </div>
  );
}

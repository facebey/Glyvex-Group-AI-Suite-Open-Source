import { useEffect, useState } from "react";
import { Thermometer } from "lucide-react";
import { useTranslation } from "react-i18next";

const POLL_INTERVAL_MS = 5000;

function tempBadgeClasses(temp) {
  if (temp == null) return "text-glyvex-bg-muted bg-glyvex-veil border-glyvex-border-soft";
  if (temp < 70) return "text-glyvex-ok bg-glyvex-ok/10 border-glyvex-ok/30";
  if (temp <= 85) return "text-glyvex-warn bg-glyvex-warn/10 border-glyvex-warn/30";
  return "text-glyvex-crit bg-glyvex-crit/10 border-glyvex-crit/30";
}

/**
 * Widget compacto para la navbar: modelo activo (launcher) + mini resumen de
 * GPU (metrics). Pollea GET /api/state cada 5s con cleanup en useEffect —
 * nunca deja un setInterval corriendo después de desmontarse.
 */
export default function StatusWidget() {
  const { t } = useTranslation();
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
  const gpus = state?.gpu ?? [];

  return (
    <div className="flex items-center gap-4 text-xs">
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${runningProcess ? "bg-emerald-400" : "bg-glyvex-muted/50"}`} />
        {/* Solo cuenta procesos lanzados desde la suite: un llama-server
            arrancado por fuera no aparece acá aunque el chat lo use. */}
        <span
          className={runningProcess ? "text-glyvex-bg-text" : "text-glyvex-bg-muted"}
          title={runningProcess ? undefined : t("statusWidget.noModelHint")}
        >
          {runningProcess ? `${runningProcess.model_name} — :${runningProcess.port}` : t("statusWidget.noModel")}
        </span>
      </div>

      {/* Una entrada por GPU: en sistemas multi-GPU cada tarjeta muestra su
          propia temp y VRAM (con índice para distinguirlas). */}
      {gpus.map((g) => (
        <div key={g.index} className="flex items-center gap-2">
          {gpus.length > 1 && (
            <span className="text-glyvex-bg-muted">{t("monitor.gpu.index", { index: g.index })}</span>
          )}
          <span className={`flex items-center gap-1 text-sm font-semibold px-2 py-0.5 rounded-md border ${tempBadgeClasses(g.temperature_c)}`}>
            <Thermometer size={14} />
            {g.temperature_c != null ? `${g.temperature_c}°C` : "—"}
          </span>
          <span className="text-xs text-glyvex-bg-muted">
            VRAM {(g.vram_used_mb / 1024).toFixed(1)}/{(g.vram_total_mb / 1024).toFixed(0)}GB
          </span>
        </div>
      ))}
    </div>
  );
}

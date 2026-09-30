import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, AlertTriangle, RefreshCw } from "lucide-react";
import { getBackendPort, isTauri, onSidecarState, restartSidecar } from "../lib/tauri.js";

/**
 * Estado del sidecar (glyvex-backend.exe) dentro de la shell Tauri.
 * Escucha el evento "sidecar-state" que emite Rust:
 *  - starting → splash con spinner (también cubre los reintentos automáticos)
 *  - running  → se oculta
 *  - error    → pantalla de error con botón de reintento
 * En el navegador de dev no existe el sidecar: el componente no hace nada.
 */
export default function SidecarOverlay() {
  const { t } = useTranslation();
  const [evt, setEvt] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten = null;
    let cancelled = false;
    // Si la página se carga directamente desde el backend (tras el redirect
    // de main.jsx), el evento "sidecar-state" ya se emitió: preguntar el
    // puerto evita quedar atorado en el splash.
    getBackendPort()
      .then((port) =>
        setEvt({ state: "running", attempt: 0, url: `http://127.0.0.1:${port}`, message: null })
      )
      .catch(() => {});
    onSidecarState(setEvt)
      .then((fn) => {
        if (cancelled) fn();
        else unlisten = fn;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  if (!evt || evt.state === "running") return null;

  async function handleRetry() {
    setBusy(true);
    try {
      await restartSidecar();
      setEvt({ state: "starting", attempt: 1, url: null, message: null });
    } finally {
      setBusy(false);
    }
  }

  if (evt.state === "starting") {
    return (
      <div className="fixed inset-0 z-[100] bg-glyvex-bg flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 size={28} className="animate-spin text-glyvex-accent" />
          <p className="text-sm text-glyvex-muted">
            {evt.attempt > 1
              ? t("sidecar.startingRetry", { attempt: evt.attempt })
              : t("sidecar.starting")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[100] bg-glyvex-bg/95 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="max-w-sm w-full bg-glyvex-card border border-glyvex-border rounded-lg p-6 space-y-4 text-center">
        <AlertTriangle size={32} className="mx-auto text-red-400" />
        <h2 className="text-lg font-semibold">{t("sidecar.errorTitle")}</h2>
        {evt.message && (
          <p className="text-sm text-red-400/80 break-words">{evt.message}</p>
        )}
        <p className="text-xs text-glyvex-muted">{t("sidecar.hint")}</p>
        <button
          type="button"
          onClick={handleRetry}
          disabled={busy}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-glyvex-accent/15 text-glyvex-accent border border-glyvex-accent/40 hover:bg-glyvex-accent/25 disabled:opacity-50"
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
          {t("sidecar.retry")}
        </button>
      </div>
    </div>
  );
}

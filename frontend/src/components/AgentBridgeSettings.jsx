import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import Section from "./ui/Section.jsx";
import { useTranslation } from "react-i18next";

/**
 * Sección "Integración con OpenCode" de Config (AG-1). El toggle vive en la
 * config general (agent_bridge.enabled) y se guarda con el botón Guardar de
 * la página; acá abajo se muestra el estado real del bridge desde
 * GET /api/agent-bridge/status para poder verificar qué bloques publicó.
 */
export default function AgentBridgeSettings({ bridgeConfig, onChange }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(false);

  async function fetchStatus() {
    try {
      const res = await fetch("/api/agent-bridge/status");
      if (!res.ok) throw new Error("bridge_status_failed");
      setStatus(await res.json());
      setStatusError(false);
    } catch {
      setStatusError(true);
    }
  }

  useEffect(() => {
    fetchStatus();
  }, []);

  const enabled = Boolean(bridgeConfig?.enabled);
  const dirty = status && status.enabled !== enabled;

  return (
    <Section title={t("config.agentBridge.section")}>
      <p className="text-xs text-glyvex-muted">{t("config.agentBridge.hint")}</p>

      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          className="mt-0.5 accent-glyvex-accent"
          checked={enabled}
          onChange={(e) => onChange({ ...bridgeConfig, enabled: e.target.checked })}
        />
        <span>
          <span className="text-glyvex-text">{t("config.agentBridge.toggle")}</span>
          <span className="block text-xs text-glyvex-muted">{t("config.agentBridge.toggleHint")}</span>
        </span>
      </label>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-glyvex-muted">
            {t("config.agentBridge.liveTitle")}
          </span>
          {status && (
            <span
              className={`px-2 py-0.5 rounded border text-xs font-medium ${
                status.enabled
                  ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                  : "border-glyvex-border-soft text-glyvex-muted"
              }`}
            >
              {status.enabled ? t("config.agentBridge.enabled") : t("config.agentBridge.disabled")}
            </span>
          )}
          <button
            type="button"
            onClick={fetchStatus}
            className="inline-flex items-center gap-1.5 text-xs text-glyvex-muted hover:text-glyvex-text"
          >
            <RefreshCw size={12} />
            {t("config.agentBridge.refresh")}
          </button>
        </div>

        {dirty && (
          <p className="text-xs text-amber-400">{t("config.agentBridge.pendingApply")}</p>
        )}

        {statusError && (
          <p className="text-xs text-red-400">{t("config.agentBridge.statusError")}</p>
        )}

        {status && (
          <div className="space-y-2 text-xs">
            <p className="text-glyvex-muted">
              {t("config.agentBridge.configPath")}:{" "}
              <span className="font-mono text-glyvex-text">{status.config_path}</span>
            </p>
            {status.style && (
              <p className="text-glyvex-muted">
                {t("config.agentBridge.style", { style: status.style.toUpperCase() })}
              </p>
            )}
            {!status.file_exists && (
              <p className="text-glyvex-muted">{t("config.agentBridge.fileMissing")}</p>
            )}
            <p className="text-glyvex-muted">
              {t("config.agentBridge.keys")}:{" "}
              {status.bridge_keys.length > 0 ? (
                <span className="font-mono text-glyvex-text">{status.bridge_keys.join(", ")}</span>
              ) : (
                t("config.agentBridge.noKeys")
              )}
            </p>

            <div>
              <p className="text-glyvex-muted mb-1">{t("config.agentBridge.publishedTitle")}</p>
              {status.published.length === 0 ? (
                <p className="text-glyvex-muted">{t("config.agentBridge.publishedEmpty")}</p>
              ) : (
                <ul className="space-y-1">
                  {status.published.map((p) => (
                    <li key={p.process_id} className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-glyvex-text">{p.model_name}</span>
                      <span className="text-glyvex-muted">
                        {p.host}:{p.port}
                      </span>
                      {p.vision && (
                        <span className="px-1.5 py-0.5 rounded border border-sky-500/40 text-sky-300 bg-sky-500/10 text-[10px] font-medium">
                          {t("config.agentBridge.publishedVision")}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {status.last_error && (
              <p className="flex items-start gap-1.5 text-amber-400">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                {t("config.agentBridge.lastError", { error: status.last_error })}
              </p>
            )}
          </div>
        )}
      </div>
    </Section>
  );
}

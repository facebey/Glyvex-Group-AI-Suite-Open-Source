import { useCallback, useEffect, useState } from "react";
import { DollarSign, RotateCcw } from "lucide-react";
import CollapsiblePanel from "./ui/CollapsiblePanel.jsx";
import SavingsHero, { SavingsChip } from "./SavingsHero.jsx";
import { cloudCost, formatTokens } from "../lib/sessionCost.js";
import { useTranslation } from "react-i18next";

// MON-2: totales de la sesión local vs precios de nube de referencia.
const POLL_MS = 10_000;
const PROVIDER_KEY = "glyvex_session_cost_provider";

function Stat({ label, value }) {
  return (
    <div className="rounded-md border border-glyvex-border-soft p-3">
      <p className="text-xs text-glyvex-muted">{label}</p>
      <p className="text-lg font-medium text-glyvex-text tabular-nums">{value}</p>
    </div>
  );
}

export default function SessionCostPanel() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [providerId, setProviderId] = useState(() => {
    try { return localStorage.getItem(PROVIDER_KEY) || ""; } catch { return ""; }
  });

  const load = useCallback(async (signal) => {
    try {
      const res = await fetch("/api/metrics/session-tokens", { signal });
      if (res.ok) setData(await res.json());
    } catch { /* abort de navegación: se ignora */ }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const id = setInterval(() => load(), POLL_MS);
    return () => { controller.abort(); clearInterval(id); };
  }, [load]);

  const tokens = data?.tokens || {};
  const pricing = data?.pricing || {};
  const providers = pricing.providers || [];
  // Proveedor elegido (persistido) → default del config → el primero.
  const provider =
    providers.find((p) => p.id === providerId) ||
    providers.find((p) => p.id === pricing.default_provider_id) ||
    providers[0] || null;

  function handleProviderChange(e) {
    setProviderId(e.target.value);
    try { localStorage.setItem(PROVIDER_KEY, e.target.value); } catch { /* sin storage */ }
  }

  async function handleReset() {
    if (!window.confirm(t("monitor.sessionCost.resetConfirm"))) return;
    setBusy(true);
    try {
      const res = await fetch("/api/metrics/session-tokens/reset", { method: "POST" });
      if (res.ok) {
        const fresh = await res.json();
        setData((prev) => (prev ? { ...prev, tokens: fresh } : prev));
      }
    } finally {
      setBusy(false);
    }
  }

  const hasUsage = (tokens.turns || 0) > 0;
  const cost = provider ? cloudCost(tokens, provider) : 0;

  return (
    <CollapsiblePanel
      icon={DollarSign}
      title={t("monitor.sessionCost.title")}
      storageKey="monitor_panel_session_cost"
      // El ahorro es el dato de marketing del panel: visible en el header aun
      // colapsado (slot headerRight de CollapsiblePanel).
      headerRight={hasUsage && provider && cost > 0 ? <SavingsChip cost={cost} currency={pricing.currency} /> : undefined}
    >
      {!hasUsage ? (
        <p className="text-sm text-glyvex-muted">{t("monitor.sessionCost.noUsage")}</p>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label={t("monitor.sessionCost.input")} value={formatTokens(tokens.prompt_tokens)} />
            <Stat label={t("monitor.sessionCost.output")} value={formatTokens(tokens.completion_tokens)} />
            <Stat label={t("monitor.sessionCost.cached")} value={formatTokens(tokens.cached_tokens)} />
            <Stat label={t("monitor.sessionCost.turns")} value={String(tokens.turns)} />
          </div>

          <div className="rounded-md border border-glyvex-border-soft p-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-glyvex-muted">{t("monitor.sessionCost.providerLabel")}</span>
              <select
                value={provider?.id || ""}
                onChange={handleProviderChange}
                className="text-xs bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-2 py-1 text-glyvex-text max-w-full"
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            {provider ? null : (
              <p className="text-xs text-glyvex-muted">{t("monitor.sessionCost.noPricing")}</p>
            )}
          </div>

          {provider && <SavingsHero savings={cost} reference={cost} providerName={provider.name} currency={pricing.currency} />}

          <button
            type="button"
            onClick={handleReset}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled disabled:opacity-50 transition-colors"
          >
            <RotateCcw size={12} />
            {t("monitor.sessionCost.reset")}
          </button>
        </div>
      )}
    </CollapsiblePanel>
  );
}

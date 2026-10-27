import { useCallback, useEffect, useRef, useState } from "react";
import { Server } from "lucide-react";
import CollapsiblePanel from "./ui/CollapsiblePanel.jsx";
import SavingsHero, { SavingsChip } from "./SavingsHero.jsx";
import { cloudCost, formatMoney, formatTokens } from "../lib/sessionCost.js";
import { totalTokens, usageForPricing, lifetimeModelRows, lifetimeSplitRows } from "../lib/serverConsumption.js";
import { useTranslation } from "react-i18next";

// MON-4: tokens acumulados por instancia de llama-server (todos los clientes,
// reinicios mergeados por el backend) vs precios de nube de referencia.
// MON-5: total de por vida de todas las instancias (/consumption/all).
const POLL_MS = 10_000;
const PROVIDER_KEY = "glyvex_server_consumption_provider";

function Stat({ label, value }) {
  return (
    <div className="rounded-md border border-glyvex-border-soft p-3">
      <p className="text-xs text-glyvex-muted">{label}</p>
      <p className="text-lg font-medium text-glyvex-text tabular-nums">{value}</p>
    </div>
  );
}

// null significa "este build no expone ese contador", no "0": se muestra em dash.
const fmt = (n) => (n == null ? "—" : formatTokens(n));

export default function ServerConsumptionPanel() {
  const { t, i18n } = useTranslation();
  const [entries, setEntries] = useState([]);
  const [data, setData] = useState(null);
  const [life, setLife] = useState(null);
  const [providerId, setProviderId] = useState(() => {
    try { return localStorage.getItem(PROVIDER_KEY) || ""; } catch { return ""; }
  });
  // El loop de fetch lee selectedRef en cada tick; el select lo escribe al
  // instante. La elección sobrevive a los refrescos de la lista mientras el
  // proceso siga existiendo.
  const [selected, setSelected] = useState("");
  const selectedRef = useRef("");

  const load = useCallback(async (signal) => {
    try {
      const res = await fetch("/api/llm-metrics/processes", { signal });
      if (!res.ok) return;
      const list = await res.json();
      // Vivos primero: son lo que uno quiere mirar; los guardados después.
      const merged = [...(list.live || []), ...(list.stored || [])];
      setEntries(merged);
      // El gran total (MON-5) se pide siempre, aunque no haya procesos vivos:
      // el histórico de por vida sobrevive a que la lista quede vacía.
      const all = await fetch("/api/llm-metrics/consumption/all", { signal });
      if (all.ok) setLife(await all.json());
      const current =
        merged.find((p) => p.process_id === selectedRef.current) || merged[0] || null;
      if (!current) {
        selectedRef.current = "";
        setSelected("");
        setData(null);
        return;
      }
      if (current.process_id !== selectedRef.current) {
        selectedRef.current = current.process_id;
        setSelected(current.process_id);
      }
      const c = await fetch(`/api/llm-metrics/${current.process_id}/consumption`, { signal });
      if (c.ok) setData(await c.json());
    } catch { /* abort por desmontaje: ignora */ }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const id = setInterval(() => load(), POLL_MS);
    return () => { controller.abort(); clearInterval(id); };
  }, [load]);

  function handleProcessChange(e) {
    selectedRef.current = e.target.value;
    setSelected(e.target.value);
    load();
  }

  // El pricing por proceso manda; si no hay proceso seleccionado, el embebido
  // en el total global permite calcular costos de todos modos.
  const pricing = data?.pricing || life?.pricing || {};
  const providers = pricing.providers || [];
  const provider =
    providers.find((p) => p.id === providerId) ||
    providers.find((p) => p.id === pricing.default_provider_id) ||
    providers[0] || null;

  function handleProviderChange(e) {
    setProviderId(e.target.value);
    try { localStorage.setItem(PROVIDER_KEY, e.target.value); } catch { /* sin storage */ }
  }

  const hasData =
    data != null &&
    (data.prompt_tokens != null || data.cached_tokens != null || data.predicted_tokens != null);
  const cost = provider ? cloudCost(usageForPricing(data), provider) : 0;

  // MON-5: el gran total existe si alguna serie aportó algún contador.
  const hasLife =
    life != null &&
    (life.prompt_tokens != null || life.cached_tokens != null || life.predicted_tokens != null);
  const lifeCost = provider ? cloudCost(usageForPricing(life), provider) : 0;
  const modelRows = lifetimeModelRows(life, provider);
  const lifeSince =
    life?.first_ts != null
      ? new Date(life.first_ts * 1000).toLocaleDateString(i18n.language)
      : null;
  // Desglose chat vs externo (MON-5): chat_since es un ISO string (viene de
  // session_tokens), los ts del motor son epoch seconds — formatos distintos.
  const splitRows = lifetimeSplitRows(life?.split, provider);
  const splitTotal = splitRows.reduce((s, r) => s + r.tokens, 0);
  const chatSinceIso = life?.split?.chat?.since;
  const chatSince = chatSinceIso ? new Date(chatSinceIso).toLocaleDateString(i18n.language) : null;

  return (
    <CollapsiblePanel
      icon={Server}
      title={t("monitor.serverConsumption.title")}
      storageKey="monitor_panel_server_consumption"
      // Chip con el ahorro de por vida en el header: visible aun colapsado
      // (slot headerRight de CollapsiblePanel).
      headerRight={hasLife && provider && lifeCost > 0 ? <SavingsChip cost={lifeCost} currency={pricing.currency} /> : undefined}
    >
      {entries.length === 0 && !hasLife && (
        <p className="text-sm text-glyvex-muted">{t("monitor.serverConsumption.noProcesses")}</p>
      )}
      {entries.length > 0 && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-glyvex-muted">{t("monitor.serverConsumption.processLabel")}</span>
            <select
              value={selected}
              onChange={handleProcessChange}
              className="text-xs bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-2 py-1 text-glyvex-text max-w-full"
            >
              {entries.map((p) => (
                <option key={p.process_id} value={p.process_id}>
                  {p.model_name || p.process_id}{p.state ? ` · ${p.state}` : ""}
                </option>
              ))}
            </select>
          </div>
          {!hasData ? (
            <p className="text-sm text-glyvex-muted">{t("monitor.serverConsumption.noData")}</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <Stat label={t("monitor.serverConsumption.input")} value={fmt(data.prompt_tokens)} />
                <Stat label={t("monitor.serverConsumption.cached")} value={fmt(data.cached_tokens)} />
                <Stat label={t("monitor.serverConsumption.output")} value={fmt(data.predicted_tokens)} />
                <Stat label={t("monitor.serverConsumption.total")} value={formatTokens(totalTokens(data))} />
              </div>
              {/* Rotulado de vida de la instancia, no por sesión (espec MON-4). */}
              <p className="text-xs text-glyvex-muted">
                {t("monitor.serverConsumption.sinceStart")}
                {data.runs > 1 && <> · {t("monitor.serverConsumption.merged", { n: data.runs })}</>}
              </p>
              <div className="rounded-md border border-glyvex-border-soft p-3">
                {providers.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-glyvex-muted">{t("monitor.serverConsumption.providerLabel")}</span>
                    <select
                      value={provider?.id || ""}
                      onChange={handleProviderChange}
                      className="text-xs bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-2 py-1 text-glyvex-text max-w-full"
                    >
                      {providers.map((p) => (
                        <option key={p.id} value={p.id}>{p.name ?? p.id}</option>
                      ))}
                    </select>
                  </div>
                )}
                {!provider && (
                  <p className="text-sm text-glyvex-muted">{t("monitor.serverConsumption.noPricing")}</p>
                )}
              </div>
              {provider && (
                <SavingsHero
                  savings={cost}
                  reference={cost}
                  providerName={provider.name ?? provider.id}
                  currency={pricing.currency}
                />
              )}
            </>
          )}
        </div>
      )}
      {hasLife && (
        <div className="space-y-4 border-t border-glyvex-border-soft pt-4">
          <div>
            <p className="text-sm font-medium text-glyvex-text">
              {t("monitor.serverConsumption.lifetimeTitle")}
            </p>
            <p className="text-xs text-glyvex-muted">
              {t("monitor.serverConsumption.lifetimeMeta", { n: life.servers })}
              {lifeSince && <> · {t("monitor.serverConsumption.lifetimeSince", { date: lifeSince })}</>}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label={t("monitor.serverConsumption.input")} value={fmt(life.prompt_tokens)} />
            <Stat label={t("monitor.serverConsumption.cached")} value={fmt(life.cached_tokens)} />
            <Stat label={t("monitor.serverConsumption.output")} value={fmt(life.predicted_tokens)} />
            <Stat label={t("monitor.serverConsumption.total")} value={formatTokens(totalTokens(life))} />
          </div>
          {provider && (
            <SavingsHero
              savings={lifeCost}
              reference={lifeCost}
              providerName={provider.name ?? provider.id}
              currency={pricing.currency}
            />
          )}
          {modelRows.length > 0 && (
            <div className="rounded-md border border-glyvex-border-soft p-3 space-y-2">
              <p className="text-xs text-glyvex-muted">
                {t("monitor.serverConsumption.lifetimeModelsLabel")}
              </p>
              {modelRows.map((r, i) => (
                <p key={r.model_name || i} className="flex flex-wrap items-baseline gap-x-2 text-sm text-glyvex-text">
                  <span className="font-medium">{r.model_name || t("monitor.serverConsumption.unknownModel")}</span>
                  <span className="tabular-nums">{formatTokens(r.tokens)}</span>
                  {provider && (
                    <>
                      <span className="text-xs text-glyvex-muted">
                        {t("monitor.serverConsumption.lifetimeModelCost")}
                      </span>
                      <span className="text-xs font-medium text-glyvex-accent tabular-nums">
                        {formatMoney(r.cost)}
                      </span>
                    </>
                  )}
                </p>
              ))}
            </div>
          )}
          {splitRows.length > 0 && (
            <div className="rounded-md border border-glyvex-border-soft p-3 space-y-2">
              <p className="text-xs text-glyvex-muted">
                {t("monitor.serverConsumption.splitLabel")}
              </p>
              {splitRows.map((r) => (
                <p key={r.key} className="flex flex-wrap items-baseline gap-x-2 text-sm text-glyvex-text">
                  <span className="font-medium">
                    {r.key === "chat"
                      ? t("monitor.serverConsumption.splitChat")
                      : t("monitor.serverConsumption.splitExternal")}
                  </span>
                  <span className="tabular-nums">{formatTokens(r.tokens)}</span>
                  {splitTotal > 0 && (
                    <span className="text-xs text-glyvex-muted">
                      {t("monitor.serverConsumption.splitShare", {
                        share: ((r.tokens / splitTotal) * 100).toFixed(1),
                      })}
                    </span>
                  )}
                  {provider && (
                    <>
                      <span className="text-xs text-glyvex-muted">
                        {t("monitor.serverConsumption.lifetimeModelCost")}
                      </span>
                      <span className="text-xs font-medium text-glyvex-accent tabular-nums">
                        {formatMoney(r.cost)}
                      </span>
                    </>
                  )}
                </p>
              ))}
              {chatSince && (
                <p className="text-[11px] text-glyvex-muted">
                  {t("monitor.serverConsumption.splitNote", { date: chatSince })}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </CollapsiblePanel>
  );
}

import { useState } from "react";
import { Check, Copy, Loader2 } from "lucide-react";
import Section from "./ui/Section.jsx";
import Field from "./ui/Field.jsx";
import { formInputClasses } from "../lib/styles.js";
import { useTranslation } from "react-i18next";

const INFLUX_DEFAULTS = {
  enabled: false, version: "v2", url: "http://127.0.0.1:8086", token: "", org: "",
  bucket: "glyvex", database: "glyvex", username: "", password: "", interval_s: 10,
  measurement_prefix: "glyvex",
};

function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="flex items-start gap-2 text-sm cursor-pointer">
      <input type="checkbox" className="mt-0.5 accent-glyvex-accent" checked={checked}
        onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="text-glyvex-text">{label}</span>
        {hint && <span className="block text-xs text-glyvex-muted">{hint}</span>}
      </span>
    </label>
  );
}

/**
 * Sección "Exportar métricas" de Config. Controlado como ChatSettings: el
 * guardado lo hace el botón de Config. Lo único propio es "Probar conexión",
 * que usa los valores del formulario todavía sin guardar.
 */
export default function MetricsExportSettings({ exportsConfig, port, onChange }) {
  const { t } = useTranslation();
  const exp = exportsConfig || {};
  const influx = { ...INFLUX_DEFAULTS, ...(exp.influx || {}) };
  const [copied, setCopied] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const set = (patch) => onChange({ ...exp, ...patch });
  const setInflux = (patch) => {
    setTestResult(null);
    onChange({ ...exp, influx: { ...influx, ...patch } });
  };

  const promUrl = `http://127.0.0.1:${port || 7981}/api/metrics/prometheus`;
  const telegraf = [
    "[[inputs.prometheus]]",
    `  urls = ["${promUrl}"]`,
    exp.prometheus_token ? '  bearer_token_string = "<el token de arriba>"' : null,
  ].filter(Boolean).join("\n");

  async function copySnippet() {
    try {
      await navigator.clipboard.writeText(telegraf);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* portapapeles no disponible */ }
  }

  async function testInflux() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch("/api/metrics/export/influx/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(influx),
      });
      setTestResult(res.ok ? await res.json() : { ok: false, detail: `HTTP ${res.status}` });
    } catch {
      setTestResult({ ok: false, detail: t("config.export.testNoBackend") });
    } finally {
      setTesting(false);
    }
  }

  return (
    <Section title={t("config.export.section")}>
      <p className="text-xs text-glyvex-muted">
        {t("config.export.intro")}
      </p>

      {/* Prometheus */}
      <div className="rounded-md border border-glyvex-border-soft p-3 space-y-3">
        <Toggle
          checked={Boolean(exp.prometheus_enabled)}
          onChange={(v) => set({ prometheus_enabled: v })}
          label="Prometheus"
          hint={t("config.export.promHint")}
        />
        {exp.prometheus_enabled && (
          <>
            <Field label={t("config.export.token")} hint={t("config.export.tokenHint")}>
              <input type="password" autoComplete="off" className={formInputClasses}
                value={exp.prometheus_token || ""} onChange={(e) => set({ prometheus_token: e.target.value })} />
            </Field>
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm text-glyvex-muted">{t("config.export.telegrafExample")}</span>
                <button type="button" onClick={copySnippet}
                  className="inline-flex items-center gap-1 text-xs text-glyvex-muted hover:text-glyvex-text">
                  {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? t("config.export.copied") : t("config.export.copy")}
                </button>
              </div>
              <pre className="text-xs font-mono bg-glyvex-surface-code border border-glyvex-border-soft rounded-md p-2 overflow-x-auto">{telegraf}</pre>
            </div>
          </>
        )}
      </div>

      {/* InfluxDB */}
      <div className="rounded-md border border-glyvex-border-soft p-3 space-y-3">
        <Toggle
          checked={influx.enabled}
          onChange={(v) => setInflux({ enabled: v })}
          label="InfluxDB"
          hint={t("config.export.influxHint")}
        />
        {influx.enabled && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label={t("config.export.version")}>
                <select className={formInputClasses} value={influx.version}
                  onChange={(e) => setInflux({ version: e.target.value })}>
                  <option value="v2">InfluxDB 2.x / 3</option>
                  <option value="v1">InfluxDB 1.x / VictoriaMetrics / QuestDB</option>
                </select>
              </Field>
              <Field label="URL">
                <input className={formInputClasses} value={influx.url} placeholder="http://127.0.0.1:8086"
                  onChange={(e) => setInflux({ url: e.target.value })} />
              </Field>

              {influx.version === "v2" ? (
                <>
                  <Field label={t("config.export.org")}>
                    <input className={formInputClasses} value={influx.org} onChange={(e) => setInflux({ org: e.target.value })} />
                  </Field>
                  <Field label="Bucket">
                    <input className={formInputClasses} value={influx.bucket} onChange={(e) => setInflux({ bucket: e.target.value })} />
                  </Field>
                  <Field label="Token" hint={t("config.export.tokenEnvHint")}>
                    <input type="password" autoComplete="off" className={formInputClasses} value={influx.token}
                      onChange={(e) => setInflux({ token: e.target.value })} />
                  </Field>
                </>
              ) : (
                <>
                  <Field label={t("config.export.database")}>
                    <input className={formInputClasses} value={influx.database} onChange={(e) => setInflux({ database: e.target.value })} />
                  </Field>
                  <Field label={t("config.export.username")}>
                    <input className={formInputClasses} value={influx.username} onChange={(e) => setInflux({ username: e.target.value })} />
                  </Field>
                  <Field label={t("config.export.password")}>
                    <input type="password" autoComplete="off" className={formInputClasses} value={influx.password}
                      onChange={(e) => setInflux({ password: e.target.value })} />
                  </Field>
                </>
              )}

              <Field label={t("config.export.interval")}>
                <input type="number" min={1} className={formInputClasses} value={influx.interval_s}
                  onChange={(e) => setInflux({ interval_s: Math.max(1, Number(e.target.value) || 10) })} />
              </Field>
              <Field label={t("config.export.prefix")} hint={t("config.export.prefixHint")}>
                <input className={formInputClasses} value={influx.measurement_prefix}
                  onChange={(e) => setInflux({ measurement_prefix: e.target.value })} />
              </Field>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button type="button" onClick={testInflux} disabled={testing}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-text hover:bg-glyvex-veil-disabled disabled:opacity-50">
                {testing && <Loader2 size={14} className="animate-spin" />}
                {t("config.export.test")}
              </button>
              {testResult && (
                <span className={`text-sm ${testResult.ok ? "text-emerald-400" : "text-red-400"}`}>
                  {testResult.ok ? t("config.export.testOk") : t("config.export.testFail")}
                  {testResult.status_code ? ` (HTTP ${testResult.status_code})` : ""}
                  {!testResult.ok && testResult.detail ? `: ${testResult.detail}` : ""}
                </span>
              )}
            </div>
            <p className="text-xs text-glyvex-muted">
              {t("config.export.influxNote")}
            </p>
          </>
        )}
      </div>
    </Section>
  );
}

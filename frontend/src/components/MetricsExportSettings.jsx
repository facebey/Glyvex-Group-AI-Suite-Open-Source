import { useState } from "react";
import { Check, Copy, Loader2 } from "lucide-react";
import Section from "./ui/Section.jsx";
import Field from "./ui/Field.jsx";
import { formInputClasses } from "../lib/styles.js";

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
      setTestResult({ ok: false, detail: "no se pudo contactar al backend" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <Section title="Exportar métricas">
      <p className="text-xs text-glyvex-muted">
        Se exportan todas las métricas, también las ocultas en pantalla. Los cambios se aplican al guardar.
      </p>

      {/* Prometheus */}
      <div className="rounded-md border border-white/10 p-3 space-y-3">
        <Toggle
          checked={Boolean(exp.prometheus_enabled)}
          onChange={(v) => set({ prometheus_enabled: v })}
          label="Prometheus"
          hint="Expone GET /api/metrics/prometheus para Prometheus, Telegraf o VictoriaMetrics."
        />
        {exp.prometheus_enabled && (
          <>
            <Field label="Token (opcional)" hint="Si tiene valor, el endpoint exige Authorization: Bearer <token>. Útil si el backend queda expuesto en la red.">
              <input type="password" autoComplete="off" className={formInputClasses}
                value={exp.prometheus_token || ""} onChange={(e) => set({ prometheus_token: e.target.value })} />
            </Field>
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm text-glyvex-muted">Ejemplo para Telegraf</span>
                <button type="button" onClick={copySnippet}
                  className="inline-flex items-center gap-1 text-xs text-glyvex-muted hover:text-glyvex-text">
                  {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copiado" : "Copiar"}
                </button>
              </div>
              <pre className="text-xs font-mono bg-black/40 border border-white/10 rounded-md p-2 overflow-x-auto">{telegraf}</pre>
            </div>
          </>
        )}
      </div>

      {/* InfluxDB */}
      <div className="rounded-md border border-white/10 p-3 space-y-3">
        <Toggle
          checked={influx.enabled}
          onChange={(v) => setInflux({ enabled: v })}
          label="InfluxDB"
          hint="Envía ventanas de 5 s (promedio, mínimo y máximo), las mismas que guarda el histórico."
        />
        {influx.enabled && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Versión">
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
                  <Field label="Organización">
                    <input className={formInputClasses} value={influx.org} onChange={(e) => setInflux({ org: e.target.value })} />
                  </Field>
                  <Field label="Bucket">
                    <input className={formInputClasses} value={influx.bucket} onChange={(e) => setInflux({ bucket: e.target.value })} />
                  </Field>
                  <Field label="Token" hint="La variable de entorno GLYVEX_INFLUX_TOKEN tiene prioridad; dejarlo acá lo guarda en config.json.">
                    <input type="password" autoComplete="off" className={formInputClasses} value={influx.token}
                      onChange={(e) => setInflux({ token: e.target.value })} />
                  </Field>
                </>
              ) : (
                <>
                  <Field label="Base de datos">
                    <input className={formInputClasses} value={influx.database} onChange={(e) => setInflux({ database: e.target.value })} />
                  </Field>
                  <Field label="Usuario (opcional)">
                    <input className={formInputClasses} value={influx.username} onChange={(e) => setInflux({ username: e.target.value })} />
                  </Field>
                  <Field label="Contraseña (opcional)">
                    <input type="password" autoComplete="off" className={formInputClasses} value={influx.password}
                      onChange={(e) => setInflux({ password: e.target.value })} />
                  </Field>
                </>
              )}

              <Field label="Intervalo de envío (s)">
                <input type="number" min={1} className={formInputClasses} value={influx.interval_s}
                  onChange={(e) => setInflux({ interval_s: Math.max(1, Number(e.target.value) || 10) })} />
              </Field>
              <Field label="Prefijo de measurement" hint="Se escribe en <prefijo>_hw y <prefijo>_llm.">
                <input className={formInputClasses} value={influx.measurement_prefix}
                  onChange={(e) => setInflux({ measurement_prefix: e.target.value })} />
              </Field>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button type="button" onClick={testInflux} disabled={testing}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-text hover:bg-black/30 disabled:opacity-50">
                {testing && <Loader2 size={14} className="animate-spin" />}
                Probar conexión
              </button>
              {testResult && (
                <span className={`text-sm ${testResult.ok ? "text-emerald-400" : "text-red-400"}`}>
                  {testResult.ok ? "Escritura aceptada" : "Falló"}
                  {testResult.status_code ? ` (HTTP ${testResult.status_code})` : ""}
                  {!testResult.ok && testResult.detail ? `: ${testResult.detail}` : ""}
                </span>
              )}
            </div>
            <p className="text-xs text-glyvex-muted">
              Si activás InfluxDB con el histórico apagado, reiniciá el backend para que empiece a medir.
            </p>
          </>
        )}
      </div>
    </Section>
  );
}

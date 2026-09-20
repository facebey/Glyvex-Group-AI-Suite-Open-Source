import { useRef, useState } from "react";
import { Download, Trash2, Upload } from "lucide-react";
import CollapsibleSection from "./ui/CollapsibleSection.jsx";
import Slider from "./ui/Slider.jsx";
import { inputClasses } from "../lib/styles.js";

/** Switch chico y sin dependencias extra, mismo patrón que el del Launcher. */
function MiniToggle({ checked, onChange, disabled = false }) {
  function handleClick() {
    if (disabled) return;
    onChange(!checked);
  }
  return (
    <span
      role="switch"
      aria-checked={checked}
      tabIndex={disabled ? -1 : 0}
      onClick={handleClick}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), handleClick())}
      className={
        "inline-flex items-center h-5 w-9 shrink-0 rounded-full border transition-colors cursor-pointer " +
        (disabled ? "opacity-40 cursor-not-allowed " : "") +
        (checked ? "bg-glyvex-accent border-glyvex-accent" : "bg-black/30 border-white/10")
      }
    >
      <span
        className={
          "h-3.5 w-3.5 rounded-full bg-white transition-transform " +
          (checked ? "translate-x-[19px]" : "translate-x-[3px]")
        }
      />
    </span>
  );
}

/**
 * Panel izquierdo del chat: endpoint, modelo, system prompt, parámetros de
 * muestreo y acciones de la conversación.
 *
 * No tiene estado propio más allá de qué secciones están abiertas y el
 * borrador del endpoint manual — todo lo demás sube por callbacks.
 */
export default function ChatSidebar({
  endpoints,
  endpointUrl,
  onEndpointChange,
  endpointStatus,
  models,
  selectedModel,
  onModelChange,
  capabilities,
  apiKey,
  onApiKeyChange,
  systemPrompt,
  onSystemPromptChange,
  appendFileConvention,
  onAppendFileConventionChange,
  params,
  onParamChange,
  onClear,
  onExportJson,
  onExportMarkdown,
  onImport,
}) {
  const [systemPromptOpen, setSystemPromptOpen] = useState(false);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [manualEndpoint, setManualEndpoint] = useState("");
  const importInputRef = useRef(null);

  const contextSize = capabilities?.context_size ?? 0;

  const pingColor =
    endpointStatus === "ok"
      ? "bg-emerald-400"
      : endpointStatus === "error"
        ? "bg-red-400"
        : "bg-glyvex-muted/50";

  function useManualEndpoint() {
    const url = manualEndpoint.trim();
    if (!url) return;
    onEndpointChange(url);
    setManualEndpoint("");
  }

  return (
    <aside className="w-[300px] shrink-0 overflow-y-auto space-y-3 pr-2">
      <div className="bg-glyvex-card rounded-lg border border-white/10 p-4 space-y-3">
        <div>
          <span className="block text-sm text-glyvex-muted mb-1">Endpoint</span>
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full shrink-0 ${pingColor}`} />
            <select
              className={inputClasses}
              value={endpointUrl}
              onChange={(e) => onEndpointChange(e.target.value)}
            >
              {endpoints.map((e) => (
                <option key={e.url} value={e.url}>
                  {e.name}
                </option>
              ))}
              {!endpoints.some((e) => e.url === endpointUrl) && (
                <option value={endpointUrl}>{endpointUrl}</option>
              )}
            </select>
          </div>
          <div className="flex gap-2 mt-2">
            <input
              className={inputClasses}
              value={manualEndpoint}
              onChange={(e) => setManualEndpoint(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && useManualEndpoint()}
              placeholder="http://host:puerto"
            />
            <button
              type="button"
              onClick={useManualEndpoint}
              className="px-2 py-1.5 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30 shrink-0"
            >
              Usar
            </button>
          </div>
        </div>

        <label className="block">
          <span className="block text-sm text-glyvex-muted mb-1">Modelo</span>
          <select
            className={inputClasses}
            value={selectedModel}
            onChange={(e) => onModelChange(e.target.value)}
          >
            {models.length === 0 && <option value="">(sin detectar)</option>}
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          {capabilities && (
            <span className="block text-xs text-glyvex-muted mt-1 tabular-nums">
              {contextSize > 0
                ? `Contexto: ${contextSize.toLocaleString()} tokens`
                : "Contexto: sin detectar"}
              {capabilities.vision ? " · visión" : ""}
              {capabilities.tools ? " · tools" : ""}
              {capabilities.parallel_tool_calls ? " · paralelo" : ""}
              {capabilities.reasoning_effort ? " · effort" : ""}
              {capabilities.source === "heuristic" && (
                <span title="El endpoint no expone /props: esto se dedujo del nombre del modelo.">
                  {" "}
                  · estimado
                </span>
              )}
            </span>
          )}
        </label>

        <label className="block">
          <span className="block text-sm text-glyvex-muted mb-1">API key (opcional)</span>
          <input
            className={inputClasses}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            type="password"
          />
        </label>
      </div>

      <CollapsibleSection
        title="System prompt"
        open={systemPromptOpen}
        onToggle={() => setSystemPromptOpen((v) => !v)}
      >
        <textarea
          className={inputClasses + " min-h-[80px] resize-y"}
          value={systemPrompt}
          onChange={(e) => onSystemPromptChange(e.target.value)}
          placeholder="Instrucciones de sistema…"
        />
        <label className="flex items-start gap-2 mt-2.5 cursor-pointer select-none">
          <MiniToggle
            checked={Boolean(appendFileConvention)}
            onChange={onAppendFileConventionChange}
          />
          <span className="text-xs text-glyvex-muted leading-snug">
            Nombrar archivos generados
            <span className="block text-[11px] opacity-70">
              Se suma a lo de arriba: le pide al modelo poner
              {" "}<code className="text-glyvex-text">lenguaje:nombre.ext</code>{" "}
              en la cerca de código cuando genera un archivo completo, para
              poder descargarlo con su nombre real.
            </span>
          </span>
        </label>
      </CollapsibleSection>

      <CollapsibleSection
        title="Parámetros"
        open={paramsOpen}
        onToggle={() => setParamsOpen((v) => !v)}
      >
        <Slider label="Temperature" value={params.temperature} min={0} max={2} step={0.05} onChange={(v) => onParamChange({ temperature: v })} formatValue={(v) => v.toFixed(2)} />
        <Slider label="Top P" value={params.top_p} min={0} max={1} step={0.01} onChange={(v) => onParamChange({ top_p: v })} formatValue={(v) => v.toFixed(2)} />
        <Slider label="Top K" value={params.top_k} min={0} max={100} step={1} onChange={(v) => onParamChange({ top_k: v })} />
        <Slider label="Min P" value={params.min_p} min={0} max={1} step={0.01} onChange={(v) => onParamChange({ min_p: v })} formatValue={(v) => v.toFixed(2)} />
        <Slider label="Repeat penalty" value={params.repeat_penalty} min={1} max={1.5} step={0.01} onChange={(v) => onParamChange({ repeat_penalty: v })} formatValue={(v) => v.toFixed(2)} />
        <label className="block">
          <span className="block text-xs text-glyvex-muted mb-1">Max tokens</span>
          <input
            type="number"
            className={inputClasses}
            value={params.max_tokens}
            onChange={(e) => onParamChange({ max_tokens: Number(e.target.value) })}
          />
        </label>
        <label className="block">
          <span className="block text-xs text-glyvex-muted mb-1">Seed (-1 = aleatorio)</span>
          <input
            type="number"
            className={inputClasses}
            value={params.seed}
            onChange={(e) => onParamChange({ seed: Number(e.target.value) })}
          />
        </label>
      </CollapsibleSection>

      <div className="border-t border-white/10 pt-3 space-y-2">
        <button
          type="button"
          onClick={onClear}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
        >
          <Trash2 size={14} />
          Limpiar conversación
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onExportJson}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-2 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
          >
            <Download size={13} /> JSON
          </button>
          <button
            type="button"
            onClick={onExportMarkdown}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-2 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
          >
            <Download size={13} /> Markdown
          </button>
        </div>
        <button
          type="button"
          onClick={() => importInputRef.current?.click()}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
        >
          <Upload size={14} />
          Importar
        </button>
        <input
          ref={importInputRef}
          type="file"
          accept="application/json"
          className="hidden"
          onChange={onImport}
        />
      </div>
    </aside>
  );
}

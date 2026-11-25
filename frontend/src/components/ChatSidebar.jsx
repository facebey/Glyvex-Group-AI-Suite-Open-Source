import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bookmark, Download, Trash2, Upload } from "lucide-react";
import CollapsibleSection from "./ui/CollapsibleSection.jsx";
import Slider from "./ui/Slider.jsx";
import { inputClasses } from "../lib/styles.js";
import { PRESET_AUTO, PRESET_CUSTOM, presetOriginKeys, resolvePresetEntry } from "../lib/chatPresets.js";

// llama-server reporta como id del modelo la ruta completa del .gguf
// (C:\LLM\gguf\Qwen…\modelo.gguf), que se corta en el selector. Se muestra
// solo el nombre del archivo; la ruta completa queda en el tooltip.
function modelLabel(id) {
  if (!id) return id;
  const base = String(id).split(/[\\/]/).pop();
  return base || id;
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
  recentEndpoints = [],
  models,
  selectedModel,
  onModelChange,
  capabilities,
  apiKey,
  onApiKeyChange,
  systemPrompt,
  onSystemPromptChange,
  params,
  onParamChange,
  presets = [],
  presetChoice,
  onPresetChange,
  onPresetSave,
  onPresetDelete,
  onClear,
  onExportJson,
  onExportMarkdown,
  onImport,
}) {
  const { t } = useTranslation();
  const [systemPromptOpen, setSystemPromptOpen] = useState(false);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [manualEndpoint, setManualEndpoint] = useState("");
  const importInputRef = useRef(null);

  const contextSize = capabilities?.context_size ?? 0;

  const builtinPresets = presets.filter((p) => p.builtin);
  const userPresets = presets.filter((p) => !p.builtin);
  // Solo los presets del usuario pueden borrarse; los built-in no viven en la DB.
  const selectedUserPreset = userPresets.find((p) => p.id === presetChoice) || null;

  // PRESET-ORIGIN: la entrada que Chat.jsx está aplicando (auto incluye el
  // recomendado; custom o auto sin recomendado = ninguna). Los sliders cuyo
  // valor viene de esa entrada se marcan; lo no marcado es default de la app.
  const originKeys = presetOriginKeys(resolvePresetEntry(presets, presetChoice));
  const originTag = (key) => (originKeys.has(key) ? t("chatSidebar.originPreset") : undefined);

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
    <aside className="w-[300px] 2xl:w-[360px] shrink-0 overflow-y-auto space-y-3 pr-2">
      <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-4 space-y-3">
        <div>
          <span className="block text-sm text-glyvex-muted mb-1">{t("chatSidebar.endpoint")}</span>
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
              {recentEndpoints
                .filter((url) => !endpoints.some((e) => e.url === url))
                .map((url) => (
                  <option key={url} value={url}>
                    {url}
                  </option>
                ))}
              {!endpoints.some((e) => e.url === endpointUrl) &&
                !recentEndpoints.includes(endpointUrl) && (
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
              placeholder={t("chatSidebar.endpointPlaceholder")}
            />
            <button
              type="button"
              onClick={useManualEndpoint}
              className="px-2 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled shrink-0"
            >
              {t("chatSidebar.use")}
            </button>
          </div>
        </div>

        <label className="block">
          <span className="block text-sm text-glyvex-muted mb-1">{t("chatSidebar.model")}</span>
          <select
            className={inputClasses}
            value={selectedModel}
            onChange={(e) => onModelChange(e.target.value)}
            title={selectedModel || undefined}
          >
            {models.length === 0 && <option value="">{t("chatSidebar.noModel")}</option>}
            {models.map((m) => (
              <option key={m} value={m} title={m}>
                {modelLabel(m)}
              </option>
            ))}
          </select>
          {capabilities && (
            <span className="block text-xs text-glyvex-muted mt-1 tabular-nums">
              {contextSize > 0
                ? t("chatSidebar.context", { count: contextSize.toLocaleString() })
                : t("chatSidebar.contextUnknown")}
              {capabilities.vision ? t("chatSidebar.vision") : ""}
              {capabilities.tools ? t("chatSidebar.tools") : ""}
              {capabilities.parallel_tool_calls ? t("chatSidebar.parallel") : ""}
              {capabilities.reasoning_effort ? t("chatSidebar.effort") : ""}
              {capabilities.source === "heuristic" && (
                <span title={t("chatSidebar.estimatedTitle")}>
                  {" "}
                  {t("chatSidebar.estimated")}
                </span>
              )}
            </span>
          )}
        </label>

        <label className="block">
          <span className="block text-sm text-glyvex-muted mb-1">{t("chatSidebar.apiKey")}</span>
          <input
            className={inputClasses}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            type="password"
          />
        </label>
      </div>

      <CollapsibleSection
        title={t("chatSidebar.systemPrompt")}
        open={systemPromptOpen}
        onToggle={() => setSystemPromptOpen((v) => !v)}
      >
        <textarea
          className={inputClasses + " min-h-[80px] resize-y"}
          value={systemPrompt}
          onChange={(e) => onSystemPromptChange(e.target.value)}
          placeholder={t("chatSidebar.systemPromptPlaceholder")}
        />
      </CollapsibleSection>

      <CollapsibleSection
        title={t("chatSidebar.params")}
        open={paramsOpen}
        onToggle={() => setParamsOpen((v) => !v)}
      >
        <div>
          <span className="block text-sm text-glyvex-muted mb-1">{t("chatSidebar.preset")}</span>
          <div className="flex gap-2">
            <select
              className={inputClasses}
              value={presetChoice}
              onChange={(e) => onPresetChange(e.target.value)}
            >
              <option value={PRESET_AUTO}>{t("chatPresets.auto")}</option>
              {builtinPresets.length > 0 && (
                <optgroup label={t("chatPresets.groupBuiltin")}>
                  {builtinPresets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.labelKey ? t(p.labelKey) : p.name}
                    </option>
                  ))}
                </optgroup>
              )}
              {userPresets.length > 0 && (
                <optgroup label={t("chatPresets.groupUser")}>
                  {userPresets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </optgroup>
              )}
              <option value={PRESET_CUSTOM}>{t("chatPresets.custom")}</option>
            </select>
            <button
              type="button"
              onClick={onPresetSave}
              title={t("chatPresets.save")}
              className="px-2 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled shrink-0"
            >
              <Bookmark size={14} />
            </button>
            {selectedUserPreset && (
              <button
                type="button"
                onClick={onPresetDelete}
                title={t("chatPresets.delete")}
                className="px-2 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-red-400 hover:bg-glyvex-veil-disabled shrink-0"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        </div>
        <Slider label="Temperature" value={params.temperature} min={0} max={2} step={0.05} onChange={(v) => onParamChange({ temperature: v })} formatValue={(v) => v.toFixed(2)} originTag={originTag("temperature")} />
        <Slider label="Top P" value={params.top_p} min={0} max={1} step={0.01} onChange={(v) => onParamChange({ top_p: v })} formatValue={(v) => v.toFixed(2)} originTag={originTag("top_p")} />
        <Slider label="Top K" value={params.top_k} min={0} max={100} step={1} onChange={(v) => onParamChange({ top_k: v })} originTag={originTag("top_k")} />
        <Slider label="Min P" value={params.min_p} min={0} max={1} step={0.01} onChange={(v) => onParamChange({ min_p: v })} formatValue={(v) => v.toFixed(2)} originTag={originTag("min_p")} />
        <Slider label="Repeat penalty" value={params.repeat_penalty} min={1} max={1.5} step={0.01} onChange={(v) => onParamChange({ repeat_penalty: v })} formatValue={(v) => v.toFixed(2)} originTag={originTag("repeat_penalty")} />
        <Slider label="Presence penalty" value={params.presence_penalty} min={-2} max={2} step={0.1} onChange={(v) => onParamChange({ presence_penalty: v })} formatValue={(v) => v.toFixed(2)} originTag={originTag("presence_penalty")} />
        {originKeys.size > 0 && (
          <p className="text-xs text-glyvex-muted">{t("chatSidebar.originHint")}</p>
        )}
      </CollapsibleSection>

      <div className="border-t border-glyvex-border-soft pt-3 space-y-2">
        <button
          type="button"
          onClick={onClear}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
        >
          <Trash2 size={14} />
          {t("chatSidebar.clearConversation")}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onExportJson}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-2 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
          >
            <Download size={13} /> JSON
          </button>
          <button
            type="button"
            onClick={onExportMarkdown}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-2 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
          >
            <Download size={13} /> Markdown
          </button>
        </div>
        <button
          type="button"
          onClick={() => importInputRef.current?.click()}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
        >
          <Upload size={14} />
          {t("chatSidebar.import")}
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

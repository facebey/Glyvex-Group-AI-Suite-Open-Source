import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Check, Download, Loader2, RefreshCw, Trash2 } from "lucide-react";
import Section from "./ui/Section.jsx";
import Field from "./ui/Field.jsx";
import { formInputClasses } from "../lib/styles.js";
import { consumeSSE } from "../lib/sse.js";

/**
 * Secciones de configuración del módulo de chat: búsqueda web, voz a texto y
 * adjuntos.
 *
 * Es un componente controlado: no guarda nada por su cuenta. Recibe el
 * `config` de Config.jsx y le sube los cambios, para que la página siga
 * teniendo una sola fuente de verdad y un solo botón de Guardar.
 *
 * Lo único propio son los semáforos de estado, que consultan
 * /api/chat/tools/status y /api/stt/status. Esos reflejan lo que hay
 * GUARDADO en el servidor, no lo que está editado en pantalla — de ahí el
 * botón "Comprobar" para volver a consultarlos después de guardar.
 *
 * Las API keys de Brave y Tavily no se editan acá a propósito: se leen de
 * variables de entorno para no terminar escritas en config.json, que es un
 * archivo que se sincroniza y a veces se sube a un repo.
 */

const WHISPER_MODELS = [
  { id: "tiny", size: "~75 MB" },
  { id: "base", size: "~145 MB", noteKey: "chatSettings.whisperDefault" },
  { id: "small", size: "~484 MB" },
  { id: "medium", size: "~1.5 GB" },
  { id: "large-v3-turbo", size: "~1.6 GB", noteKey: "chatSettings.whisperGpu" },
  { id: "distil-large-v3", size: "~1.5 GB" },
];

const FALLBACK_PROVIDERS = [{ id: "ddgs", label: "DuckDuckGo", requires: null, hint: null }];

// Nombres nativos: no se traducen. El backend corta la región para los
// motores que solo aceptan 2 letras ("es-AR" → "es"); el navegador la usa
// completa como BCP-47.
const STT_LANGUAGES = [
  { id: "es-AR", label: "Español (AR)" },
  { id: "es", label: "Español" },
  { id: "en-US", label: "Inglés (US)" },
  { id: "en", label: "Inglés" },
  { id: "pt-BR", label: "Portugués (BR)" },
  { id: "pt", label: "Portugués" },
  { id: "fr", label: "Francés" },
  { id: "de", label: "Alemán" },
  { id: "it", label: "Italiano" },
];

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function Toggle({ label, hint, checked, onChange }) {
  return (
    <label className="flex items-start gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={Boolean(checked)}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 accent-glyvex-accent"
      />
      <span>
        <span className="block text-sm text-glyvex-text">{label}</span>
        {hint && <span className="block text-xs text-glyvex-muted/70">{hint}</span>}
      </span>
    </label>
  );
}

/** Semáforo de un servicio: listo, no disponible, o consultando. */
function StatusLine({ loading, ready, reason, okLabel }) {
  const { t } = useTranslation();
  if (loading) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-glyvex-muted">
        <Loader2 size={13} className="animate-spin" /> {t("chatSettings.statusChecking")}
      </p>
    );
  }
  if (ready) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-emerald-400">
        <Check size={13} /> {okLabel}
      </p>
    );
  }
  return (
    <p className="flex items-start gap-1.5 text-sm text-amber-400">
      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      <span>{reason || t("chatSettings.statusUnavailable")}</span>
    </p>
  );
}

/**
 * Input numérico que solo sube a `config` valores válidos: entero dentro de
 * [min, max]. Con el campo vacío o intermedio (p. ej. "1." o nada) no se
 * parchea nada, y al salir del campo sin un valor válido vuelve a mostrar
 * el valor guardado. Así un campo borrado por error nunca termina como
 * 0/NaN/null en config.json (un null en el backend rompe los int() de
 * /tools/status, _limits(), etc.).
 */
function NumberField({ label, hint, value, min, max, step, onCommit }) {
  const [text, setText] = useState(String(value));

  useEffect(() => {
    setText(String(value));
  }, [value]);

  function handleChange(e) {
    const raw = e.target.value;
    setText(raw);
    const n = Number(raw);
    if (raw.trim() !== "" && Number.isInteger(n) && n >= min && n <= max) {
      onCommit(n);
    }
  }

  function handleBlur() {
    const n = Number(text);
    if (text.trim() === "" || !Number.isInteger(n) || n < min || n > max) {
      setText(String(value));
    }
  }

  return (
    <Field label={label} hint={hint}>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        className={formInputClasses}
        value={text}
        onChange={handleChange}
        onBlur={handleBlur}
      />
    </Field>
  );
}

/**
 * Voz neuronal Piper (TTS-1): selector de voz del catálogo, botón de descarga
 * con progreso por SSE y estado del motor. Solo se muestra cuando el engine
 * de TTS es auto o piper.
 */
function PiperVoice({ t, piper, voiceName, onVoice, downloading, downloadError, onDownload }) {
  const engine = piper.engine || {};
  const voices = piper.voices || [];
  const selected =
    voices.find((v) => v.name === (voiceName || piper.default_voice)) || voices[0] || null;

  if (!engine.available) {
    return (
      <p className="flex items-start gap-1.5 text-sm text-amber-400">
        <AlertTriangle size={13} className="mt-0.5 shrink-0" />
        <span>{t("chatSettings.ttsPiperEngineMissing", { detail: engine.detail || "" })}</span>
      </p>
    );
  }

  return (
    <>
      <Field label={t("chatSettings.ttsPiperVoice")} hint={t("chatSettings.ttsPiperVoiceHint")}>
        <select
          className={formInputClasses}
          value={selected?.name || ""}
          onChange={(e) => onVoice(e.target.value)}
        >
          {voices.map((v) => (
            <option key={v.name} value={v.name}>
              {v.label}
              {v.state === "ready" ? ` — ${Math.round(v.size_mb)} MB` : ""}{" "}
              ({t(`chatSettings.state${cap(v.state)}`)})
            </option>
          ))}
        </select>
      </Field>

      {selected &&
        selected.state !== "ready" &&
        selected.state !== "downloading" &&
        downloading?.kind !== "piperVoice" && (
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => onDownload(selected.name)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border border-glyvex-border-soft text-glyvex-text hover:bg-glyvex-veil-disabled"
            >
              <Download size={14} />
              {t("chatSettings.ttsPiperDownload", {
                mb: Math.round(selected.download_mb || 0),
              })}
            </button>
            {selected.error && <span className="text-xs text-amber-400">{selected.error}</span>}
          </div>
        )}

      {downloading?.kind === "piperVoice" && (
        <div>
          <p className="text-sm text-glyvex-muted">
            {t("chatSettings.ttsPiperDownloading", {
              voice: downloading.name,
              pct: Math.round(downloading.pct),
            })}
            {downloading.detail ? ` — ${downloading.detail}` : ""}
          </p>
          <div className="h-1.5 rounded-full bg-glyvex-surface-code overflow-hidden mt-1.5">
            <div
              className="h-full bg-glyvex-accent transition-all"
              style={{ width: `${downloading.pct}%` }}
            />
          </div>
        </div>
      )}

      {downloadError?.kind === "piperVoice" && !downloading && (
        <p className="flex items-start gap-1.5 text-sm text-red-400">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          {downloadError.message}
        </p>
      )}
    </>
  );
}

/**
 * Voz neuronal Kokoro (TTS-2): descarga del modelo compartido (ONNX + bin con
 * las 54 voces) con progreso por SSE, selector de voz del catálogo y borrado
 * del modelo. Solo se muestra cuando el engine de TTS es auto o kokoro.
 */
function KokoroVoice({
  t,
  kokoro,
  voiceName,
  onVoice,
  downloading,
  downloadError,
  onDownload,
  onDelete,
}) {
  const engine = kokoro.engine || {};
  const voices = kokoro.voices || [];
  const selected = voices.find((v) => v.name === voiceName) || voices[0] || null;
  const modelState = kokoro.model_state || "missing";

  if (!engine.available) {
    return (
      <p className="flex items-start gap-1.5 text-sm text-amber-400">
        <AlertTriangle size={13} className="mt-0.5 shrink-0" />
        <span>{t("chatSettings.ttsKokoroEngineMissing", { detail: engine.detail || "" })}</span>
      </p>
    );
  }

  return (
    <>
      <Field label={t("chatSettings.ttsKokoroVoice")} hint={t("chatSettings.ttsKokoroVoiceHint")}>
        <select
          className={formInputClasses}
          value={selected?.name || ""}
          onChange={(e) => onVoice(e.target.value)}
        >
          {voices.map((v) => (
            <option key={v.name} value={v.name}>
              {v.label}
            </option>
          ))}
        </select>
      </Field>

      {modelState !== "ready" &&
        modelState !== "downloading" &&
        downloading?.kind !== "kokoroModel" && (
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onDownload}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border border-glyvex-border-soft text-glyvex-text hover:bg-glyvex-veil-disabled"
            >
              <Download size={14} />
              {t("chatSettings.ttsKokoroDownload", {
                mb: Math.round(kokoro.download_mb || 0),
              })}
            </button>
            {modelState === "error" && (
              <span className="text-xs text-amber-400">
                {t("chatSettings.ttsKokoroModelError")}
              </span>
            )}
          </div>
        )}

      {modelState === "ready" && downloading?.kind !== "kokoroModel" && (
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onDelete}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border border-glyvex-border-soft text-amber-400 hover:bg-glyvex-veil-disabled"
          >
            <Trash2 size={14} />
            {t("chatSettings.ttsKokoroDelete")}
          </button>
        </div>
      )}

      {downloading?.kind === "kokoroModel" && (
        <div>
          <p className="text-sm text-glyvex-muted">
            {t("chatSettings.ttsKokoroDownloading", { pct: Math.round(downloading.pct) })}
            {downloading.detail ? ` — ${downloading.detail}` : ""}
          </p>
          <div className="h-1.5 rounded-full bg-glyvex-surface-code overflow-hidden mt-1.5">
            <div
              className="h-full bg-glyvex-accent transition-all"
              style={{ width: `${downloading.pct}%` }}
            />
          </div>
        </div>
      )}

      {downloadError?.kind === "kokoroModel" && !downloading && (
        <p className="flex items-start gap-1.5 text-sm text-red-400">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          {downloadError.message}
        </p>
      )}
    </>
  );
}

function CheckButton({ onClick, checking }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={checking}
      title={t("chatSettings.checkTitle")}
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled disabled:opacity-50"
    >
      <RefreshCw size={12} className={checking ? "animate-spin" : ""} />
      {t("chatSettings.check")}
    </button>
  );
}

export default function ChatSettings({ config, onChange }) {
  const { t } = useTranslation();
  const [toolsStatus, setToolsStatus] = useState(null);
  const [sttStatus, setSttStatus] = useState(null);
  const [sttModels, setSttModels] = useState(null);
  const [ttsStatus, setTtsStatus] = useState(null);
  // STT-3: el spinner es por sección ("search" | "stt" | "tts"); sin sección
  // es una reconsulta silenciosa (arranque y polling). Antes un solo flag
  // hacía girar a los 4 semáforos a la vez.
  const [checking, setChecking] = useState(null);
  const [downloading, setDownloading] = useState(null); // { kind, name, pct, detail }
  const [downloadError, setDownloadError] = useState(null);
  const downloadAbortRef = useRef(null);
  useEffect(() => () => downloadAbortRef.current?.abort(), []);

  const loadStatus = useCallback(async (section) => {
    if (section) setChecking(section);
    const [tools, stt, models, tts] = await Promise.all([
      fetch("/api/chat/tools/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/stt/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/stt/runtime/models").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/tts/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    setToolsStatus(tools);
    setSttStatus(stt);
    setSttModels(models);
    setTtsStatus(tts);
    setChecking(null);
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // Descargas que arrancaron desde otra vista (p. ej. /provision): el
  // backend sigue trabajando aunque la UI no lea el stream, así se
  // reconsulta periódicamente (silencioso: sin spinner) hasta que el
  // modelo o el runtime salgan de "downloading".
  const modelBusy = (sttModels?.models || []).some((m) => m.state === "downloading");
  const runtimeBusy = sttStatus?.whispercpp?.runtime_state === "downloading";
  const piperVoiceBusy = (ttsStatus?.piper?.voices || []).some(
    (v) => v.state === "downloading"
  );
  const kokoroModelBusy = ttsStatus?.kokoro?.model_state === "downloading";
  useEffect(() => {
    if (!modelBusy && !runtimeBusy && !piperVoiceBusy && !kokoroModelBusy) return undefined;
    const id = setInterval(() => loadStatus(), 2500);
    return () => clearInterval(id);
  }, [modelBusy, runtimeBusy, piperVoiceBusy, kokoroModelBusy, loadStatus]);

  // Descarga por SSE (modelo o runtime): progreso en vivo, y al terminar se
  // reconsultan los estados para refrescar los selectores. El fetch NO lleva
  // signal a propósito: si la UI va, el backend termina la descarga igual.
  const startDownload = useCallback(
    async (kind, url, name) => {
      setDownloadError(null);
      setDownloading({ kind, name, pct: 0, detail: "" });
      const controller = new AbortController();
      downloadAbortRef.current = controller;
      try {
        const res = await fetch(url, { method: "POST" });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `El servidor respondió ${res.status}`);
        }
        await consumeSSE(res, controller.signal, (event) => {
          if (event.type === "progress") {
            setDownloading({ kind, name, pct: event.pct, detail: event.detail });
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
        });
        await loadStatus();
      } catch (err) {
        if (err.name !== "AbortError") {
          setDownloadError({ kind, message: err.message || String(err) });
        }
      } finally {
        setDownloading(null);
      }
    },
    [loadStatus]
  );

  const tools = config.tools || {};
  const stt = config.stt || {};
  const tts = config.tts || {};
  const attachments = config.attachments || {};

  function patch(sectionName, values) {
    onChange((prev) => ({
      ...prev,
      [sectionName]: { ...(prev[sectionName] || {}), ...values },
    }));
  }

  const providers = toolsStatus?.providers?.length ? toolsStatus.providers : FALLBACK_PROVIDERS;
  const activeProvider = providers.find((p) => p.id === (tools.search_provider || "ddgs"));
  const whisper = sttStatus?.whisper || {};
  const whispercpp = sttStatus?.whispercpp || {};
  const packaged = Boolean(sttStatus?.packaged);
  const cppModels = sttModels?.models || [];
  const selectedModel = cppModels.find((m) => m.name === (stt.whisper_model || "base")) || null;

  return (
    <>
      <Section title={t("chatSettings.sectionSearch")}>
        <div className="flex items-start justify-between gap-4">
          <StatusLine
            loading={checking === "search"}
            ready={toolsStatus?.search?.ready}
            reason={toolsStatus?.search?.reason}
            okLabel={t("chatSettings.searchOk", {
              provider: toolsStatus?.search?.provider_label || t("chatSettings.providerDefault"),
            })}
          />
          <CheckButton onClick={() => loadStatus("search")} checking={checking === "search"} />
        </div>

        <Field label={t("chatSettings.provider")} hint={activeProvider?.hint}>
          <select
            className={formInputClasses}
            value={tools.search_provider || "ddgs"}
            onChange={(e) => patch("tools", { search_provider: e.target.value })}
          >
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.requires ? t("chatSettings.needs", { what: p.requires }) : ""}
              </option>
            ))}
          </select>
        </Field>

        {tools.search_provider === "searxng" && (
          <Field
            label={t("chatSettings.searxngUrl")}
            hint={t("chatSettings.searxngHint")}
          >
            <input
              className={formInputClasses}
              value={tools.searxng_url || ""}
              onChange={(e) => patch("tools", { searxng_url: e.target.value })}
              placeholder="http://127.0.0.1:8888"
            />
          </Field>
        )}

        {(tools.search_provider === "brave" || tools.search_provider === "tavily") && (
          <p className="text-sm text-glyvex-muted">
            {t("chatSettings.apiKeyNoteBefore")}
            <code className="px-1 rounded bg-glyvex-surface-code text-glyvex-text">
              {tools.search_provider === "brave" ? "BRAVE_API_KEY" : "TAVILY_API_KEY"}
            </code>
            {t("chatSettings.apiKeyNoteAfter")}
          </p>
        )}

        <div className="grid grid-cols-2 gap-4">
          <NumberField
            label={t("chatSettings.maxResults")}
            min={1}
            max={10}
            value={tools.max_results ?? 5}
            onCommit={(n) => patch("tools", { max_results: n })}
          />
          <NumberField
            label={t("chatSettings.fetchMaxChars")}
            min={1000}
            max={200000}
            step={1000}
            value={tools.fetch_max_chars ?? 8000}
            onCommit={(n) => patch("tools", { fetch_max_chars: n })}
          />
        </div>

        <NumberField
          label={t("chatSettings.maxRounds")}
          hint={t("chatSettings.maxRoundsHint")}
          min={1}
          max={20}
          value={tools.max_rounds ?? 5}
          onCommit={(n) => patch("tools", { max_rounds: n })}
        />

        <Toggle
          label={t("chatSettings.allowPrivate")}
          hint={t("chatSettings.allowPrivateHint")}
          checked={tools.allow_private_hosts}
          onChange={(v) => patch("tools", { allow_private_hosts: v })}
        />
      </Section>

      <Section title={t("chatSettings.sectionStt")}>
        <Field
          label={t("chatSettings.engine")}
          hint={
            stt.engine === "browser"
              ? t("chatSettings.engineHintBrowser")
              : stt.engine === "whispercpp"
                ? t("chatSettings.engineHintWhisperCpp")
                : stt.engine === "whisper"
                  ? t("chatSettings.engineHintWhisper")
                  : t("chatSettings.engineHintAuto")
          }
        >
          <select
            className={formInputClasses}
            value={stt.engine || "auto"}
            onChange={(e) => patch("stt", { engine: e.target.value })}
          >
            <option value="auto">{t("chatSettings.engineAuto")}</option>
            <option value="whispercpp">{t("chatSettings.engineWhisperCpp")}</option>
            <option value="browser">{t("chatSettings.engineBrowser")}</option>
            <option value="whisper" disabled={packaged}>{t("chatSettings.engineWhisper")}</option>
          </select>
        </Field>

        <Field label={t("chatSettings.sttLanguage")} hint={t("chatSettings.sttLanguageHint")}>
          <select
            className={formInputClasses}
            value={stt.language ?? "es-AR"}
            onChange={(e) => patch("stt", { language: e.target.value })}
          >
            <option value="">{t("chatSettings.sttLanguageAuto")}</option>
            {STT_LANGUAGES.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
            {stt.language && !STT_LANGUAGES.some((l) => l.id === stt.language) && (
              <option value={stt.language}>{stt.language}</option>
            )}
          </select>
        </Field>

        {stt.engine !== "browser" && (
          <Toggle
            label={t("chatSettings.sttTranslate")}
            hint={t("chatSettings.sttTranslateHint")}
            checked={stt.translate_english}
            onChange={(v) => patch("stt", { translate_english: v })}
          />
        )}

        {(stt.engine === "auto" || stt.engine === "whispercpp") && (
          <>
            <div className="flex items-start justify-between gap-4">
              <StatusLine
                loading={checking === "stt"}
                ready={whispercpp.installed && whispercpp.model_ready}
                reason={whispercpp.reason}
                okLabel={t("chatSettings.cppReady", { model: whispercpp.model })}
              />
              <CheckButton onClick={() => loadStatus("stt")} checking={checking === "stt"} />
            </div>

            {whispercpp.runtime_state && whispercpp.runtime_state !== "ready" &&
              whispercpp.runtime_state !== "downloading" &&
              whispercpp.runtime_state !== "unsupported" &&
              downloading?.kind !== "runtime" && (
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => startDownload("runtime", "/api/stt/runtime/download", "whisper.cpp runtime")}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border border-glyvex-border-soft text-glyvex-text hover:bg-glyvex-veil-disabled"
                  >
                    <Download size={14} />
                    {t("chatSettings.cppRuntimeDownload")}
                  </button>
                  {whispercpp.missing_files?.length > 0 && (
                    <span className="text-xs text-amber-400 break-all">
                      {t("chatSettings.cppMissingFiles")}: {whispercpp.missing_files.join(", ")}
                    </span>
                  )}
                </div>
              )}

            <Field label={t("chatSettings.model")} hint={t("chatSettings.cppModelHint")}>
              <select
                className={formInputClasses}
                value={stt.whisper_model || "base"}
                onChange={(e) => patch("stt", { whisper_model: e.target.value })}
              >
                {(cppModels.length
                  ? cppModels
                  : [
                      { name: "base", label: "base", size: 141 * 1024 * 1024, state: "missing" },
                      { name: "small", label: "small", size: 466 * 1024 * 1024, state: "missing" },
                      { name: "medium", label: "medium", size: 1422 * 1024 * 1024, state: "missing" },
                      { name: "large-v3", label: "large-v3", size: 3 * 1024 * 1024 * 1024, state: "missing" },
                    ]
                ).map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.label} — {Math.round(m.size / (1024 * 1024))} MB
                    {" "}({t(`chatSettings.state${cap(m.state)}`)})
                  </option>
                ))}
              </select>
            </Field>

            {selectedModel && selectedModel.state !== "ready" && !downloading && (
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => startDownload("model", `/api/stt/runtime/models/${selectedModel.name}/download`, selectedModel.name)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border border-glyvex-border-soft text-glyvex-text hover:bg-glyvex-veil-disabled"
                >
                  <Download size={14} />
                  {t("chatSettings.cppDownload", {
                    mb: Math.round(selectedModel.size / (1024 * 1024)),
                  })}
                </button>
                {selectedModel.error && (
                  <span className="text-xs text-amber-400">{selectedModel.error}</span>
                )}
              </div>
            )}

            {downloading && downloading.kind !== "piperVoice" && (
              <div>
                <p className="text-sm text-glyvex-muted">
                  {t("chatSettings.cppDownloading", {
                    model: downloading.name,
                    pct: Math.round(downloading.pct),
                  })}
                  {downloading.detail ? ` — ${downloading.detail}` : ""}
                </p>
                <div className="h-1.5 rounded-full bg-glyvex-surface-code overflow-hidden mt-1.5">
                  <div
                    className="h-full bg-glyvex-accent transition-all"
                    style={{ width: `${downloading.pct}%` }}
                  />
                </div>
              </div>
            )}

            {downloadError && downloadError.kind !== "piperVoice" && !downloading && (
              <p className="flex items-start gap-1.5 text-sm text-red-400">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                {downloadError.message}
              </p>
            )}
          </>
        )}

        {(stt.engine === "whisper" || (stt.engine === "auto" && !packaged)) && (
          <>
            <div className="flex items-start justify-between gap-4">
              <StatusLine
                loading={checking === "stt"}
                ready={whisper.installed}
                reason={whisper.reason}
                okLabel={
                  whisper.cached
                    ? t("chatSettings.whisperReadyCached", { model: whisper.model })
                    : t("chatSettings.whisperInstalled", { mb: whisper.download_mb || "?" })
                }
              />
              <CheckButton onClick={() => loadStatus("stt")} checking={checking === "stt"} />
            </div>

            <Field label={t("chatSettings.model")}>
              <select
                className={formInputClasses}
                value={stt.whisper_model || "base"}
                onChange={(e) => patch("stt", { whisper_model: e.target.value })}
              >
                {WHISPER_MODELS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id} — {m.size}
                    {m.noteKey ? ` (${t(m.noteKey)})` : ""}
                  </option>
                ))}
              </select>
            </Field>

            <div className="grid grid-cols-2 gap-4">
              <Field label={t("chatSettings.device")}>
                <select
                  className={formInputClasses}
                  value={stt.whisper_device || "auto"}
                  onChange={(e) => patch("stt", { whisper_device: e.target.value })}
                >
                  <option value="auto">{t("chatSettings.deviceAuto")}</option>
                  <option value="cpu">CPU</option>
                  <option value="cuda">CUDA</option>
                </select>
              </Field>
              <Field label={t("chatSettings.computeType")} hint={t("chatSettings.computeHint")}>
                <select
                  className={formInputClasses}
                  value={stt.whisper_compute_type || "int8"}
                  onChange={(e) => patch("stt", { whisper_compute_type: e.target.value })}
                >
                  <option value="int8">int8</option>
                  <option value="int8_float16">int8_float16</option>
                  <option value="float16">float16</option>
                  <option value="float32">float32</option>
                </select>
              </Field>
            </div>

            <Field
              label={t("chatSettings.whisperLanguage")}
              hint={t("chatSettings.whisperLanguageHint")}
            >
              <input
                className={formInputClasses}
                value={stt.whisper_language || ""}
                onChange={(e) => patch("stt", { whisper_language: e.target.value })}
                placeholder="es"
              />
            </Field>
          </>
        )}

      </Section>

      <Section title={t("chatSettings.sectionTts")}>
        <div className="flex items-start justify-between gap-4">
          <StatusLine
            loading={checking === "tts"}
            ready={ttsStatus?.platform_ok}
            reason={ttsStatus?.reason}
            okLabel={
              ttsStatus?.active_engine === "kokoro"
                ? t("chatSettings.ttsOkKokoro", { voice: ttsStatus?.kokoro?.voice || "—" })
                : ttsStatus?.active_engine === "piper"
                  ? t("chatSettings.ttsOkPiper", { voice: ttsStatus?.piper?.voice || "—" })
                  : t("chatSettings.ttsOk", {
                      voice: ttsStatus?.voice || t("chatSettings.ttsVoiceSystem"),
                    })
            }
          />
          <CheckButton onClick={() => loadStatus("tts")} checking={checking === "tts"} />
        </div>

        <Toggle
          label={t("chatSettings.ttsEnable")}
          checked={tts.enabled !== false}
          onChange={(v) => patch("tts", { enabled: v })}
        />

        <Field
          label={t("chatSettings.ttsEngine")}
          hint={
            tts.engine === "sapi"
              ? t("chatSettings.ttsEngineHintSapi")
              : tts.engine === "piper"
                ? t("chatSettings.ttsEngineHintPiper")
                : tts.engine === "kokoro"
                  ? t("chatSettings.ttsEngineHintKokoro")
                  : t("chatSettings.ttsEngineHintAuto")
          }
        >
          <select
            className={formInputClasses}
            value={tts.engine || "auto"}
            onChange={(e) => patch("tts", { engine: e.target.value })}
          >
            <option value="auto">{t("chatSettings.ttsEngineAuto")}</option>
            <option value="sapi">{t("chatSettings.ttsEngineSapi")}</option>
            <option value="piper">{t("chatSettings.ttsEnginePiper")}</option>
            <option value="kokoro">{t("chatSettings.ttsEngineKokoro")}</option>
          </select>
        </Field>

        {(tts.engine || "auto") !== "piper" && (tts.engine || "auto") !== "kokoro" && (
          <Field label={t("chatSettings.ttsVoice")} hint={t("chatSettings.ttsVoiceHint")}>
            <select
              className={formInputClasses}
              value={tts.voice || ""}
              onChange={(e) => patch("tts", { voice: e.target.value })}
            >
              <option value="">{t("chatSettings.ttsVoiceAuto")}</option>
              {(ttsStatus?.voices || []).map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name} ({v.culture})
                </option>
              ))}
            </select>
          </Field>
        )}

        {(tts.engine || "auto") !== "sapi" && (
          <PiperVoice
            t={t}
            piper={ttsStatus?.piper || {}}
            voiceName={tts.piper_voice || ""}
            onVoice={(name) => patch("tts", { piper_voice: name })}
            downloading={downloading}
            downloadError={downloadError}
            onDownload={(name) =>
              startDownload("piperVoice", `/api/tts/piper/voices/${name}/download`, name)
            }
          />
        )}

        {(tts.engine || "auto") !== "piper" && (tts.engine || "auto") !== "sapi" && (
          <KokoroVoice
            t={t}
            kokoro={ttsStatus?.kokoro || {}}
            voiceName={tts.kokoro_voice || ""}
            onVoice={(name) => patch("tts", { kokoro_voice: name })}
            downloading={downloading}
            downloadError={downloadError}
            onDownload={() => startDownload("kokoroModel", "/api/tts/kokoro/model/download", "kokoro")}
            onDelete={() => {
              if (
                !window.confirm(
                  t("chatSettings.ttsKokoroDeleteConfirm", {
                    mb: Math.round(ttsStatus?.kokoro?.download_mb || 0),
                  })
                )
              )
                return;
              fetch("/api/tts/kokoro/model", { method: "DELETE" }).then(() => loadStatus());
            }}
          />
        )}

        <NumberField
          label={t("chatSettings.ttsRate")}
          hint={t("chatSettings.ttsRateHint")}
          min={-10}
          max={10}
          step={1}
          value={tts.rate ?? 0}
          onCommit={(n) => patch("tts", { rate: n })}
        />
      </Section>

      <Section title={t("chatSettings.sectionAttachments")}>
        <div className="grid grid-cols-2 gap-4">
          <NumberField
            label={t("chatSettings.maxFileMb")}
            min={1}
            max={200}
            value={attachments.max_file_mb ?? 16}
            onCommit={(n) => patch("attachments", { max_file_mb: n })}
          />
          <NumberField
            label={t("chatSettings.maxFiles")}
            min={1}
            max={50}
            value={attachments.max_files_per_message ?? 10}
            onCommit={(n) => patch("attachments", { max_files_per_message: n })}
          />
        </div>

        <NumberField
          label={t("chatSettings.maxTextChars")}
          hint={t("chatSettings.maxTextCharsHint")}
          min={1000}
          max={1000000}
          step={5000}
          value={attachments.max_text_chars ?? 50000}
          onCommit={(n) => patch("attachments", { max_text_chars: n })}
        />

        <NumberField
          label={t("chatSettings.imageTokens")}
          hint={t("chatSettings.imageTokensHint")}
          min={64}
          max={16384}
          step={64}
          value={attachments.image_tokens_estimate ?? 1024}
          onCommit={(n) => patch("attachments", { image_tokens_estimate: n })}
        />
      </Section>
    </>
  );
}

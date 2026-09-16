import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, RefreshCw } from "lucide-react";
import Section from "./ui/Section.jsx";
import Field from "./ui/Field.jsx";
import { formInputClasses } from "../lib/styles.js";

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
  { id: "tiny", label: "tiny — ~75 MB" },
  { id: "base", label: "base — ~145 MB (default)" },
  { id: "small", label: "small — ~484 MB" },
  { id: "medium", label: "medium — ~1.5 GB" },
  { id: "large-v3-turbo", label: "large-v3-turbo — ~1.6 GB (conviene GPU)" },
  { id: "distil-large-v3", label: "distil-large-v3 — ~1.5 GB" },
];

const FALLBACK_PROVIDERS = [{ id: "ddgs", label: "DuckDuckGo", requires: null, hint: null }];

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
  if (loading) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-glyvex-muted">
        <Loader2 size={13} className="animate-spin" /> Comprobando…
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
      <span>{reason || "No disponible"}</span>
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

function CheckButton({ onClick, checking }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={checking}
      title="Vuelve a consultar el servicio con la configuración ya guardada"
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30 disabled:opacity-50"
    >
      <RefreshCw size={12} className={checking ? "animate-spin" : ""} />
      Comprobar
    </button>
  );
}

export default function ChatSettings({ config, onChange }) {
  const [toolsStatus, setToolsStatus] = useState(null);
  const [sttStatus, setSttStatus] = useState(null);
  const [checking, setChecking] = useState(false);

  const loadStatus = useCallback(async () => {
    setChecking(true);
    const [tools, stt] = await Promise.all([
      fetch("/api/chat/tools/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/stt/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    setToolsStatus(tools);
    setSttStatus(stt);
    setChecking(false);
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const tools = config.tools || {};
  const stt = config.stt || {};
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

  return (
    <>
      <Section title="Búsqueda web">
        <div className="flex items-start justify-between gap-4">
          <StatusLine
            loading={checking}
            ready={toolsStatus?.search?.ready}
            reason={toolsStatus?.search?.reason}
            okLabel={`${toolsStatus?.search?.provider_label || "El proveedor"} responde correctamente`}
          />
          <CheckButton onClick={loadStatus} checking={checking} />
        </div>

        <Field label="Proveedor" hint={activeProvider?.hint}>
          <select
            className={formInputClasses}
            value={tools.search_provider || "ddgs"}
            onChange={(e) => patch("tools", { search_provider: e.target.value })}
          >
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.requires ? ` — necesita ${p.requires}` : ""}
              </option>
            ))}
          </select>
        </Field>

        {tools.search_provider === "searxng" && (
          <Field
            label="URL de SearXNG"
            hint="La imagen Docker escucha en 8080 adentro del contenedor, el mismo puerto que llama-server: el mapeo habitual es -p 8888:8080."
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
            La API key se lee de la variable de entorno{" "}
            <code className="px-1 rounded bg-black/40 text-glyvex-text">
              {tools.search_provider === "brave" ? "BRAVE_API_KEY" : "TAVILY_API_KEY"}
            </code>
            . No se edita acá para no dejarla escrita en config.json.
          </p>
        )}

        <div className="grid grid-cols-2 gap-4">
          <NumberField
            label="Resultados por búsqueda"
            min={1}
            max={10}
            value={tools.max_results ?? 5}
            onCommit={(n) => patch("tools", { max_results: n })}
          />
          <NumberField
            label="Caracteres por página leída"
            min={1000}
            max={200000}
            step={1000}
            value={tools.fetch_max_chars ?? 8000}
            onCommit={(n) => patch("tools", { fetch_max_chars: n })}
          />
        </div>

        <NumberField
          label="Usos de herramientas por turno"
          hint="Tope de idas y vueltas entre el modelo y las tools dentro de una misma respuesta."
          min={1}
          max={20}
          value={tools.max_rounds ?? 5}
          onCommit={(n) => patch("tools", { max_rounds: n })}
        />

        <Toggle
          label="Permitir leer direcciones de red interna"
          hint="La URL de fetch_url la elige el modelo, no vos. Un resultado de búsqueda manipulado podría dirigirlo a un servicio de tu red. Activalo solo si querés que lea documentación interna."
          checked={tools.allow_private_hosts}
          onChange={(v) => patch("tools", { allow_private_hosts: v })}
        />
      </Section>

      <Section title="Voz a texto">
        <Field
          label="Motor"
          hint={
            stt.engine === "browser"
              ? "Transcribe el navegador. En Chrome el audio sale hacia servidores de Google, y la API no existe dentro del paquete de escritorio."
              : stt.engine === "whisper"
                ? "Transcripción local con faster-whisper. No sale nada de la máquina."
                : "Usa el motor del navegador si está disponible y cae a Whisper local si no. Es lo que hace falta para que funcione tanto en el navegador como en el paquete de escritorio."
          }
        >
          <select
            className={formInputClasses}
            value={stt.engine || "auto"}
            onChange={(e) => patch("stt", { engine: e.target.value })}
          >
            <option value="auto">Automático (recomendado)</option>
            <option value="browser">Navegador — Web Speech API</option>
            <option value="whisper">Whisper local</option>
          </select>
        </Field>

        {stt.engine !== "browser" && (
          <>
            <div className="flex items-start justify-between gap-4">
              <StatusLine
                loading={checking}
                ready={whisper.installed}
                reason={whisper.reason}
                okLabel={
                  whisper.cached
                    ? `Modelo ${whisper.model} descargado y listo`
                    : `faster-whisper instalado — el primer uso descarga ~${whisper.download_mb || "?"} MB`
                }
              />
              <CheckButton onClick={loadStatus} checking={checking} />
            </div>

            <Field label="Modelo">
              <select
                className={formInputClasses}
                value={stt.whisper_model || "base"}
                onChange={(e) => patch("stt", { whisper_model: e.target.value })}
              >
                {WHISPER_MODELS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Field>

            <div className="grid grid-cols-2 gap-4">
              <Field label="Dispositivo">
                <select
                  className={formInputClasses}
                  value={stt.whisper_device || "auto"}
                  onChange={(e) => patch("stt", { whisper_device: e.target.value })}
                >
                  <option value="auto">Automático</option>
                  <option value="cpu">CPU</option>
                  <option value="cuda">CUDA</option>
                </select>
              </Field>
              <Field label="Precisión" hint="int8 anda en cualquier CPU.">
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
              label="Idioma de la transcripción"
              hint="Vacío = detección automática. Si siempre dictás en el mismo idioma, fijarlo mejora la precisión."
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

        {stt.engine !== "whisper" && (
          <Field label="Idioma del dictado del navegador" hint="Formato BCP-47: es-AR, es-ES, en-US.">
            <input
              className={formInputClasses}
              value={stt.language || ""}
              onChange={(e) => patch("stt", { language: e.target.value })}
              placeholder="es-AR"
            />
          </Field>
        )}
      </Section>

      <Section title="Adjuntos del chat">
        <div className="grid grid-cols-2 gap-4">
          <NumberField
            label="Tamaño máximo por archivo (MB)"
            min={1}
            max={200}
            value={attachments.max_file_mb ?? 16}
            onCommit={(n) => patch("attachments", { max_file_mb: n })}
          />
          <NumberField
            label="Archivos por mensaje"
            min={1}
            max={50}
            value={attachments.max_files_per_message ?? 10}
            onCommit={(n) => patch("attachments", { max_files_per_message: n })}
          />
        </div>

        <NumberField
          label="Caracteres extraídos por archivo"
          hint="El texto extraído se guarda dentro del mensaje, así que esto también acota cuánto crece la base de datos."
          min={1000}
          max={1000000}
          step={5000}
          value={attachments.max_text_chars ?? 50000}
          onCommit={(n) => patch("attachments", { max_text_chars: n })}
        />

        <NumberField
          label="Tokens estimados por imagen"
          hint="Solo para el aviso de contexto antes de enviar. El costo real varía mucho entre modelos de visión."
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

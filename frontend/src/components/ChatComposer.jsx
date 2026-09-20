import { useRef } from "react";
import { Send, Square, Paperclip, AlertTriangle, Globe } from "lucide-react";
import ReasoningControl from "./ReasoningControl.jsx";
import AttachmentChips from "./AttachmentChips.jsx";
import MicButton from "./MicButton.jsx";
import { inputClasses } from "../lib/styles.js";

/**
 * El picker no filtra nada a propósito.
 *
 * Antes se listaban extensiones concretas y `*\/*` al final. El problema es
 * que al mezclar tipos específicos con el comodín, Chrome arma un filtro
 * "Archivos personalizados" y lo deja SELECCIONADO por defecto: los archivos
 * que no están en la lista (un .pcap, un .bin, cualquier extensión propia)
 * aparecen grisados hasta que el usuario cambia el desplegable a "Todos los
 * archivos". Como el backend acepta cualquier cosa igual —lo que no sabe
 * interpretar se adjunta con su metadata y es el modelo quien dice que no
 * puede leerlo— filtrar acá solo escondía opciones válidas.
 */
const SUGGESTED_ACCEPT = "*/*";

function ContextWarning({ estimate }) {
  if (!estimate || estimate.fits) return null;

  const over = Math.abs(estimate.headroom).toLocaleString();
  return (
    <div className="flex items-start gap-2 mb-2 px-2.5 py-2 rounded-md border border-amber-500/40 bg-amber-500/10 text-xs text-amber-300">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span>
        Este envío supera el contexto del modelo por unos {over} tokens
        {estimate.source === "heuristic" && " (estimado, el endpoint no expone tokenizer)"}.
        Quitá algún adjunto, empezá una conversación nueva o bajá max_tokens.
      </span>
    </div>
  );
}

/**
 * Área de escritura del chat: razonamiento, adjuntos, textarea y envío.
 *
 * El drag & drop se maneja en Chat.jsx sobre toda el área de conversación
 * (se puede soltar en cualquier parte, no solo acá); el paste sí vive en el
 * textarea, que es donde el usuario tiene el foco.
 */
export default function ChatComposer({
  input,
  onInputChange,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  reasoning,
  onReasoningChange,
  streaming,
  onSend,
  onStop,
  estimate,
  visionReady,
  capabilitiesSource,
  busyWithAttachments,
  toolsEnabled,
  onToolsToggle,
  toolsSupported,
  toolsStatus,
  sttStatus,
  onTranscript,
  onInterimTranscript,
  interimTranscript,
  onDictationError,
}) {
  const fileInputRef = useRef(null);

  function handleFileInput(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (files.length > 0) onAddFiles(files);
  }

  function handlePaste(event) {
    const items = Array.from(event.clipboardData?.items || []);
    const files = items
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (files.length === 0) return;
    // Solo interceptamos si vino un archivo: pegar texto sigue funcionando.
    event.preventDefault();
    onAddFiles(files);
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      onSend();
    }
  }

  const attachTitle = visionReady
    ? "Adjuntar archivos (imágenes, documentos, código)"
    : capabilitiesSource === "props"
      ? "Adjuntar archivos — el modelo activo no acepta imágenes"
      : "Adjuntar archivos — no se pudo confirmar si el modelo acepta imágenes";

  const canSend = Boolean(input.trim() || attachments.length > 0);

  // El toggle se deshabilita por dos motivos distintos y el tooltip los
  // separa: o el modelo no sabe llamar tools, o el proveedor de búsqueda no
  // está listo. El backend ya manda el motivo redactado, así que se muestra
  // tal cual en vez de reconstruirlo acá.
  const search = toolsStatus?.search;
  const searchReady = search ? search.ready !== false : true;
  const toolsBlocked = !toolsSupported || !searchReady;
  const providerLabel = search?.provider_label || "el proveedor de búsqueda";

  let toolsTitle = toolsEnabled
    ? `Búsqueda web activada para esta conversación (${providerLabel})`
    : `Activar búsqueda web para esta conversación (${providerLabel})`;
  if (!toolsSupported) {
    toolsTitle =
      capabilitiesSource === "props"
        ? "El chat template del modelo activo no soporta tool-calling"
        : "No se pudo confirmar si el modelo soporta tool-calling";
  } else if (!searchReady) {
    toolsTitle = search?.reason || `${providerLabel} no está disponible`;
  }

  return (
    <div className="border-t border-white/10 p-3">
      <ContextWarning estimate={estimate} />
      <AttachmentChips attachments={attachments} onRemove={onRemoveAttachment} />

      {interimTranscript && (
        <p className="mb-2 px-2.5 py-1.5 rounded-md border border-white/10 bg-black/20 text-xs text-glyvex-muted italic">
          {interimTranscript}
        </p>
      )}

      <div className="flex items-end gap-2">
        <div className="flex items-center gap-1 shrink-0">
          <ReasoningControl value={reasoning} onChange={onReasoningChange} />
          <button
            type="button"
            onClick={() => onToolsToggle(!toolsEnabled)}
            disabled={toolsBlocked}
            aria-pressed={toolsEnabled}
            title={toolsTitle}
            className={
              "flex items-center h-9 px-2 rounded-md border shrink-0 disabled:opacity-40 " +
              (toolsEnabled
                ? "border-sky-500/40 bg-sky-500/10 text-sky-400"
                : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30")
            }
          >
            <Globe size={16} />
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            title={attachTitle}
            className="flex items-center h-9 px-2 rounded-md border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30"
          >
            <Paperclip size={16} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={SUGGESTED_ACCEPT}
            className="hidden"
            onChange={handleFileInput}
          />
          <MicButton
            status={sttStatus}
            onTranscript={onTranscript}
            onInterim={onInterimTranscript}
            onError={onDictationError}
            disabled={streaming}
          />
        </div>

        <textarea
          className={inputClasses + " min-h-[44px] max-h-40 resize-y flex-1"}
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder="Escribí un mensaje… (Enter envía, Shift+Enter nueva línea; arrastrá o pegá archivos para adjuntarlos)"
        />

        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            className="flex items-center gap-2 px-4 py-2.5 rounded-md text-sm font-medium bg-red-600 text-white hover:bg-red-500 shrink-0"
          >
            <Square size={16} />
            Detener
          </button>
        ) : (
          <button
            type="button"
            onClick={onSend}
            disabled={!canSend || busyWithAttachments}
            title={busyWithAttachments ? "Esperando a que terminen de procesarse los adjuntos" : undefined}
            className="flex items-center gap-2 px-4 py-2.5 rounded-md text-sm font-medium bg-glyvex-accent text-white hover:bg-glyvex-accent/90 disabled:opacity-50 shrink-0"
          >
            <Send size={16} />
            Enviar
          </button>
        )}
      </div>
    </div>
  );
}

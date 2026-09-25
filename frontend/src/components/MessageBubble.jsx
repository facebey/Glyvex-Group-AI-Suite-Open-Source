import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FileText, EyeOff, AlertTriangle, Pencil, RefreshCw, ChevronLeft, ChevronRight,
  X, Check, CornerDownRight,
} from "lucide-react";
import ThinkingPanel from "./ThinkingPanel.jsx";
import ToolSummary, { ToolActivityLive } from "./ToolActivity.jsx";
import StreamStatus from "./StreamStatus.jsx";
import MarkdownMessage from "./MarkdownMessage.jsx";
import { CopyButton } from "./CodeBlock.jsx";
import ReasoningControl from "./ReasoningControl.jsx";
import { composerInputClasses } from "../lib/styles.js";

function formatTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return ""; }
}

function formatSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Adjuntos del mensaje ya enviado. Las imágenes se muestran como miniatura
 * servida desde /api/chat/attachments/<id>/raw (nunca base64 en el estado);
 * el resto como una etiqueta con el nombre y el tamaño.
 */
function MessageAttachments({ attachments }) {
  const { t } = useTranslation();
  if (!attachments || attachments.length === 0) return null;

  // Se muestra la miniatura de toda imagen que tenga archivo servible, no
  // solo de las que se enviaron al modelo: si quedó en vision_unsupported,
  // el usuario igual la adjuntó y quiere verla (con el aviso de que no se
  // mandó). Antes se exigía status === "ready", y como el backend solo
  // devolvía `url` en ese caso, la miniatura no aparecía nunca.
  const images = attachments.filter(
    (a) => a.kind === "image" && a.url && a.status !== "error"
  );
  const rest = attachments.filter((a) => !images.includes(a));

  return (
    <div className="flex flex-col gap-1.5 mb-1.5 w-full">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {images.map((a) => {
            const skipped = a.status === "vision_unsupported";
            return (
              <a
                key={a.id}
                href={a.url}
                target="_blank"
                rel="noreferrer"
                title={skipped ? t("message.notSent", { filename: a.filename }) : a.filename}
                className="relative block"
              >
                <img
                  src={a.url}
                  alt={a.filename}
                  loading="lazy"
                  className={
                    "h-24 w-24 object-cover rounded-md border " +
                    (skipped ? "border-amber-500/40 opacity-50" : "border-glyvex-border-soft")
                  }
                />
                {skipped && (
                  <span
                    className="absolute bottom-1 right-1 p-0.5 rounded bg-black/70 text-amber-300"
                    title={t("message.visionNotSent")}
                  >
                    <EyeOff size={12} />
                  </span>
                )}
              </a>
            );
          })}
        </div>
      )}

      {rest.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {rest.map((a) => {
            const skipped = a.status === "vision_unsupported";
            const failed = a.status === "error";
            const Icon = skipped ? EyeOff : failed ? AlertTriangle : FileText;
            return (
              <span
                key={a.id}
                title={a.error || a.note || a.filename}
                className={
                  "inline-flex items-center gap-1.5 px-2 py-1 rounded-md border text-xs " +
                  (failed
                    ? "border-red-500/40 bg-red-500/10 text-red-300"
                    : skipped
                      ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                      : "border-glyvex-border-soft bg-glyvex-veil-disabled text-glyvex-bg-text")
                }
              >
                <Icon size={12} className="shrink-0" />
                <span className="truncate max-w-[180px]">{a.filename}</span>
                {a.size > 0 && <span className="opacity-60 tabular-nums">{formatSize(a.size)}</span>}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Navegación entre ramas: "< 2/3 >". Solo aparece si hay más de una. */
function BranchNav({ branch, onSwitch }) {
  const { t } = useTranslation();
  if (!branch || branch.total <= 1) return null;
  return (
      <span className="inline-flex items-center gap-0.5 text-xs text-glyvex-bg-muted">
      <button
        type="button"
        onClick={() => onSwitch(-1)}
        disabled={branch.index <= 1}
        title={t("message.prevVersion")}
        className="p-0.5 rounded hover:bg-glyvex-veil-strong hover:text-glyvex-bg-text disabled:opacity-30"
      >
        <ChevronLeft size={12} />
      </button>
      <span className="tabular-nums px-0.5">
        {branch.index}/{branch.total}
      </span>
      <button
        type="button"
        onClick={() => onSwitch(1)}
        disabled={branch.index >= branch.total}
        title={t("message.nextVersion")}
        className="p-0.5 rounded hover:bg-glyvex-veil-strong hover:text-glyvex-bg-text disabled:opacity-30"
      >
        <ChevronRight size={12} />
      </button>
    </span>
  );
}

/** Editor inline de un mensaje del usuario. */
function EditBox({ initial, onSubmit, onCancel }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(initial);
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  function handleKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (draft.trim()) onSubmit(draft.trim());
    }
  }

  return (
    <div className="w-full min-w-[320px]">
      <textarea
        ref={ref}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        className={composerInputClasses + " min-h-[72px] max-h-60 resize-y"}
      />
      <div className="flex items-center justify-end gap-2 mt-1.5">
        <span className="text-xs text-glyvex-bg-muted mr-auto">
          {t("message.editNote")}
        </span>
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs border border-glyvex-border-soft text-glyvex-bg-muted hover:text-glyvex-bg-text hover:bg-glyvex-veil-disabled"
        >
          <X size={12} /> {t("message.cancel")}
        </button>
        <button
          type="button"
          onClick={() => draft.trim() && onSubmit(draft.trim())}
          disabled={!draft.trim()}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-glyvex-accent text-white hover:bg-glyvex-accent/90 disabled:opacity-50"
        >
          <Check size={12} /> {t("message.send")}
        </button>
      </div>
    </div>
  );
}

export default function MessageBubble({
  message,
  streaming = false,
  branch = null,
  onSwitchBranch,
  onEdit,
  onRegenerate,
  onContinue,
  reasoning,
  busy = false,
  // Solo para la respuesta que se está generando: fase actual (ver
  // StreamStatus.streamPhase) y tools en curso.
  livePhase = null,
  liveTools = null,
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  // Nivel de razonamiento con el que se va a regenerar. Arranca en el de la
  // conversación y se puede cambiar sin afectar al próximo mensaje.
  const [regenReasoning, setRegenReasoning] = useState(reasoning);
  useEffect(() => setRegenReasoning(reasoning), [reasoning]);
  const isUser = message.role === "user";
  const hasAttachments = Array.isArray(message.attachments) && message.attachments.length > 0;
  // Solo tiene sentido continuar si el corte fue por max_tokens y la
  // respuesta ya terminó de streamear.
  const canContinue =
    !isUser && !streaming && !busy && message.finish_reason === "length" && Boolean(message.content);

  const hasText = Boolean(message.content && message.content.trim());
  // Duración del razonamiento: desde el primer token hasta la primera palabra
  // de la respuesta. Si no hubo respuesta (solo pensó), hasta el final.
  const m = message.metrics;
  const thinkingSeconds =
    m?.ttft_ms != null && m?.ttft_answer_ms != null
      ? (m.ttft_answer_ms - m.ttft_ms) / 1000
      : m?.ttft_ms != null && m?.duration_s != null
        ? m.duration_s - m.ttft_ms / 1000
        : null;

  if (isUser && editing) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] w-full min-w-0 flex flex-col items-end">
          <EditBox
            initial={message.content || ""}
            onCancel={() => setEditing(false)}
            onSubmit={(text) => {
              setEditing(false);
              onEdit?.(message, text);
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} group`}>
      {/* Las respuestas del asistente usan todo el ancho: al 75% quedaban en
          ~570 px útiles y cualquier bloque de código o tabla necesitaba barra
          horizontal. min-w-0 evita que un contenido muy ancho estire la
          columna por fuera del chat. */}
      <div
        className={
          "min-w-0 flex flex-col " +
          (isUser ? "max-w-[85%] items-end" : "max-w-full w-full items-start")
        }
      >
        {!isUser && <ToolSummary activity={message.tool_activity} />}
        {!isUser && (
          <ThinkingPanel
            thinking={message.thinking}
            active={streaming && (livePhase === "thinking" || livePhase === "analyzing")}
            durationSeconds={thinkingSeconds}
          />
        )}
        {!isUser && streaming && liveTools?.length > 0 && (
          <div className="mb-2 max-w-full">
            <ToolActivityLive entries={liveTools} />
          </div>
        )}
        {isUser && <MessageAttachments attachments={message.attachments} />}

        {!isUser && streaming && !hasText && <StreamStatus phase={livePhase} />}

        {!isUser && !streaming && !hasText && (
          <p className="text-xs italic text-glyvex-bg-muted px-1">
            {message.thinking || message.tool_activity?.length
              ? t("message.noText")
              : t("message.stoppedEarly")}
          </p>
        )}

        {(isUser ? message.content || !hasAttachments : hasText) && (
          <div
            className={
              "rounded-lg px-4 py-2.5 text-sm break-words [overflow-wrap:anywhere] min-w-0 max-w-full " +
              (isUser
                ? "bg-glyvex-accent text-white whitespace-pre-wrap"
                : "bg-glyvex-card text-glyvex-text")
            }
          >
            {isUser ? (
              message.content
            ) : (
              <>
                <MarkdownMessage content={message.content} />
                {streaming && (
                  <span className="inline-block w-1.5 h-4 bg-current align-text-bottom ml-0.5 animate-pulse" />
                )}
              </>
            )}
          </div>
        )}

        {canContinue && (
          <button
            type="button"
            onClick={() => onContinue?.(message)}
            title={t("message.continueTitle")}
            className="mt-1.5 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20"
          >
            <CornerDownRight size={12} />
            {t("message.continue")}
          </button>
        )}

        {/* Barra de acciones: visible al pasar por encima, o siempre que haya
            ramas para navegar (si no, no habría cómo descubrirlas). */}
        <div
          className={
            "flex items-center gap-2 mt-1 transition-opacity " +
            (branch?.total > 1 ? "opacity-100" : "opacity-0 group-hover:opacity-100")
          }
        >
          <BranchNav branch={branch} onSwitch={(dir) => onSwitchBranch?.(message, dir)} />

          <span className="text-xs text-glyvex-bg-muted">{formatTime(message.timestamp)}</span>

          {!isUser && message.metrics?.tps ? (
            <span className="text-xs text-glyvex-bg-muted font-mono">
              {message.metrics.metrics_source === "chunks" ? "~" : ""}
              {message.metrics.tps.toFixed(1)} t/s
              {message.metrics.ttft_ms ? ` · ${Math.round(message.metrics.ttft_ms)}ms TTFT` : ""}
            </span>
          ) : null}

          {!streaming && message.content && (
            <CopyButton text={message.content} label={t("message.copyMessage")} className="p-0.5" size={12} />
          )}

          {isUser && !streaming && !busy && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              title={t("message.editTitle")}
              className="p-0.5 rounded text-glyvex-bg-muted hover:text-glyvex-bg-text hover:bg-glyvex-veil-strong"
            >
              <Pencil size={12} />
            </button>
          )}

          {!isUser && !streaming && !busy && (
            <span className="inline-flex items-center gap-0.5">
              <span className="scale-90 origin-center">
                <ReasoningControl value={regenReasoning} onChange={setRegenReasoning} />
              </span>
              <button
                type="button"
                onClick={() => onRegenerate?.(message, regenReasoning)}
                title={t("message.regenerateTitle")}
                className="p-0.5 rounded text-glyvex-bg-muted hover:text-glyvex-bg-text hover:bg-glyvex-veil-strong"
              >
                <RefreshCw size={12} />
              </button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

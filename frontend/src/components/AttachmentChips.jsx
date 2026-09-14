import {
  FileText, FileSpreadsheet, FileCode2, FileImage, Presentation,
  File as FileIcon, X, Loader2, AlertTriangle, EyeOff,
} from "lucide-react";

/** Extensiones agrupadas por el ícono que las representa. */
const ICON_BY_EXT = {
  pdf: FileText,
  doc: FileText, docx: FileText, odt: FileText, rtf: FileText,
  xls: FileSpreadsheet, xlsx: FileSpreadsheet, xlsm: FileSpreadsheet,
  csv: FileSpreadsheet, tsv: FileSpreadsheet,
  ppt: Presentation, pptx: Presentation, odp: Presentation,
  py: FileCode2, js: FileCode2, jsx: FileCode2, ts: FileCode2, tsx: FileCode2,
  json: FileCode2, yaml: FileCode2, yml: FileCode2, toml: FileCode2,
  sh: FileCode2, bash: FileCode2, ps1: FileCode2, sql: FileCode2,
  html: FileCode2, css: FileCode2, xml: FileCode2, go: FileCode2,
  rs: FileCode2, java: FileCode2, c: FileCode2, cpp: FileCode2, rb: FileCode2,
  txt: FileText, md: FileText, log: FileText,
};

function iconFor(attachment) {
  if (attachment.kind === "image") return FileImage;
  const ext = (attachment.filename || "").split(".").pop()?.toLowerCase();
  return ICON_BY_EXT[ext] || FileIcon;
}

function formatSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Trunca por el medio: "presupuesto-escondida-2026.xlsx" → "presupuesto-esc….xlsx" */
function truncateName(name, max = 24) {
  if (!name || name.length <= max) return name;
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || name.length - dot > 8) return `${name.slice(0, max - 1)}…`;
  const ext = name.slice(dot);
  return `${name.slice(0, max - ext.length - 1)}…${ext}`;
}

const STATUS_STYLES = {
  pending: "border-white/10 bg-black/30 text-glyvex-muted",
  ready: "border-white/10 bg-black/30 text-glyvex-text",
  error: "border-red-500/40 bg-red-500/10 text-red-300",
  vision_unsupported: "border-amber-500/40 bg-amber-500/10 text-amber-300",
};

function StatusIcon({ attachment }) {
  if (attachment.status === "pending") {
    return <Loader2 size={13} className="animate-spin shrink-0" />;
  }
  if (attachment.status === "error") {
    return <AlertTriangle size={13} className="shrink-0" />;
  }
  if (attachment.status === "vision_unsupported") {
    return <EyeOff size={13} className="shrink-0" />;
  }
  const Icon = iconFor(attachment);
  return <Icon size={13} className="text-glyvex-muted shrink-0" />;
}

function chipTitle(attachment) {
  const lines = [attachment.filename];
  if (attachment.mime) lines.push(attachment.mime);
  if (attachment.error) lines.push(attachment.error);
  if (attachment.note) lines.push(attachment.note);
  if (attachment.status === "vision_unsupported") {
    lines.push("El modelo activo no acepta imágenes, así que no se envía.");
  }
  if (attachment.tokens_estimate) {
    lines.push(`~${attachment.tokens_estimate.toLocaleString()} tokens`);
  }
  return lines.filter(Boolean).join("\n");
}

/**
 * Chips de los archivos adjuntados al mensaje que se está escribiendo.
 * Cada uno muestra tipo, nombre truncado, tamaño y estado, y se puede quitar.
 */
export default function AttachmentChips({ attachments, onRemove }) {
  if (!attachments || attachments.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 mb-2">
      {attachments.map((attachment) => {
        return (
          <div
            key={attachment.localId}
            title={chipTitle(attachment)}
            className={
              "inline-flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-md border text-xs " +
              (STATUS_STYLES[attachment.status] || STATUS_STYLES.ready)
            }
          >
            {attachment.kind === "image" && attachment.url ? (
              <img
                src={attachment.url}
                alt=""
                className="w-5 h-5 rounded object-cover border border-white/10 shrink-0"
              />
            ) : (
              <StatusIcon attachment={attachment} />
            )}

            <span className="truncate max-w-[160px]">
              {truncateName(attachment.filename)}
            </span>

            {attachment.size > 0 && (
              <span className="tabular-nums opacity-60">{formatSize(attachment.size)}</span>
            )}

            <button
              type="button"
              onClick={() => onRemove(attachment.localId)}
              title={`Quitar ${attachment.filename}`}
              className="p-0.5 rounded hover:bg-white/10 hover:text-glyvex-text"
            >
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

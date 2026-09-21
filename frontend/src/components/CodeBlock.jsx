import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, Download, WrapText } from "lucide-react";

/**
 * Copia al portapapeles con fallback.
 *
 * navigator.clipboard solo existe en contexto seguro (https o localhost).
 * main.py escucha en 127.0.0.1 por defecto, pero si se expone a la red con
 * GLYVEX_HOST y el usuario entra por IP desde otra máquina, la API no está.
 * El fallback con execCommand está deprecado pero sigue funcionando en todos
 * los navegadores actuales.
 */
export async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // permiso denegado — se intenta el fallback
    }
  }

  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

/** Botón de copiar que muestra un tilde un segundo y medio. */
export function CopyButton({ text, label, className = "", size = 13 }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [text]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? t("code.copied") : label || t("code.copy")}
      className={
        "inline-flex items-center gap-1 rounded hover:bg-white/10 " +
        (copied ? "text-emerald-400 " : "text-glyvex-muted hover:text-glyvex-text ") +
        className
      }
    >
      {copied ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}

/** Nombres lindos para las etiquetas más comunes de los fences. */
const LANGUAGE_LABELS = {
  js: "JavaScript", jsx: "JSX", ts: "TypeScript", tsx: "TSX",
  py: "Python", python: "Python", rb: "Ruby", rs: "Rust",
  sh: "Shell", bash: "Bash", zsh: "Zsh", ps1: "PowerShell", powershell: "PowerShell",
  yml: "YAML", yaml: "YAML", json: "JSON", toml: "TOML", xml: "XML",
  sql: "SQL", html: "HTML", css: "CSS", scss: "SCSS",
  cpp: "C++", cs: "C#", go: "Go", java: "Java", php: "PHP", kt: "Kotlin",
  dockerfile: "Dockerfile", ini: "INI", diff: "Diff", md: "Markdown",
};

function labelFor(language, t) {
  if (!language) return t("code.plainText");
  return LANGUAGE_LABELS[language.toLowerCase()] || language;
}

// Fences donde lo que viene es texto (logs, salidas de comandos, prosa) y
// no código: ahí conviene ajustar líneas por defecto. Sin lenguaje también.
const WRAP_BY_DEFAULT = new Set(["text", "txt", "plaintext", "plain", "log", "md", "markdown", "output", "console"]);

/**
 * Nombre seguro para el atributo `download`: el nombre viene del modelo, así
 * que se queda solo con el basename (sin separadores de ruta). Si queda
 * vacío, un nombre por defecto.
 */
function safeFilename(filename) {
  const base = String(filename || "").replace(/[/\\]+/g, "").trim();
  return base || "archivo.txt";
}

/** Descarga el texto como archivo (mismo patrón Blob + anchor de export.js). */
function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = safeFilename(filename);
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Envoltorio de un bloque de código: header con el lenguaje detectado,
 * ajuste de líneas y botón de copiar; debajo el <pre> que ya viene resaltado
 * por rehype-highlight.
 *
 * Ajuste de líneas: con el chat angosto, una línea larga dejaba el bloque con
 * barra de desplazamiento horizontal. El código conserva sus líneas por
 * defecto (la indentación importa) y se puede ajustar con un clic; el texto
 * plano se ajusta solo.
 */
export default function CodeBlock({ language, code, children, filename }) {
  const { t } = useTranslation();
  const [wrap, setWrap] = useState(() => !language || WRAP_BY_DEFAULT.has(language.toLowerCase()));

  return (
    <div className="my-2 rounded-md border border-white/10 overflow-hidden bg-black/40 max-w-full min-w-0">
      <div className="flex items-center justify-between gap-2 px-3 py-1 border-b border-white/10 bg-black/30">
        <span className="text-xs text-glyvex-muted font-mono truncate min-w-0">
          {labelFor(language, t)}
          {filename ? <span className="text-glyvex-text"> · {filename}</span> : null}
        </span>
        <span className="inline-flex items-center gap-0.5 shrink-0">
          {filename && (
            <button
              type="button"
              onClick={() => downloadText(filename, code)}
              title={t("code.download", { filename })}
              className="p-1 rounded hover:bg-white/10 text-glyvex-muted hover:text-glyvex-text"
            >
              <Download size={13} />
            </button>
          )}
          <button
            type="button"
            onClick={() => setWrap((v) => !v)}
            aria-pressed={wrap}
            title={wrap ? t("code.showFullLines") : t("code.wrapLines")}
            className={
              "p-1 rounded hover:bg-white/10 " +
              (wrap ? "text-glyvex-accent" : "text-glyvex-muted hover:text-glyvex-text")
            }
          >
            <WrapText size={13} />
          </button>
          <CopyButton text={code} label={t("code.copyBlock")} className="p-1" />
        </span>
      </div>
      {/* Sin ajuste, el scroll horizontal queda adentro del bloque. highlight.js
          le pone overflow-x al <code>, por eso el ajuste se aplica a los dos. */}
      <div
        className={
          "overflow-x-auto max-w-full text-sm [&>pre]:p-3 [&>pre]:m-0 [&>pre]:bg-transparent " +
          (wrap
            ? "[&>pre]:whitespace-pre-wrap [&_code]:whitespace-pre-wrap [&_code]:[overflow-wrap:anywhere] [&_code]:overflow-x-visible"
            : "[&_code]:[overflow-wrap:normal]")
        }
      >
        {children}
      </div>
    </div>
  );
}

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
        "inline-flex items-center gap-1 rounded hover:bg-glyvex-veil-strong " +
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
 * que se queda solo con el basename y sin los caracteres que Windows no acepta
 * en un archivo (`<>:"|?*` y los separadores de ruta). Sin ese filtro, un
 * `C:\tmp\notas.md` descargaba `C:tmpnotas.md`, que el sistema rechaza.
 * Si queda vacío, un nombre por defecto.
 */
export function safeFilename(filename) {
  const base = String(filename || "").replace(/[<>:"/\\|?*]+/g, "").trim();
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
 * Nombre por defecto cuando el modelo no nombró el archivo: se arma a partir de
 * la etiqueta del lenguaje (que ya está traducida), así el archivo que baja el
 * usuario se llama igual en español que en inglés.
 */
export function fallbackName(languageLabel) {
  const base = String(languageLabel || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "bloque";
  return `${base}.txt`;
}

/**
 * Envoltorio de un bloque de código: header con el lenguaje detectado, y una
 * barra de acciones AL PIE (descarga, ajuste de líneas, copiar); debajo del
 * header viene el <pre> que ya viene resaltado por rehype-highlight.
 *
 * Las acciones van al pie porque el bloque puede ocupar varias pantallas: con
 * la barra arriba había que subir todo el mensaje para poder copiar o descargar
 * al final de una respuesta larga.
 *
 * Ajuste de líneas: con el chat angosto, una línea larga dejaba el bloque con
 * barra de desplazamiento horizontal. El código conserva sus líneas por
 * defecto (la indentación importa) y se puede ajustar con un clic; el texto
 * plano se ajusta solo.
 */
export default function CodeBlock({ language, code, children, filename }) {
  const { t } = useTranslation();
  const [wrap, setWrap] = useState(() => !language || WRAP_BY_DEFAULT.has(language.toLowerCase()));
  const label = labelFor(language, t);
  // La descarga está disponible siempre: si el modelo no abrió la cerca con
  // `lenguaje:nombre`, se usa un nombre por defecto (antes el botón directo
  // no existía, y eso dependía de que el modelo obedeciera una instrucción).
  const downloadName = filename || fallbackName(label);

  return (
    <div className="my-2 rounded-md border border-glyvex-border-soft overflow-hidden bg-glyvex-surface-code max-w-full min-w-0">
      <div className="flex items-center justify-between gap-2 px-3 py-1 border-b border-glyvex-border-soft bg-glyvex-veil-disabled">
        <span className="text-xs text-glyvex-muted font-mono truncate min-w-0">
          {label}
          {filename ? <span className="text-glyvex-text"> · {filename}</span> : null}
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
      <div className="flex items-center justify-end gap-0.5 px-3 py-1 border-t border-glyvex-border-soft bg-glyvex-veil-disabled">
        <button
          type="button"
          onClick={() => downloadText(downloadName, code)}
          title={t("code.download", { filename: downloadName })}
          className="p-1 rounded hover:bg-glyvex-veil-strong text-glyvex-muted hover:text-glyvex-text"
        >
          <Download size={13} />
        </button>
        <button
          type="button"
          onClick={() => setWrap((v) => !v)}
          aria-pressed={wrap}
          title={wrap ? t("code.showFullLines") : t("code.wrapLines")}
          className={
            "p-1 rounded hover:bg-glyvex-veil-strong " +
            (wrap ? "text-glyvex-accent" : "text-glyvex-muted hover:text-glyvex-text")
          }
        >
          <WrapText size={13} />
        </button>
        <CopyButton text={code} label={t("code.copyBlock")} className="p-1" />
      </div>
    </div>
  );
}

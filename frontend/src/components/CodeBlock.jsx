import { useCallback, useState } from "react";
import { Check, Copy } from "lucide-react";

/**
 * Copia al portapapeles con fallback.
 *
 * navigator.clipboard solo existe en contexto seguro (https o localhost), y
 * main.py escucha en 0.0.0.0 — si el usuario entra por IP desde otra
 * máquina, la API no está. El fallback con execCommand está deprecado pero
 * sigue funcionando en todos los navegadores actuales.
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
      title={copied ? "Copiado" : label || "Copiar"}
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

function labelFor(language) {
  if (!language) return "texto";
  return LANGUAGE_LABELS[language.toLowerCase()] || language;
}

/**
 * Envoltorio de un bloque de código: header con el lenguaje detectado y
 * botón de copiar, y debajo el <pre> que ya viene resaltado por
 * rehype-highlight.
 */
export default function CodeBlock({ language, code, children }) {
  return (
    <div className="my-2 rounded-md border border-white/10 overflow-hidden bg-black/40">
      <div className="flex items-center justify-between px-3 py-1 border-b border-white/10 bg-black/30">
        <span className="text-xs text-glyvex-muted font-mono">{labelFor(language)}</span>
        <CopyButton text={code} label="Copiar bloque" className="p-1" />
      </div>
      <div className="overflow-x-auto text-sm [&>pre]:p-3 [&>pre]:m-0 [&>pre]:bg-transparent">
        {children}
      </div>
    </div>
  );
}

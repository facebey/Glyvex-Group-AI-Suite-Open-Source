import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import CodeBlock from "./CodeBlock.jsx";
import "highlight.js/styles/github-dark.css";

/** Texto plano de un nodo del AST, para poder copiarlo. */
function textOf(node) {
  if (node == null) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node.props?.children) return textOf(node.props.children);
  return "";
}

/**
 * Convención "lenguaje:nombre" (FILE_CONVENTION_PROMPT de Chat.jsx): el
 * modelo abre la cerca como ```python:mi_script.py```. remark-gfm convierte
 * la info string entera en la clase `language-python:mi_script.py`, que
 * rehype-highlight no reconoce (el "lenguaje" sería `python:mi_script.py` y
 * no resaltaría). Este transform corre ANTES de rehype-highlight: corta el
 * nombre del archivo, deja la clase con el lenguaje solo (para que el
 * resaltado funcione) y guarda el nombre en `data-filename` para CodeBlock.
 */
function rehypeFilename() {
  function split(code) {
    const cls = code.properties?.className;
    const list = Array.isArray(cls) ? cls : cls != null ? [cls] : [];
    const i = list.findIndex((c) => typeof c === "string" && c.startsWith("language-"));
    if (i === -1) return;
    const m = /^language-(.+)$/.exec(list[i]);
    if (!m) return;
    const colon = m[1].indexOf(":");
    if (colon <= 0) return;
    // `python:` (nombre vacío) se limpia igualmente: sin la dos la clase es
    // un lenguaje válido y el resaltado no se rompe.
    const filename = m[1].slice(colon + 1).trim();
    list[i] = `language-${m[1].slice(0, colon)}`;
    code.properties.className = list;
    if (filename) code.properties["data-filename"] = filename;
  }

  function walk(node, parent) {
    if (!node || node.type !== "element") return;
    if (node.tagName === "code" && parent?.tagName === "pre") split(node);
    for (const child of node.children || []) walk(child, node);
  }

  return (tree) => {
    for (const child of tree.children || []) walk(child, tree);
  };
}

const components = {
  /**
   * El lenguaje viene en el className del <code> interno, no en el <pre>,
   * así que el header con el lenguaje y el botón de copiar se arma acá
   * leyendo el hijo — y se deja pasar el <pre> original para no perder el
   * resaltado que ya aplicó rehype-highlight.
   */
  pre({ children }) {
    const child = Array.isArray(children) ? children[0] : children;
    const className = child?.props?.className || "";
    const match = /language-([\w-]+)/.exec(className);
    // `data-filename` lo deja rehypeFilename (convención lenguaje:nombre).
    const filename = child?.props?.["data-filename"] || null;
    return (
      <CodeBlock language={match?.[1]} code={textOf(child)} filename={filename}>
        <pre>{children}</pre>
      </CodeBlock>
    );
  },

  code({ className, children, ...props }) {
    // Sin className es código inline; con className viene de un fence y lo
    // envuelve el `pre` de arriba.
    if (!className) {
      return (
        <code className="px-1 py-0.5 rounded bg-glyvex-surface-code text-[0.9em] font-mono [overflow-wrap:anywhere]">
          {children}
        </code>
      );
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },

  a({ children, href }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-glyvex-accent hover:underline"
      >
        {children}
      </a>
    );
  },

  table({ children }) {
    return (
      <div className="my-2 overflow-x-auto max-w-full">
        {/* overflow-wrap normal: una tabla ancha se desplaza dentro de su
            contenedor en vez de partir cada palabra letra por letra. */}
        <table className="w-full text-sm border-collapse [overflow-wrap:normal]">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return (
      <th className="border border-glyvex-border-soft px-2 py-1 bg-glyvex-veil-disabled text-left font-medium">
        {children}
      </th>
    );
  },
  td({ children }) {
    return <td className="border border-glyvex-border-soft px-2 py-1 align-top">{children}</td>;
  },

  ul({ children }) {
    return <ul className="list-disc pl-5 my-1.5 space-y-0.5">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="list-decimal pl-5 my-1.5 space-y-0.5">{children}</ol>;
  },
  blockquote({ children }) {
    return (
      <blockquote className="border-l-2 border-glyvex-border-hi pl-3 my-2 text-glyvex-muted">
        {children}
      </blockquote>
    );
  },
  h1: ({ children }) => <h1 className="text-lg font-semibold mt-3 mb-1.5">{children}</h1>,
  h2: ({ children }) => <h2 className="text-base font-semibold mt-3 mb-1.5">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-semibold mt-2 mb-1">{children}</h3>,
  hr: () => <hr className="my-3 border-glyvex-border-soft" />,
  p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
};

/**
 * Markdown de un mensaje del asistente.
 *
 * memo importa: durante el streaming el contenido cambia en cada token y sin
 * esto se re-parsearía todo el markdown del historial en cada chunk.
 */
function MarkdownMessage({ content }) {
  return (
    <div className="text-sm break-words [overflow-wrap:anywhere] min-w-0 max-w-full">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeFilename, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={components}
      >
        {content || ""}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownMessage);

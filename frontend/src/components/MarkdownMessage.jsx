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
    return (
      <CodeBlock language={match?.[1]} code={textOf(child)}>
        <pre>{children}</pre>
      </CodeBlock>
    );
  },

  code({ className, children, ...props }) {
    // Sin className es código inline; con className viene de un fence y lo
    // envuelve el `pre` de arriba.
    if (!className) {
      return (
        <code className="px-1 py-0.5 rounded bg-black/40 text-[0.9em] font-mono">
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
      <div className="my-2 overflow-x-auto">
        <table className="w-full text-sm border-collapse">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return (
      <th className="border border-white/10 px-2 py-1 bg-black/30 text-left font-medium">
        {children}
      </th>
    );
  },
  td({ children }) {
    return <td className="border border-white/10 px-2 py-1 align-top">{children}</td>;
  },

  ul({ children }) {
    return <ul className="list-disc pl-5 my-1.5 space-y-0.5">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="list-decimal pl-5 my-1.5 space-y-0.5">{children}</ol>;
  },
  blockquote({ children }) {
    return (
      <blockquote className="border-l-2 border-white/20 pl-3 my-2 text-glyvex-muted">
        {children}
      </blockquote>
    );
  },
  h1: ({ children }) => <h1 className="text-lg font-semibold mt-3 mb-1.5">{children}</h1>,
  h2: ({ children }) => <h2 className="text-base font-semibold mt-3 mb-1.5">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-semibold mt-2 mb-1">{children}</h3>,
  hr: () => <hr className="my-3 border-white/10" />,
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
    <div className="text-sm break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={components}
      >
        {content || ""}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownMessage);

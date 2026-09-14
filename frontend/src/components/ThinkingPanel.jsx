import { useState } from "react";
import { ChevronRight } from "lucide-react";

/**
 * Panel colapsable con el contenido del bloque <think>...</think> de una
 * respuesta. Colapsado por default; se muestra arriba del contenido de la
 * respuesta del asistente cuando preserve_thinking está activo.
 */
export default function ThinkingPanel({ thinking, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);

  if (!thinking) return null;

  return (
    <div
      className="rounded-md border mb-2 overflow-hidden"
      style={{ backgroundColor: "#0d1117", borderColor: "rgba(6, 182, 212, 0.25)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left"
        style={{ color: "#06b6d4" }}
      >
        <ChevronRight size={14} className={open ? "rotate-90 transition-transform" : "transition-transform"} />
        <span>🧠 Proceso de razonamiento</span>
      </button>
      {open && (
        <pre
          className="px-3 pb-3 text-xs whitespace-pre-wrap break-words font-mono"
          style={{ color: "#9ca3af" }}
        >
          {thinking}
        </pre>
      )}
    </div>
  );
}

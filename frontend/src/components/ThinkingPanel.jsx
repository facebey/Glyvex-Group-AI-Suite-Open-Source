import { useEffect, useRef, useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { formatSeconds } from "./StreamStatus.jsx";
import { useTranslation } from "react-i18next";

/**
 * Panel colapsable con el razonamiento de una respuesta (reasoning_content o
 * bloque <think>). Colapsado por default.
 *
 * Mientras el modelo razona (`active`), el encabezado lo dice con un
 * indicador y, si el usuario lo abre, el texto sigue al último renglón.
 * Al terminar muestra cuánto duró, calculado con las métricas del mensaje.
 */
export default function ThinkingPanel({ thinking, active = false, durationSeconds = null, defaultOpen = false }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen);
  const bodyRef = useRef(null);

  useEffect(() => {
    if (open && active && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [thinking, open, active]);

  if (!thinking) return null;

  const label = active
    ? t("chat.thinking.thinking")
    : durationSeconds != null && durationSeconds >= 0.1
      ? t("chat.thinking.thoughtFor", { time: formatSeconds(durationSeconds) })
      : t("chat.thinking.reasoning");

  return (
    <div
      className="rounded-md border mb-2 overflow-hidden w-full min-w-0"
      style={{ backgroundColor: "#0d1117", borderColor: "rgba(6, 182, 212, 0.25)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left"
        style={{ color: "#06b6d4" }}
      >
        <ChevronRight size={14} className={open ? "rotate-90 transition-transform" : "transition-transform"} />
        {active && <Loader2 size={13} className="animate-spin shrink-0 motion-reduce:animate-none" />}
        <span>{label}</span>
        {!open && !active && (
          <span className="ml-auto text-xs opacity-60">{t("chat.thinking.viewReasoning")}</span>
        )}
      </button>
      {open && (
        <pre
          ref={bodyRef}
          className="px-3 pb-3 text-xs whitespace-pre-wrap break-words [overflow-wrap:anywhere] font-mono max-h-80 overflow-y-auto"
          style={{ color: "#9ca3af" }}
        >
          {thinking}
        </pre>
      )}
    </div>
  );
}

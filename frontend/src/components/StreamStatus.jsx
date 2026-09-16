import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

/**
 * Qué está haciendo el modelo mientras todavía no hay texto de respuesta.
 *
 * Antes, en esa espera el chat dibujaba la burbuja vacía (un rectángulo).
 * Las fases salen de lo que ya llega por el stream, sin eventos nuevos:
 *
 * - "prompt":    todavía no llegó ningún token (procesando el prompt).
 * - "thinking":  llegan tokens de razonamiento.
 * - "tool_prep": llegan tokens pero no son texto ni razonamiento: el modelo
 *                está escribiendo los argumentos de una tool.
 * - "tools":     hay una tool corriendo (la lista en vivo ya lo muestra).
 * - "analyzing": terminaron las tools y el modelo todavía no escribe.
 * - "writing":   ya hay texto de respuesta; no se muestra estado.
 */
export function streamPhase({ message, metrics, tools }) {
  if (message?.content && message.content.trim()) return "writing";
  const list = tools || [];
  if (list.some((entry) => entry.status === "running")) return "tools";

  const thinkingTokens = metrics?.tokens_thinking ?? 0;
  const totalTokens = metrics?.tokens_total ?? 0;

  if (list.length > 0) {
    // Tras las tools, el razonamiento de la ronda nueva también cuenta como
    // "analizando": es el modelo leyendo los resultados.
    return "analyzing";
  }
  if (message?.thinking || thinkingTokens > 0) return "thinking";
  if (totalTokens > 0) return "tool_prep";
  return "prompt";
}

const LABELS = {
  prompt: "Leyendo el mensaje",
  thinking: "Pensando",
  tool_prep: "Preparando una búsqueda",
  analyzing: "Analizando los resultados",
};

function useElapsedSeconds(resetKey) {
  const startRef = useRef(performance.now());
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    startRef.current = performance.now();
    setSeconds(0);
    const id = setInterval(() => {
      setSeconds(Math.floor((performance.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [resetKey]);

  return seconds;
}

export function formatSeconds(seconds) {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return "";
  if (seconds < 60) {
    return `${seconds.toLocaleString("es-AR", { maximumFractionDigits: seconds < 10 ? 1 : 0 })} s`;
  }
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m} min ${s.toString().padStart(2, "0")} s`;
}

/**
 * Renglón de estado. Muestra los segundos de la fase actual, no del turno:
 * "Pensando 12 s" dice cuánto lleva pensando, que es lo que uno quiere saber.
 */
export default function StreamStatus({ phase }) {
  const seconds = useElapsedSeconds(phase);
  if (!phase || phase === "writing" || phase === "tools") return null;

  const thinking = phase === "thinking" || phase === "analyzing";

  return (
    <div
      role="status"
      aria-live="polite"
      className={
        "inline-flex items-center gap-2 self-start px-3 py-2 rounded-lg text-sm " +
        (thinking ? "text-glyvex-cyan bg-glyvex-cyan/5" : "text-glyvex-muted bg-glyvex-card")
      }
    >
      <Loader2 size={14} className="animate-spin shrink-0 motion-reduce:animate-none" />
      <span>{LABELS[phase]}</span>
      {seconds >= 1 && <span className="tabular-nums opacity-70">{seconds} s</span>}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";

/** Margen en px para considerar que estás "abajo de todo". */
const BOTTOM_THRESHOLD = 80;

/**
 * Autoscroll que se corre cuando el usuario toma el control.
 *
 * El problema del scroll automático simple es que si leés algo de más arriba
 * mientras el modelo genera, cada token te tira de vuelta al final. Acá el
 * autoscroll se apaga apenas te alejás del fondo y se vuelve a prender solo
 * cuando volvés (o cuando apretás el botón flotante).
 */
export function useAutoScroll({ dependency, streaming }) {
  const containerRef = useRef(null);
  const bottomRef = useRef(null);
  const pinnedRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const isNearBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD;
  }, []);

  const scrollToBottom = useCallback((behavior = "smooth") => {
    pinnedRef.current = true;
    setShowJump(false);
    bottomRef.current?.scrollIntoView({ behavior });
  }, []);

  const handleScroll = useCallback(() => {
    const near = isNearBottom();
    pinnedRef.current = near;
    setShowJump(!near);
  }, [isNearBottom]);

  useEffect(() => {
    if (pinnedRef.current) {
      // Durante el streaming el scroll suave no llega a terminar antes del
      // token siguiente y queda entrecortado; "auto" es instantáneo.
      bottomRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth" });
    }
  }, [dependency, streaming]);

  // Al terminar de generar, si el usuario nunca se movió, se asegura el final.
  useEffect(() => {
    if (!streaming && pinnedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [streaming]);

  return { containerRef, bottomRef, showJump, scrollToBottom, handleScroll };
}

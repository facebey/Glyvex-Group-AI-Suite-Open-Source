import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import {
  NETWORK_ERROR,
  clear,
  getState,
  getSnapshot,
  setUiError,
  start,
  stop,
  subscribe,
} from "../lib/chatStreamStore.js";
import i18n from "../lib/i18n.js";

/**
 * useChatStream — binding React del stream del chat.
 *
 * La generación vive en chatStreamStore (nivel de módulo): cambiar de
 * módulo no la interrumpe. Este hook solo traduce:
 * - sincroniza el contenido acumulado al nodo (idempotente, asigna el
 *   texto completo: al montar recupera lo que llegó mientras no estaba);
 * - al terminar, compromete el payload final y dispara onFinish.
 */
export function useChatStream({ nodes, setNodes, onFinish, onError }) {
  const snap = useSyncExternalStore(subscribe, getSnapshot);
  const cbRef = useRef({ onFinish, onError });
  cbRef.current = { onFinish, onError };
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;

  /** Asigna el contenido acumulado al nodo asistente (idempotente). */
  const sync = useCallback(() => {
    const s = getState();
    if (!s.assistantId) return;
    setNodes((prev) =>
      prev.map((m) =>
        m.id === s.assistantId
          ? {
              ...m,
              content: s.content,
              ...(s.thinking ? { thinking: s.thinking } : {}),
            }
          : m
      )
    );
  }, [setNodes]);

  useEffect(() => {
    sync();
  }, [snap, sync]);

  // Compromiso al terminar: payload "done" + persistencia, una sola vez.
  useEffect(() => {
    if (!snap.finished || !snap.assistantId) return;
    if (!nodesRef.current.some((m) => m.id === snap.assistantId)) return;
    const s = getState();
    if (s.done) {
      const event = s.done;
      setNodes((prev) =>
        prev.map((m) => {
          if (m.id !== s.assistantId) return m;
          // Al continuar se acumulan los intercambios de tools de las
          // dos pasadas en vez de pisar los de la primera.
          const priorExchange = s.continuation ? m.tool_exchange || [] : [];
          const priorActivity = s.continuation ? m.tool_activity || [] : [];
          const exchange = [...priorExchange, ...(event.tool_messages || [])];
          const activity = [...priorActivity, ...(event.tool_activity || [])];
          return {
            ...m,
            finish_reason: event.finish_reason,
            tool_exchange: exchange.length ? exchange : undefined,
            tool_activity: activity.length ? activity : undefined,
            metrics: {
              tps: event.tps,
              pp_tps: event.pp_tps,
              ttft_ms: event.ttft_ms,
              ttft_answer_ms: event.ttft_answer_ms,
              tokens_total: event.tokens_total ?? event.total_tokens,
              tokens_thinking: event.tokens_thinking,
              context_tokens: event.context_tokens,
              metrics_source: event.metrics_source,
              duration_s: event.duration_s,
            },
          };
        })
      );
    }
    if (s.finished === "failed" && s.error === NETWORK_ERROR) {
      const message = i18n.t("chatErrors.connection");
      cbRef.current.onError?.(message);
    }
    clear();
    cbRef.current.onFinish?.();
  }, [snap, setNodes]);

  const run = useCallback(
    async ({ request, assistantId, continuation = false }) => {
      setUiError(null);
      await start({ request, assistantId, continuation });
    },
    []
  );

  const stopStream = useCallback(() => stop(), []);
  const discard = useCallback(() => clear(), []);
  const setError = useCallback((message) => setUiError(message), []);

  return {
    streaming: snap.streaming,
    liveMetrics: snap.liveMetrics,
    toolActivity: snap.toolActivity,
    error: snap.error === NETWORK_ERROR ? i18n.t("chatErrors.connection") : snap.error,
    active: snap.assistantId !== null,
    setError,
    run,
    stop: stopStream,
    discard,
    sync,
  };
}

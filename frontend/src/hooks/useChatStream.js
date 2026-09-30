import { useCallback, useRef, useState } from "react";
import { consumeSSE } from "../lib/sse.js";
import i18n from "../lib/i18n.js";

/** Refresco de métricas: ~12 fps. Suficiente para leerse "en vivo" sin
 *  disparar un re-render extra por cada token del stream. */
const METRICS_FLUSH_MS = 80;

/** Tokens: se acumulan en un buffer y se vuelcan cada 60 ms (D3). Un
 *  setNodes por token a 60+ t/s disparaba un re-render por token. */
const TOKEN_FLUSH_MS = 60;

/**
 * Una generación contra el endpoint, volcada en un nodo del árbol.
 *
 * Lo usan los cuatro caminos del chat — enviar, editar y reenviar, regenerar
 * y continuar — porque salvo qué mensajes se mandan y en qué nodo se
 * escribe, todo lo demás (métricas, tools, razonamiento, aborto) es idéntico.
 */
export function useChatStream({ setNodes, onFinish, onError }) {
  const [streaming, setStreaming] = useState(false);
  const [liveMetrics, setLiveMetrics] = useState(null);
  const [toolActivity, setToolActivity] = useState([]);
  const [error, setError] = useState(null);

  const abortRef = useRef(null);
  const metricsRef = useRef(null);
  const flushRef = useRef(0);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const run = useCallback(
    async ({ request, assistantId, continuation = false }) => {
      setError(null);
      setStreaming(true);
      setLiveMetrics(null);
      setToolActivity([]);
      metricsRef.current = null;
      flushRef.current = 0;

      const controller = new AbortController();
      abortRef.current = controller;

      let pendingContent = "";
      let pendingThinking = "";
      let flushTimer = 0;
      const flushPending = () => {
        flushTimer = 0;
        const content = pendingContent;
        const thinking = pendingThinking;
        pendingContent = "";
        pendingThinking = "";
        if (!content && !thinking) return;
        setNodes((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  ...(content ? { content: (m.content || "") + content } : {}),
                  ...(thinking ? { thinking: (m.thinking || "") + thinking } : {}),
                }
              : m
          )
        );
      };
      const scheduleFlush = () => {
        if (!flushTimer) flushTimer = setTimeout(flushPending, TOKEN_FLUSH_MS);
      };

      try {
        const res = await fetch("/api/chat/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({ ...request, continuation }),
        });

        if (!res.ok || !res.body) throw new Error(`El servidor respondió ${res.status}`);

        await consumeSSE(res, controller.signal, (event) => {
          if (event.type === "token") {
            pendingContent += event.content;
            scheduleFlush();
          } else if (event.type === "thinking_token") {
            pendingThinking += event.content;
            scheduleFlush();
          } else if (event.type === "metrics") {
            metricsRef.current = event;
            const t = performance.now();
            if (t - flushRef.current >= METRICS_FLUSH_MS) {
              flushRef.current = t;
              setLiveMetrics(event);
            }
          } else if (event.type === "tool_call") {
            setToolActivity((prev) => [
              ...prev,
              {
                id: event.id,
                name: event.name,
                arguments: event.arguments,
                status: "running",
                ok: null,
                summary: "",
                sources: [],
              },
            ]);
          } else if (event.type === "tool_result") {
            setToolActivity((prev) =>
              prev.map((entry) =>
                entry.id === event.id
                  ? {
                      ...entry,
                      status: "done",
                      ok: event.ok,
                      summary: event.summary,
                      sources: event.sources,
                    }
                  : entry
              )
            );
          } else if (event.type === "done") {
            // Vaciar el buffer antes del update final: los tokens restantes
            // entran en el mismo nodo que los métricas de cierre.
            flushPending();
            metricsRef.current = event;
            setLiveMetrics(event);
            setNodes((prev) =>
              prev.map((m) => {
                if (m.id !== assistantId) return m;
                // Al continuar se acumulan los intercambios de tools de las
                // dos pasadas en vez de pisar los de la primera.
                const priorExchange = continuation ? m.tool_exchange || [] : [];
                const priorActivity = continuation ? m.tool_activity || [] : [];
                const exchange = [...priorExchange, ...(event.tool_messages || [])];
                const activity = [...priorActivity, ...(event.tool_activity || [])];
                return {
                  ...m,
                  finish_reason: event.finish_reason,
                  tool_exchange: exchange.length ? exchange : undefined,
                  tool_activity: activity.length ? activity : undefined,
                  // context_tokens se guarda con el mensaje: el próximo
                  // /estimate lo usa como base medida en vez de re-tokenizar
                  // el historial (ver Chat.jsx).
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
          } else if (event.type === "error") {
            setError(event.message);
          }
        });
      } catch (err) {
        if (err.name !== "AbortError") {
          const message = i18n.t("chatErrors.connection");
          setError(message);
          onError?.(message);
        }
      } finally {
        // Error o aborto: no perder los tokens que quedaron en el buffer.
        clearTimeout(flushTimer);
        flushPending();
        if (metricsRef.current) setLiveMetrics(metricsRef.current);
        setToolActivity([]);
        setStreaming(false);
        abortRef.current = null;
        onFinish?.();
      }
    },
    [setNodes, onFinish, onError]
  );

  return { streaming, liveMetrics, toolActivity, error, setError, run, stop };
}

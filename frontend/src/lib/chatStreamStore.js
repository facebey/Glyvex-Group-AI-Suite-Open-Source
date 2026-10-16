import { consumeSSE } from "./sse.js";

/**
 * chatStreamStore.js — La generación vive FUERA de React.
 *
 * Antes el stream era un fetch dentro del componente Chat: al cambiar de
 * módulo el componente se desmontaba, abortaba el fetch y el backend
 * cancelaba la generación. Aquí el loop fetch+SSE vive a nivel de módulo:
 * navegar no lo mata. El componente montado se sincroniza (idempotente,
 * asigna el contenido acumulado al nodo); al volver, el mismo efecto se
 * ejecuta en el mount y recupera lo que llegó mientras no estaba.
 */

const TOKEN_FLUSH_MS = 60;
const METRICS_FLUSH_MS = 80;
export const NETWORK_ERROR = "__network__";

function idleState() {
  return {
    streaming: false,
    finished: null, // "done" | "aborted" | "failed" al terminar
    error: null,
    assistantId: null,
    continuation: false,
    content: "",
    thinking: "",
    liveMetrics: null,
    toolActivity: [],
    done: null,
  };
}

let state = idleState();
let snapshot = { ...state };
let controller = null;
let flushTimer = 0;
let metricsLastEmit = 0;
let pendingContent = "";
let pendingThinking = "";
const listeners = new Set();

function emit() {
  snapshot = { ...state };
  listeners.forEach((l) => l());
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getSnapshot() {
  return snapshot;
}

export function getState() {
  return state;
}

/** Error puntual de UI (no de stream): null lo limpia. */
export function setUiError(message) {
  state.error = message;
  emit();
}

/** Aborta (si hay) y vuelve a cero. Se usa tras comprometer o al resetear. */
export function clear() {
  controller?.abort();
  clearTimeout(flushTimer);
  pendingContent = "";
  pendingThinking = "";
  state = idleState();
  emit();
}

export function stop() {
  controller?.abort();
}

function flush() {
  flushTimer = 0;
  const content = pendingContent;
  const thinking = pendingThinking;
  pendingContent = "";
  pendingThinking = "";
  if (!content && !thinking) return;
  state.content += content;
  state.thinking += thinking;
  emit();
}

function scheduleFlush() {
  if (!flushTimer) flushTimer = setTimeout(flush, TOKEN_FLUSH_MS);
}

function handleEvent(event) {
  if (event.type === "token") {
    pendingContent += event.content;
    scheduleFlush();
  } else if (event.type === "thinking_token") {
    pendingThinking += event.content;
    scheduleFlush();
  } else if (event.type === "metrics") {
    state.liveMetrics = event;
    const t = performance.now();
    if (t - metricsLastEmit >= METRICS_FLUSH_MS) {
      metricsLastEmit = t;
      emit();
    }
  } else if (event.type === "tool_call") {
    state.toolActivity = [
      ...state.toolActivity,
      {
        id: event.id,
        name: event.name,
        arguments: event.arguments,
        status: "running",
        ok: null,
        summary: "",
        sources: [],
      },
    ];
    emit();
  } else if (event.type === "tool_result") {
    state.toolActivity = state.toolActivity.map((entry) =>
      entry.id === event.id
        ? {
            ...entry,
            status: "done",
            ok: event.ok,
            summary: event.summary,
            sources: event.sources,
          }
        : entry
    );
    emit();
  } else if (event.type === "done") {
    flush();
    state.done = event;
    state.liveMetrics = event;
    emit();
  } else if (event.type === "error") {
    state.error = event.message;
    emit();
  }
}

/** Inicia una generación. Ignorada si ya hay una en vuelo. */
export async function start({ request, assistantId, continuation = false }) {
  if (state.streaming) return;
  controller = new AbortController();
  state = { ...idleState(), streaming: true, assistantId, continuation };
  const self = state;
  pendingContent = "";
  pendingThinking = "";
  flushTimer = 0;
  metricsLastEmit = 0;
  emit();
  try {
    const res = await fetch("/api/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ ...request, continuation }),
    });
    if (!res.ok || !res.body) throw new Error(`El servidor respondió ${res.status}`);
    await consumeSSE(res, controller.signal, handleEvent);
  } catch (err) {
    if (err.name !== "AbortError") state.error = NETWORK_ERROR;
  } finally {
    if (state !== self) return; // clear() durante el stream: no tocar el estado nuevo
    clearTimeout(flushTimer);
    flush();
    state.streaming = false;
    if (!state.finished) {
      state.finished = state.error ? "failed" : state.done ? "done" : "aborted";
    }
    controller = null;
    emit();
  }
}

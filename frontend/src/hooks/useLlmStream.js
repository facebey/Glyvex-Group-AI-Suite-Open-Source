import { useEffect, useRef, useState } from "react";

function wsUrlFor(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

// Backoff de reconexión: 1 s, 2 s, 4 s… hasta 15 s. Se reinicia en cada
// conexión exitosa; si el proceso murió no tiene sentido reintentar más
// rápido de lo que el backend va a volver a tenerlo.
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

/**
 * Cliente compartido del WS /api/llm-metrics/{id}/stream.
 *
 * Lo usan la tira de vitales del Launcher y el Monitor en vivo: ambos
 * querían lo mismo (buffer inicial por /history + snaps por WS), y con la
 * reconexión con backoff un corte de conexión no deja los datos obsoletos
 * para siempre. `connected` se expone para que la UI avise si el stream
 * está caído.
 *
 * Los callbacks van por ref: cambiar de callback no debe tumbar el socket.
 */
export function useLlmStream({ processId, active = true, onSnap, onHistory }) {
  const [connected, setConnected] = useState(false);

  const snapRef = useRef(onSnap);
  snapRef.current = onSnap;
  const historyRef = useRef(onHistory);
  historyRef.current = onHistory;

  useEffect(() => {
    if (!processId || !active) return undefined;
    let cancelled = false;
    let ws = null;
    let retry = null;
    let attempt = 0;

    fetch(`/api/llm-metrics/${processId}/history`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (!cancelled && Array.isArray(data)) historyRef.current?.(data);
      })
      .catch(() => {});

    const connect = () => {
      ws = new WebSocket(wsUrlFor(`/api/llm-metrics/${processId}/stream`));
      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      ws.onmessage = (event) => {
        let snap;
        try { snap = JSON.parse(event.data); } catch { return; }
        if (snap.error) return;
        snapRef.current?.(snap);
      };
      ws.onerror = () => {};
      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
        attempt += 1;
        retry = setTimeout(connect, delay);
      };
    };
    connect();

    return () => {
      cancelled = true;
      clearTimeout(retry);
      try { ws?.close(); } catch { /* ya cerrado */ }
      setConnected(false);
    };
  }, [processId, active]);

  return { connected };
}

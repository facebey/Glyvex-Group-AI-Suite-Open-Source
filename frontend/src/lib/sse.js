/**
 * sse.js — Lectura del stream de /api/chat/completions.
 *
 * Se usa fetch + ReadableStream y no EventSource porque el endpoint es POST
 * y hace falta poder cancelarlo con AbortController.
 */

/** Lee el body SSE de fetch como stream y llama onEvent por cada evento. */
export async function consumeSSE(response, signal, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    if (signal.aborted) {
      await reader.cancel().catch(() => {});
      return;
    }
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          onEvent(JSON.parse(data));
        } catch {
          // línea malformada — se ignora
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

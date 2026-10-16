import { afterEach, describe, expect, it, vi } from "vitest";
import {
  NETWORK_ERROR,
  clear,
  getSnapshot,
  getState,
  start,
  stop,
  subscribe,
} from "./chatStreamStore.js";

/**
 * Body SSE fake. Con `holdAfter: n` se queda bloqueado tras el chunk n hasta
 * que el test llame a globalThis.__release(); como un body real de fetch,
 * si la señal aborta el stream entra en error y el read pendiente rechaza.
 */
function sseBody(events, signal, { holdAfter = -1 } = {}) {
  const enc = new TextEncoder();
  const chunks = events.map((e) => enc.encode(`data: ${JSON.stringify(e)}\n\n`));
  chunks.push(enc.encode("data: [DONE]\n\n"));
  return new ReadableStream({
    async start(controller) {
      const onAbort = () => {
        try {
          controller.error(new DOMException("Aborted", "AbortError"));
        } catch {
          // ya cerrado
        }
      };
      signal?.addEventListener("abort", onAbort);
      for (let i = 0; i < chunks.length; i++) {
        controller.enqueue(chunks[i]);
        if (i === holdAfter) {
          await new Promise((resolve) => {
            globalThis.__release = () => {
              resolve();
              try {
                for (let j = i + 1; j < chunks.length; j++) controller.enqueue(chunks[j]);
                controller.close();
              } catch {
                // ya cancelado
              }
            };
          });
          return;
        }
      }
      controller.close();
    },
  });
}

function mockFetch(buildBody) {
  return vi.fn(async (url, opts) => {
    if (buildBody instanceof Error) throw buildBody;
    return { ok: true, status: 200, body: buildBody(opts?.signal) };
  });
}

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

afterEach(() => {
  clear();
  vi.unstubAllGlobals();
  delete globalThis.__release;
});

describe("chatStreamStore", () => {
  it("acumula tokens y termina en done", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(() =>
        sseBody([
          { type: "token", content: "Hola" },
          { type: "token", content: " mundo" },
          { type: "metrics", tps: 12 },
          {
            type: "done",
            finish_reason: "stop",
            tps: 12,
            tokens_total: 10,
            context_tokens: 100,
          },
        ])
      )
    );

    await start({ request: {}, assistantId: "a1" });
    const s = getState();
    expect(s.streaming).toBe(false);
    expect(s.finished).toBe("done");
    expect(s.content).toBe("Hola mundo");
    expect(s.done.finish_reason).toBe("stop");
  });

  it("stop() interrumpe y termina en aborted conservando lo flushed", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((signal) =>
        sseBody(
          [
            { type: "token", content: "parcial" },
            { type: "token", content: " que no llega" },
            { type: "done", finish_reason: "stop" },
          ],
          signal,
          { holdAfter: 0 }
        )
      )
    );

    const p = start({ request: {}, assistantId: "a1" });
    await tick();
    await tick();
    expect(getState().streaming).toBe(true);
    stop();
    await p;
    const s = getState();
    expect(s.streaming).toBe(false);
    expect(s.finished).toBe("aborted");
    expect(s.content).toBe("parcial");
    globalThis.__release?.();
  });

  it("falla de red termina en failed con NETWORK_ERROR", async () => {
    vi.stubGlobal("fetch", mockFetch(new TypeError("fetch failed")));
    await start({ request: {}, assistantId: "a1" });
    const s = getState();
    expect(s.finished).toBe("failed");
    expect(s.error).toBe(NETWORK_ERROR);
  });

  it("evento error del backend termina en failed con el mensaje", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(() => sseBody([{ type: "error", message: "Modelo no en ejecución" }]))
    );
    await start({ request: {}, assistantId: "a1" });
    const s = getState();
    expect(s.finished).toBe("failed");
    expect(s.error).toBe("Modelo no en ejecución");
  });

  it("clear() durante el stream no ensucia el estado siguiente", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((signal) =>
        sseBody(
          [{ type: "token", content: "x" }, { type: "done", finish_reason: "stop" }],
          signal,
          { holdAfter: 0 }
        )
      )
    );
    const p = start({ request: {}, assistantId: "a1" });
    await tick();
    clear();
    await p;
    const s = getState();
    expect(s.streaming).toBe(false);
    expect(s.assistantId).toBeNull();
    expect(s.content).toBe("");
    globalThis.__release?.();

    vi.stubGlobal(
      "fetch",
      mockFetch(() =>
        sseBody([{ type: "token", content: "nuevo" }, { type: "done", finish_reason: "stop" }])
      )
    );
    await start({ request: {}, assistantId: "a2" });
    const s2 = getState();
    expect(s2.assistantId).toBe("a2");
    expect(s2.content).toBe("nuevo");
    expect(s2.finished).toBe("done");
  });

  it("emite a los suscriptores al terminar", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(() => sseBody([{ type: "done", finish_reason: "stop" }]))
    );
    const seen = [];
    const unsub = subscribe(() => seen.push(getSnapshot().finished));
    await start({ request: {}, assistantId: "a1" });
    unsub();
    expect(seen).toContain("done");
  });

  it("ignora un start mientras hay uno en vuelo", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((signal) =>
        sseBody([{ type: "token", content: "x" }], signal, { holdAfter: 0 })
      )
    );
    const p = start({ request: {}, assistantId: "a1" });
    await tick();
    const second = mockFetch(() =>
      sseBody([{ type: "done", finish_reason: "stop" }])
    );
    vi.stubGlobal("fetch", second);
    await start({ request: {}, assistantId: "a2" });
    expect(second).not.toHaveBeenCalled();
    stop();
    await p;
    expect(getState().assistantId).toBe("a1");
    globalThis.__release?.();
  });
});

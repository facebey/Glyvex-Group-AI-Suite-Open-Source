import { describe, expect, it } from "vitest";

import { toPayloadContent, toPayloadMessages } from "./payload.js";

describe("toPayloadContent", () => {
  it("sin adjuntos usa el contenido tal cual", () => {
    expect(toPayloadContent({ role: "user", content: "hola" })).toBe("hola");
  });

  it("las imágenes viajan como image_ref, el texto como bloques", () => {
    const content = toPayloadContent({
      role: "user",
      content: "mira",
      attachments: [
        { id: "a1", kind: "image", filename: "x.png", status: "ready" },
        { id: "a2", kind: "text", filename: "y.txt", text: "contenido", status: "ready" },
        { id: "a3", kind: "image", filename: "z.png", status: "vision_unsupported" },
      ],
    });
    expect(content).toEqual([
      { type: "text", text: "mira" },
      { type: "text", text: "--- archivo: y.txt ---\ncontenido" },
      { type: "image_ref", id: "a1", filename: "x.png" },
    ]);
  });
});

describe("toPayloadMessages", () => {
  it("un assistant que falló sin producir texto no porta contexto", () => {
    const failed = {
      role: "assistant",
      content: "",
      error: "Upstream 400: la imagen no se pudo leer",
      metrics: null,
    };
    expect(toPayloadMessages(failed)).toEqual([]);
  });

  it("un assistant falló PERO dejó texto parcial sí se manda", () => {
    const partial = { role: "assistant", content: "media respuesta", error: "Upstream 500" };
    const out = toPayloadMessages(partial);
    expect(out).toHaveLength(1);
    expect(out[0].content).toBe("media respuesta");
  });

  it("un turno con tool_exchange se conserva aunque el texto final esté vacío", () => {
    const toolTurn = {
      role: "assistant",
      content: "",
      error: "Upstream 500",
      tool_exchange: [{ role: "tool", tool_call_id: "t1", content: "resultado" }],
    };
    const out = toPayloadMessages(toolTurn);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("tool");
  });

  it("un assistant normal y los mensajes del usuario no se filtran", () => {
    expect(toPayloadMessages({ role: "assistant", content: "ok" })).toHaveLength(1);
    expect(toPayloadMessages({ role: "user", content: "hola", error: "irrelevante" })).toHaveLength(1);
  });
});

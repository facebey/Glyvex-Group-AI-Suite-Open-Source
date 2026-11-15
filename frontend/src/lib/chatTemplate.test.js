import { describe, expect, it } from "vitest";

import {
  confirmedTemplateFlags,
  embeddedTemplate,
  flagState,
  templateHighlightSegments,
} from "./chatTemplate.js";

const QWEN_LIKE =
  "{%- if tools %}# Tools\n{%- for tool in tools %}<tool_call>{{ tool }}" +
  "</tool_call>{%- endfor %}{%- endif %}{%- if enable_thinking %}<|think|>" +
  "{%- if reasoning_effort %}<effort>{{ reasoning_effort }}</effort>{% endif %}{% endif %}";

const ALL_TRUE = {
  chat_template: QWEN_LIKE,
  enable_thinking_kwarg: true,
  preserve_thinking_kwarg: false,
  reasoning_effort_kwarg: true,
  tools_in_template: true,
};

describe("flagState", () => {
  it("sin metadata o sin la clave no se afirma nada", () => {
    expect(flagState(null, "tools_in_template")).toBe(null);
    expect(flagState({}, "tools_in_template")).toBe(null);
    expect(flagState({ chat_template: "x" }, "reasoning_effort_kwarg")).toBe(null);
  });

  it("solo booleanos explícitos son una afirmación", () => {
    expect(flagState(ALL_TRUE, "tools_in_template")).toBe(true);
    expect(flagState(ALL_TRUE, "preserve_thinking_kwarg")).toBe(false);
  });
});

describe("confirmedTemplateFlags", () => {
  it("lista solo las banderas confirmadas en true", () => {
    expect(confirmedTemplateFlags(ALL_TRUE)).toEqual([
      "enable_thinking_kwarg",
      "reasoning_effort_kwarg",
      "tools_in_template",
    ]);
  });

  it("sin datos medidos la lista es vacía", () => {
    expect(confirmedTemplateFlags(null)).toEqual([]);
  });
});

describe("embeddedTemplate", () => {
  it("devuelve el template no vacío", () => {
    expect(embeddedTemplate(ALL_TRUE)).toBe(QWEN_LIKE);
  });

  it("sin template (modelo base) devuelve null", () => {
    expect(embeddedTemplate({ chat_template: null })).toBe(null);
    expect(embeddedTemplate({ chat_template: "" })).toBe(null);
    expect(embeddedTemplate(null)).toBe(null);
  });
});

describe("templateHighlightSegments", () => {
  it("resalta solo los literales de banderas confirmadas", () => {
    const segments = templateHighlightSegments(QWEN_LIKE, ALL_TRUE);
    const highlighted = segments.filter((s) => s.hl).map((s) => s.text);
    expect(highlighted).toContain("tools");
    expect(highlighted).toContain("tool_call");
    expect(highlighted).toContain("enable_thinking");
    expect(highlighted).toContain("reasoning_effort");
    // preserve_thinking está en false y no debe aparecer resaltado.
    expect(highlighted).not.toContain("preserve_thinking");
    // Reconstrucción fiel: la concatenación es el template original.
    expect(segments.map((s) => s.text).join("")).toBe(QWEN_LIKE);
  });

  it("kwargs se resaltan case-sensitive y tools case-insensitive", () => {
    const meta = { ...ALL_TRUE, preserve_thinking_kwarg: false };
    const segments = templateHighlightSegments("Enable_thinking y Tools", meta);
    // "Enable_thinking" con E mayúscula NO es la variable jinja: no se resalta.
    expect(segments.some((s) => s.hl && s.text === "Enable_thinking")).toBe(false);
    expect(segments.some((s) => s.hl && s.text === "Tools")).toBe(true);
  });

  it("sin banderas confirmadas el template queda sin resaltar", () => {
    const segments = templateHighlightSegments(QWEN_LIKE, { enable_thinking_kwarg: false });
    expect(segments).toEqual([{ text: QWEN_LIKE, hl: false }]);
  });

  it("template vacío devuelve lista vacía", () => {
    expect(templateHighlightSegments(null, ALL_TRUE)).toEqual([]);
  });
});

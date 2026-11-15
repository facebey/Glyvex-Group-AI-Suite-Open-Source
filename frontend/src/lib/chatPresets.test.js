import { describe, expect, it } from "vitest";

import {
  PRESET_AUTO,
  PRESET_CUSTOM,
  normalizePresetChoice,
  presetSaveScope,
  resolvePresetEntry,
} from "./chatPresets.js";

const options = [
  { id: "gemma4", name: null, family: "gemma4", model_name: null, builtin: true, recommended: true, params: { temperature: 1.0 } },
  { id: "mi-gemma", name: "mi-gemma", family: "gemma4", model_name: null, builtin: false, recommended: false, params: { temperature: 0.9 } },
  { id: "por-nombre", name: "por-nombre", family: null, model_name: "llama-3.1-8b-it", builtin: false, recommended: false, params: { temperature: 0.2 } },
];

describe("resolvePresetEntry", () => {
  it("auto usa solo el recomendado de la lista", () => {
    expect(resolvePresetEntry(options, PRESET_AUTO)?.id).toBe("gemma4");
    expect(resolvePresetEntry([], PRESET_AUTO)).toBe(null);
    const sinRecomendado = options.map((p) => ({ ...p, recommended: false }));
    expect(resolvePresetEntry(sinRecomendado, PRESET_AUTO)).toBe(null);
  });

  it("custom no aplica nada y una id concreta apunta a su entrada", () => {
    expect(resolvePresetEntry(options, PRESET_CUSTOM)).toBe(null);
    expect(resolvePresetEntry(options, "mi-gemma")?.id).toBe("mi-gemma");
    expect(resolvePresetEntry(options, "borrado")).toBe(null);
  });
});

describe("normalizePresetChoice", () => {
  it("las sentinelas se conservan y una elección muerta cae a auto", () => {
    expect(normalizePresetChoice(PRESET_AUTO, options)).toBe(PRESET_AUTO);
    expect(normalizePresetChoice(PRESET_CUSTOM, options)).toBe(PRESET_CUSTOM);
    expect(normalizePresetChoice("mi-gemma", options)).toBe("mi-gemma");
    expect(normalizePresetChoice("mi-gemma", [])).toBe(PRESET_AUTO);
  });
});

describe("presetSaveScope", () => {
  it("hereda el alcance de la entrada del preset actual", () => {
    expect(presetSaveScope(options[0], "Gemma4-26B")).toEqual({ family: "gemma4", model_name: null });
    expect(presetSaveScope(options[2], "llama-3.1-8b-it")).toEqual({ family: null, model_name: "llama-3.1-8b-it" });
  });

  it("sin entrada (custom/auto sin match) se ancla al modelo actual", () => {
    expect(presetSaveScope(null, "test-model")).toEqual({ family: null, model_name: "test-model" });
    expect(presetSaveScope(null, "")).toEqual({ family: null, model_name: null });
  });
});

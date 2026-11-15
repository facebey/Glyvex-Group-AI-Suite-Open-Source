import { describe, expect, it } from "vitest";

import { effortUnsupported } from "./chatReasoning.js";

describe("effortUnsupported", () => {
  it("sin capacidades medidas no se afirma nada", () => {
    expect(effortUnsupported(null)).toBe(false);
    expect(effortUnsupported(undefined)).toBe(false);
    // Un objeto sin la bandera (endpoint no-llama, probe parcial): no es un
    // false comprobado, así que la UI no grisea.
    expect(effortUnsupported({ source: "models" })).toBe(false);
  });

  it("solo un false explícito grisea el effort", () => {
    expect(effortUnsupported({ reasoning_effort: false })).toBe(true);
    expect(effortUnsupported({ reasoning_effort: true })).toBe(false);
  });
});

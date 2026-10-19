import { describe, expect, it } from "vitest";
import { sizeDeltaMb, formatDeltaMb } from "./runtimeSize.js";

describe("sizeDeltaMb", () => {
  it("reemplazo (default): delta = nueva − anterior", () => {
    expect(sizeDeltaMb(549, 710.8)).toBe(-162);
  });

  it("tamaños iguales → 0", () => {
    expect(sizeDeltaMb(549, 549)).toBe(0);
  });

  it("tamaño previo desconocido → null (la UI oculta el delta)", () => {
    expect(sizeDeltaMb(549, null)).toBe(null);
    expect(sizeDeltaMb(549, undefined)).toBe(null);
    expect(sizeDeltaMb(549, "—")).toBe(null);
  });

  it("conservar build anterior: delta = +tamaño de la nueva (la vieja no se borra)", () => {
    expect(sizeDeltaMb(549, 710.8, true)).toBe(549);
  });

  it("conservar: no depende del tamaño previo (puede ser desconocido)", () => {
    expect(sizeDeltaMb(549, null, true)).toBe(549);
  });
});

describe("formatDeltaMb", () => {
  it("positivo lleva prefijo +", () => {
    expect(formatDeltaMb(549)).toBe("+549");
  });

  it("negativo y cero sin prefijo extra", () => {
    expect(formatDeltaMb(-162)).toBe("-162");
    expect(formatDeltaMb(0)).toBe("0");
  });

  it("null/undefined → null (se oculta)", () => {
    expect(formatDeltaMb(null)).toBe(null);
    expect(formatDeltaMb(undefined)).toBe(null);
  });
});

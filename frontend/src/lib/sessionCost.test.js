import { describe, expect, it } from "vitest";
import { cloudCost, formatMoney, formatTokens } from "./sessionCost.js";

describe("cloudCost", () => {
  it("cuenta entrada fresca + salida, cache a tarifa de lectura", () => {
    const usage = { prompt_tokens: 1_000_000, completion_tokens: 1_000_000, cached_tokens: 500_000 };
    const provider = { input_per_mtok: 2, output_per_mtok: 8, cache_read_per_mtok: 0.5 };
    // 500k frescos × $2/M + 500k cache × $0.5/M + 1M salida × $8/M = 1 + 0.25 + 8
    expect(cloudCost(usage, provider)).toBeCloseTo(9.25);
  });

  it("cache_read null/ausente: los cacheados se facturan como entrada", () => {
    const usage = { prompt_tokens: 1_000_000, completion_tokens: 0, cached_tokens: 1_000_000 };
    expect(cloudCost(usage, { input_per_mtok: 2, output_per_mtok: 8, cache_read_per_mtok: null }))
      .toBeCloseTo(2);
    expect(cloudCost(usage, { input_per_mtok: 2, output_per_mtok: 8 }))
      .toBeCloseTo(2);
  });

  it("cached > prompt: clamp defensivo, sin entrada negativa", () => {
    const usage = { prompt_tokens: 100, completion_tokens: 0, cached_tokens: 999 };
    const provider = { input_per_mtok: 2, output_per_mtok: 8, cache_read_per_mtok: 0.5 };
    // Todo el prompt va a la tarifa de cache: 100 × 0.5 / 1M
    expect(cloudCost(usage, provider)).toBeCloseTo(100 * 0.5 / 1_000_000);
  });

  it("sin usage ni proveedor: cero", () => {
    expect(cloudCost(undefined, undefined)).toBe(0);
    expect(cloudCost({}, {})).toBe(0);
  });
});

describe("formatTokens", () => {
  it("escalas", () => {
    expect(formatTokens(42)).toBe("42");
    expect(formatTokens(1234)).toBe("1.2 k");
    expect(formatTokens(1_234_567)).toBe("1.23 M");
  });
  it("cero y basura", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(undefined)).toBe("0");
  });
});

describe("formatMoney", () => {
  it("dólar por defecto", () => {
    expect(formatMoney(12.345)).toBe("$12.35");
    expect(formatMoney(0.0004)).toBe("$0.0004");
    expect(formatMoney(0)).toBe("$0.00");
  });
  it("otras monedas con prefijo", () => {
    expect(formatMoney(1.5, "EUR")).toBe("EUR 1.50");
  });
});

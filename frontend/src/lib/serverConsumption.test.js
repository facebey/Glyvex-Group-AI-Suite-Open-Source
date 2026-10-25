import { describe, expect, it } from "vitest";
import { lifetimeModelRows, lifetimeSplitRows, totalTokens, usageForPricing } from "./serverConsumption.js";
import { cloudCost } from "./sessionCost.js";

describe("usageForPricing", () => {
  it("mapea los totales absolutos al formato OpenAI (prompt incluye el caché)", () => {
    const usage = usageForPricing({ prompt_tokens: 100, cached_tokens: 900, predicted_tokens: 500 });
    expect(usage).toEqual({ prompt_tokens: 1000, completion_tokens: 500, cached_tokens: 900 });
  });

  it("con la tabla de precios factura fresco, caché y salida por separado", () => {
    const data = { prompt_tokens: 200_000, cached_tokens: 800_000, predicted_tokens: 100_000 };
    const provider = { input_per_mtok: 2, output_per_mtok: 8, cache_read_per_mtok: 0.5 };
    // 200k frescos × $2/M + 800k caché × $0.5/M + 100k salida × $8/M = 0.4 + 0.4 + 0.8
    expect(cloudCost(usageForPricing(data), provider)).toBeCloseTo(1.6);
  });

  it("contadores null (builds sin contador de caché) → 0, nunca NaN", () => {
    expect(usageForPricing({ prompt_tokens: 10, predicted_tokens: null })).toEqual({
      prompt_tokens: 10, completion_tokens: 0, cached_tokens: 0,
    });
    expect(usageForPricing(null).prompt_tokens).toBe(0);
  });
});

describe("totalTokens", () => {
  it("suma entrada fresca + caché + generado", () => {
    expect(totalTokens({ prompt_tokens: 100, cached_tokens: 900, predicted_tokens: 500 })).toBe(1500);
  });

  it("los null suman 0", () => {
    expect(totalTokens({ prompt_tokens: 7, cached_tokens: null, predicted_tokens: undefined })).toBe(7);
  });
});

describe("lifetimeModelRows", () => {
  const PROVIDER = { input_per_mtok: 2, output_per_mtok: 8, cache_read_per_mtok: 0.5 };

  it("mapea cada modelo a su total de tokens y costo en la nube", () => {
    const life = {
      models: [
        { model_name: "A", prompt_tokens: 100, cached_tokens: 900, predicted_tokens: 500 },
        { model_name: "B", prompt_tokens: 1000, cached_tokens: null, predicted_tokens: null },
      ],
    };
    const rows = lifetimeModelRows(life, PROVIDER);
    expect(rows[0]).toEqual({ model_name: "A", tokens: 1500, cost: expect.closeTo(0.00465) });
    // B: 1000 frescos × $2/M, sin caché ni salida.
    expect(rows[1]).toEqual({ model_name: "B", tokens: 1000, cost: expect.closeTo(0.002) });
  });

  it("sin datos o sin proveedor: filas vacías o costo 0, nunca NaN", () => {
    expect(lifetimeModelRows(null, PROVIDER)).toEqual([]);
    expect(lifetimeModelRows({ models: null }, PROVIDER)).toEqual([]);
    const rows = lifetimeModelRows(
      { models: [{ model_name: null, prompt_tokens: null, cached_tokens: null, predicted_tokens: null }] },
      null,
    );
    expect(rows).toEqual([{ model_name: "", tokens: 0, cost: 0 }]);
  });
});

describe("lifetimeSplitRows", () => {
  const PROVIDER = { input_per_mtok: 2, output_per_mtok: 8, cache_read_per_mtok: 0.5 };

  it("filas chat/externo con total de tokens y costo, en formato llama.cpp", () => {
    // El backend ya entrega fresh/cached/generated separados (unidades del
    // motor); totalTokens y cloudCost aplican sobre ese formato tal cual.
    const split = {
      chat: { prompt_tokens: 400_000, cached_tokens: 200_000, predicted_tokens: 100_000 },
      external: { prompt_tokens: 600_000, cached_tokens: 200_000, predicted_tokens: 200_000 },
    };
    const rows = lifetimeSplitRows(split, PROVIDER);
    expect(rows.map((r) => r.key)).toEqual(["chat", "external"]);
    expect(rows[0].tokens).toBe(700_000);
    // chat: 400k frescos × $2/M + 200k caché × $0.5/M + 100k salida × $8/M.
    expect(rows[0].cost).toBeCloseTo(1.7);
    expect(rows[1].tokens).toBe(1_000_000);
    // external: 600k × $2/M + 200k × $0.5/M + 200k × $8/M.
    expect(rows[1].cost).toBeCloseTo(2.9);
  });

  it("sin split o lados null: sin filas; sin proveedor: costo 0, nunca NaN", () => {
    expect(lifetimeSplitRows(null, PROVIDER)).toEqual([]);
    expect(lifetimeSplitRows({ chat: null, external: null }, PROVIDER)).toEqual([]);
    const rows = lifetimeSplitRows(
      { chat: { prompt_tokens: 10, cached_tokens: null, predicted_tokens: undefined } },
      null,
    );
    expect(rows).toEqual([{ key: "chat", tokens: 10, cost: 0 }]);
  });
});

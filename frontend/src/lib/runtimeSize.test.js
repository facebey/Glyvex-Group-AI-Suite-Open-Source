import { describe, expect, it } from "vitest";
import { sizeDeltaMb, formatDeltaMb, runtimeSizeMb, accelOffered, accelChoiceFromSource } from "./runtimeSize.js";

describe("sizeDeltaMb", () => {
  it("reemplazo (default): delta = nueva − anterior", () => {
    expect(sizeDeltaMb(656, 710.8)).toBe(-55);
  });

  it("tamaños iguales → 0", () => {
    expect(sizeDeltaMb(656, 656)).toBe(0);
  });

  it("tamaño previo desconocido → null (la UI oculta el delta)", () => {
    expect(sizeDeltaMb(656, null)).toBe(null);
    expect(sizeDeltaMb(656, undefined)).toBe(null);
    expect(sizeDeltaMb(656, "—")).toBe(null);
  });

  it("conservar build anterior: delta = +tamaño de la nueva (la vieja no se borra)", () => {
    expect(sizeDeltaMb(656, 710.8, true)).toBe(656);
  });

  it("conservar: no depende del tamaño previo (puede ser desconocido)", () => {
    expect(sizeDeltaMb(656, null, true)).toBe(656);
  });
});

describe("formatDeltaMb", () => {
  it("positivo lleva prefijo +", () => {
    expect(formatDeltaMb(656)).toBe("+656");
  });

  it("negativo y cero sin prefijo extra", () => {
    expect(formatDeltaMb(-55)).toBe("-55");
    expect(formatDeltaMb(0)).toBe("0");
  });

  it("null/undefined → null (se oculta)", () => {
    expect(formatDeltaMb(null)).toBe(null);
    expect(formatDeltaMb(undefined)).toBe(null);
  });
});

describe("runtimeSizeMb", () => {
  it("nvidia: base + CUDA (RT-10 fase A)", () => {
    expect(runtimeSizeMb("nvidia")).toBe(656);
  });

  it("amd: base + Vulkan (RT-10 B1)", () => {
    expect(runtimeSizeMb("amd")).toBe(58);
  });

  it("amd por elección (RT-10 B2): rocm paga el paquete ROCm; default sigue siendo vulkan", () => {
    expect(runtimeSizeMb("amd", "rocm")).toBe(274);
    expect(runtimeSizeMb("amd", "vulkan")).toBe(58);
    expect(runtimeSizeMb("amd", null)).toBe(58);
    expect(runtimeSizeMb("amd", "cuda")).toBe(58);
  });

  it("nvidia no tiene selector: la elección no cambia su tamaño", () => {
    expect(runtimeSizeMb("nvidia", "rocm")).toBe(656);
  });

  it("familias sin paquete y desconocidas → solo base (degradado a CPU)", () => {
    expect(runtimeSizeMb("intel")).toBe(23);
    expect(runtimeSizeMb("cpu")).toBe(23);
    expect(runtimeSizeMb(undefined)).toBe(23);
  });
});

describe("accelOffered", () => {
  it("espejo de ACCEL_SOURCES: nvidia y amd", () => {
    expect(accelOffered("nvidia")).toBe(true);
    expect(accelOffered("amd")).toBe(true);
  });

  it("el resto de familias no recibe oferta de aceleración", () => {
    expect(accelOffered("intel")).toBe(false);
    expect(accelOffered("cpu")).toBe(false);
    expect(accelOffered(undefined)).toBe(false);
  });
});

describe("accelChoiceFromSource", () => {
  it("mapea la fuente AMD instalada al tag de la elección", () => {
    expect(accelChoiceFromSource("official-vulkan")).toBe("vulkan");
    expect(accelChoiceFromSource("official-rocm-10.0")).toBe("rocm");
  });

  it("fuentes CUDA, desconocidas o ausentes → null (familias sin selector)", () => {
    expect(accelChoiceFromSource("official-cuda-12.4")).toBe(null);
    expect(accelChoiceFromSource("official")).toBe(null);
    expect(accelChoiceFromSource(null)).toBe(null);
    expect(accelChoiceFromSource(undefined)).toBe(null);
  });
});

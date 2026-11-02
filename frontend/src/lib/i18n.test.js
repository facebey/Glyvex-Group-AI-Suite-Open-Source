import { describe, expect, it } from "vitest";

import en from "../locales/en.json";
import es from "../locales/es.json";

// El repo mantiene es.json y en.json en paralelo: una clave agregada a un
// idioma sin su gemela en el otro produce UI en inglés dentro de la app en
// español (o la clave cruda). Este test es la compuerta de esa regla.

function flatten(obj, prefix = "") {
  const out = new Map();
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object") {
      for (const [k, v] of flatten(value, path)) out.set(k, v);
    } else {
      out.set(path, value);
    }
  }
  return out;
}

describe("paridad de locales es/en", () => {
  const esFlat = flatten(es);
  const enFlat = flatten(en);

  it("los dos idiomas tienen exactamente el mismo set de claves", () => {
    const missingInEn = [...esFlat.keys()].filter((k) => !enFlat.has(k));
    const missingInEs = [...enFlat.keys()].filter((k) => !esFlat.has(k));
    expect({ missingInEn, missingInEs }).toEqual({ missingInEn: [], missingInEs: [] });
  });

  it("todas las hojas son strings no vacíos", () => {
    const bad = [];
    for (const [source, flat] of [["es", esFlat], ["en", enFlat]]) {
      for (const [key, value] of flat) {
        if (typeof value !== "string" || value.trim() === "") bad.push(`${source}:${key}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("los plurales i18next van siempre en par (_one/_other)", () => {
    // Una clave plural sin su gemela cae al default de i18next y muestra la
    // clave cruda en alguno de los idiomas.
    const bad = [];
    for (const [source, flat] of [["es", esFlat], ["en", enFlat]]) {
      for (const key of flat.keys()) {
        if (key.endsWith("_one") && !flat.has(key.replace(/_one$/, "_other"))) {
          bad.push(`${source}:${key}`);
        }
      }
    }
    expect(bad).toEqual([]);
  });
});

describe("namespace hardware (A2)", () => {
  // El backend emite rationale/warnings con estas claves estables (ver
  // hardware_recommend.py): toda clave nueva del motor debe sumarse aquí y a
  // ambos idiomas.
  const RATIONALE_KEYS = [
    "max_fit", "headroom_f16", "mtp_fit", "vision_fit", "moe_experts_cpu",
    "partial_fit", "physical_cores", "max_fit_ram",
  ];
  const WARNING_KEYS = [
    "generic_draft", "generic_architecture", "draft_invalid", "no_vram_source",
    "model_not_fit_gpu", "ram_tight", "quant_suggested",
    "mtp_no_room", "vision_no_room",
  ];
  const UI_KEYS = [
    "autoTitle", "redetect", "detecting", "copyToFields", "detectError",
    "detectedAt", "cpuLabel", "ramLabel", "gpuLabel", "noGpu", "noData",
    "vramUnknown", "available", "physicalCores_one", "physicalCores_other",
    "logicalCores_one", "logicalCores_other",
    "degraded.cpu", "degraded.cpuVendor", "degraded.cpuProc", "degraded.cpuCim",
    "degraded.ram", "degraded.gpuNvml", "degraded.gpuLhm",
    "degraded.gpuLhmUnavailable", "degraded.gpuVramUnknown", "degraded.disk",
    "suggestButton", "suggesting", "suggestError", "noDraft", "suggestedName",
    "rationaleTitle", "warningsTitle",
  ];

  for (const [name, locale] of [["es", es], ["en", en]]) {
    it(`${name} cubre todas las claves del namespace hardware`, () => {
      const flat = flatten(locale);
      const need = [
        ...RATIONALE_KEYS.map((k) => `hardware.rationale.${k}`),
        ...WARNING_KEYS.map((k) => `hardware.warnings.${k}`),
        ...UI_KEYS.map((k) => `hardware.${k}`),
      ];
      expect(need.filter((k) => !flat.has(k))).toEqual([]);
    });
  }
});

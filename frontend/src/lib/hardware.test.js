import { describe, expect, it } from "vitest";

import {
  describeDegraded,
  hardwareToConfigFields,
  mbToGb,
  snapshotLines,
} from "./hardware.js";

// t() de mentira: devuelve la clave pedida (con el count como sufijo para
// plurales), suficiente para verificar QUÉ claves consume la UI.
const fakeT = (key, opts) =>
  opts && typeof opts.count === "number" ? `${key}:${opts.count}` : key;

const SNAPSHOT = {
  detected_at: "2026-10-29T00:00:00+00:00",
  cpu: {
    vendor: "amd", model: "AMD Ryzen 7 5800X", logical_cores: 16,
    physical_cores: 8, max_mhz: 4200, source: "env",
  },
  ram: { total_mb: 32768, available_mb: 24576, swap_total_mb: null },
  gpus: [
    { index: 0, name: "Intel(R) UHD Graphics", vendor: "intel", vram_total_mb: null },
    { index: 1, name: "AMD Radeon RX 7600", vendor: "amd", vram_total_mb: 8192 },
  ],
  disks: [],
  gpu_family: "amd",
  degraded: [],
};

describe("mbToGb", () => {
  it("redondea a la capacidad comercial de la placa", () => {
    expect(mbToGb(8192)).toBe(8);
    expect(mbToGb(12288)).toBe(12);
    expect(mbToGb(24576)).toBe(24);
    expect(mbToGb(32768)).toBe(32);
  });
});

describe("hardwareToConfigFields", () => {
  it("mapea la foto completa a los campos manuales", () => {
    expect(hardwareToConfigFields(SNAPSHOT)).toEqual({
      gpu_model: "AMD Radeon RX 7600",
      vram_gb: 8,
      cpu_model: "AMD Ryzen 7 5800X",
      cpu_cores: 8, // físicos, no lógicos
      ram_gb: 32,
    });
  });

  it("elige la GPU con más VRAM, no la primera listada", () => {
    const flipped = {
      ...SNAPSHOT,
      gpus: [
        { name: "RTX 4090", vendor: "nvidia", vram_total_mb: 24576 },
        { name: "RX 7600", vendor: "amd", vram_total_mb: 8192 },
      ],
    };
    expect(hardwareToConfigFields(flipped).gpu_model).toBe("RTX 4090");
    expect(hardwareToConfigFields(flipped).vram_gb).toBe(24);
  });

  it("no inventa VRAM para GPUs sin dato (Intel WDDM)", () => {
    const out = hardwareToConfigFields({ ...SNAPSHOT, gpus: [
      { name: "Intel UHD", vendor: "intel", vram_total_mb: null },
    ] });
    expect(out.gpu_model).toBe("Intel UHD");
    expect("vram_gb" in out).toBe(false);
  });

  it("un snapshot degradado no borra campos manuales: solo omite", () => {
    const out = hardwareToConfigFields({
      detected_at: "x", cpu: null, ram: null, gpus: [], disks: [],
      gpu_family: "cpu", degraded: ["cpu", "ram", "gpu.nvml"],
    });
    expect(out).toEqual({});
  });

  it("aceptar un snapshot podado sin romper (defense in depth)", () => {
    expect(hardwareToConfigFields(null)).toEqual({});
    expect(hardwareToConfigFields({})).toEqual({});
    expect(hardwareToConfigFields({ cpu: { vendor: "intel" } })).toEqual({
      cpu_model: "intel",
    });
  });
});

describe("describeDegraded", () => {
  it("traduce los códigos conocidos y deja crudos los desconocidos", () => {
    const out = describeDegraded(
      ["cpu.cim", "gpu.vram.unknown", "algo.nuevo"],
      fakeT,
    );
    expect(out).toEqual([
      "hardware.degraded.cpuCim",
      "hardware.degraded.gpuVramUnknown",
      "algo.nuevo",
    ]);
  });

  it("degraded ausente o vacío no rompe", () => {
    expect(describeDegraded(undefined, fakeT)).toEqual([]);
    expect(describeDegraded([], fakeT)).toEqual([]);
  });
});

describe("snapshotLines", () => {
  it("una línea por componente con datos", () => {
    const lines = snapshotLines(SNAPSHOT, fakeT);
    const byLabel = Object.fromEntries(lines.map((l) => [l.label, l.value]));
    expect(byLabel["hardware.cpuLabel"]).toContain("AMD Ryzen 7 5800X");
    expect(byLabel["hardware.cpuLabel"]).toContain("hardware.physicalCores:8");
    expect(byLabel["hardware.ramLabel"]).toContain("32 GB");
    expect(byLabel["hardware.ramLabel"]).toContain("24 GB");
    // Ambas GPUs listadas: la Intel con VRAM desconocida, no ocultada.
    const gpuLines = lines.filter((l) => l.label === "hardware.gpuLabel");
    expect(gpuLines).toHaveLength(2);
    expect(gpuLines[0].value).toContain("hardware.vramUnknown");
    expect(gpuLines[1].value).toContain("8 GB");
  });

  it("sin GPU dice 'no hay GPU', no una línea vacía", () => {
    const lines = snapshotLines({ ...SNAPSHOT, gpus: [] }, fakeT);
    expect(lines.some((l) => l.value === "hardware.noGpu")).toBe(true);
  });

  it("campos None muestran 'sin datos' explícito", () => {
    const lines = snapshotLines({
      ...SNAPSHOT, cpu: null, ram: null, gpus: [],
    }, fakeT);
    const values = lines.map((l) => l.value);
    expect(values.filter((v) => v === "hardware.noData")).toHaveLength(2);
  });
});

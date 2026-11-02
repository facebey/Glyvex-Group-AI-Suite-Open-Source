/**
 * hardware.js — helpers puros del panel "Detectado automáticamente" y del
 * botón "Sugerir a partir de mi hardware" (M6 / A2).
 *
 * Todo aquí es función pura sobre el JSON de /api/hardware: sin fetch y sin
 * React, para que los tests vitest cubran la lógica de presentación.
 */

export function mbToGb(mb) {
  return Math.round(Number(mb) / 1024);
}

/**
 * Snapshot detectado -> campos manuales de config.hardware (botón "Copiar a
 * los campos manuales"). Solo se copian datos que realmente hay: un campo
 * sin dato NO borra lo que el usuario ya escribió a mano.
 */
export function hardwareToConfigFields(snapshot) {
  const out = {};
  if (!snapshot || typeof snapshot !== "object") return out;

  const gpus = Array.isArray(snapshot.gpus) ? snapshot.gpus : [];
  const best = [...gpus].sort(
    (a, b) => (b.vram_total_mb || 0) - (a.vram_total_mb || 0)
  )[0];
  if (best && best.name) {
    out.gpu_model = best.name;
    if (best.vram_total_mb) out.vram_gb = mbToGb(best.vram_total_mb);
  }

  const cpu = snapshot.cpu;
  if (cpu) {
    if (cpu.model || cpu.vendor) out.cpu_model = cpu.model || cpu.vendor;
    const cores = cpu.physical_cores || cpu.logical_cores;
    if (cores) out.cpu_cores = cores;
  }

  if (snapshot.ram && snapshot.ram.total_mb) {
    out.ram_gb = mbToGb(snapshot.ram.total_mb);
  }
  return out;
}

// Códigos de `degraded` que emite hardware_detection.py -> claves i18n.
// Un código sin clave se muestra crudo (el panel sigue siendo legible).
const DEGRADED_KEYS = {
  cpu: "cpu",
  "cpu.vendor": "cpuVendor",
  "cpu.proc": "cpuProc",
  "cpu.cim": "cpuCim",
  ram: "ram",
  "gpu.nvml": "gpuNvml",
  "gpu.lhm": "gpuLhm",
  "gpu.lhm.unavailable": "gpuLhmUnavailable",
  "gpu.vram.unknown": "gpuVramUnknown",
  disk: "disk",
};

export function describeDegraded(degraded, t) {
  return (Array.isArray(degraded) ? degraded : []).map((code) => {
    const key = DEGRADED_KEYS[code];
    return key ? t(`hardware.degraded.${key}`) : String(code);
  });
}

/** Líneas de resumen del snapshot para el bloque de solo lectura. */
export function snapshotLines(snapshot, t) {
  const lines = [];
  if (!snapshot || typeof snapshot !== "object") return lines;

  const cpu = snapshot.cpu;
  if (cpu) {
    const name = cpu.model || cpu.vendor || "—";
    const cores = [
      cpu.physical_cores ? t("hardware.physicalCores", { count: cpu.physical_cores }) : null,
      cpu.logical_cores ? t("hardware.logicalCores", { count: cpu.logical_cores }) : null,
    ].filter(Boolean).join(" · ");
    lines.push({ label: t("hardware.cpuLabel"), value: [name, cores].filter(Boolean).join(" — ") });
  } else {
    lines.push({ label: t("hardware.cpuLabel"), value: t("hardware.noData") });
  }

  const ram = snapshot.ram;
  if (ram && ram.total_mb) {
    const parts = [`${mbToGb(ram.total_mb)} GB`];
    if (ram.available_mb) parts.push(`${t("hardware.available")} ${mbToGb(ram.available_mb)} GB`);
    lines.push({ label: t("hardware.ramLabel"), value: parts.join(" · ") });
  } else {
    lines.push({ label: t("hardware.ramLabel"), value: t("hardware.noData") });
  }

  const gpus = Array.isArray(snapshot.gpus) ? snapshot.gpus : [];
  if (gpus.length === 0) {
    lines.push({ label: t("hardware.gpuLabel"), value: t("hardware.noGpu") });
  }
  for (const gpu of gpus) {
    const vram = gpu.vram_total_mb
      ? `${mbToGb(gpu.vram_total_mb)} GB`
      : t("hardware.vramUnknown");
    lines.push({ label: t("hardware.gpuLabel"), value: `${gpu.name || "—"} — ${vram}` });
  }
  return lines;
}

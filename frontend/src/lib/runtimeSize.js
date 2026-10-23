// Delta de tamaño del runtime (pulido RT-12): cuánto cambia el espacio en
// disco al actualizar. Reemplazando la build anterior (default): delta =
// nueva − anterior. Conservándola (checkbox rollback): la vieja no se borra,
// así que el costo completo es el de la nueva.

export function sizeDeltaMb(newSizeMb, prevSizeMb, keepPrevious = false) {
  if (keepPrevious) return Math.round(newSizeMb);
  if (typeof prevSizeMb !== "number" || !Number.isFinite(prevSizeMb)) return null;
  return Math.round(newSizeMb - prevSizeMb);
}

// Etiqueta firmada del delta en MB: "+656", "-55", "0". Null → null
// (tamaño previo desconocido: la UI oculta el delta).
export function formatDeltaMb(deltaMb) {
  if (deltaMb === null || deltaMb === undefined) return null;
  return deltaMb > 0 ? `+${deltaMb}` : `${deltaMb}`;
}

// Estimación de descarga por familia de GPU (RT-10): base ~23 MB + paquete de
// aceleración. Valores reales de los archives pinnados b11827: CUDA 12.4 +
// cuDART ~633 MB; Vulkan ~35 MB (B1); ROCm 10.0 ~251 MB (B2). Es SOLO la
// estimación pre-descarga de la UI: con el runtime instalado manda el size_mb
// real de la meta. Debe espejar ACCEL_SOURCES del backend — familias sin
// entrada caen a la base (degradado a CPU). AMD es un mapa por elección (B2):
// sin elección manda `default`, que espeja el primer tag de la lista del
// backend (Vulkan por cubrir toda la familia).
const SIZE_BASE_MB = 23;
const SIZE_BY_FAMILY_MB = {
  nvidia: 656,
  amd: { default: "vulkan", vulkan: 58, rocm: 274 },
};

export function runtimeSizeMb(gpuFamily, accelChoice) {
  const entry = SIZE_BY_FAMILY_MB[gpuFamily];
  if (typeof entry === "number") return entry;
  if (entry) {
    return Object.hasOwn(entry, accelChoice) ? entry[accelChoice] : entry[entry.default];
  }
  return SIZE_BASE_MB;
}

// Mapea la fuente de aceleración instalada (status.accel_source desde la meta)
// al tag de la elección: "official-vulkan" → "vulkan", "official-rocm-10.0" →
// "rocm". Fuentes CUDA/desconocidas → null (familias sin selector: NVIDIA se
// decide por cuda_major).
export function accelChoiceFromSource(sourceId) {
  if (typeof sourceId !== "string") return null;
  if (sourceId.includes("rocm")) return "rocm";
  if (sourceId.includes("vulkan")) return "vulkan";
  return null;
}

// Familias para las que el runtime gestionado ofrece aceleración (espejo de
// ACCEL_SOURCES). Las demás se ofrecen solo con la base CPU.
export function accelOffered(gpuFamily) {
  return gpuFamily === "nvidia" || gpuFamily === "amd";
}

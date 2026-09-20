import { useEffect, useState } from "react";

/**
 * Métricas visibles en Monitor, Chat y Launcher (config.display).
 *
 * Se guarda lo OCULTO (display.hidden), no lo visible: una métrica que se
 * agregue en el futuro aparece sola. Ocultar es solo visual; el histórico y
 * las exportaciones siguen con todo, salvo "monitor.processes", que además
 * hace que el backend deje de recorrer los procesos del sistema.
 */

export const DISPLAY_CATALOG = [
  {
    page: "Monitor",
    items: [
      { key: "monitor.alerts", label: "Barra de alertas" },
      { key: "monitor.gpu.vram", label: "GPU · VRAM" },
      { key: "monitor.gpu.util", label: "GPU · utilización" },
      { key: "monitor.gpu.temp", label: "GPU · temperatura" },
      { key: "monitor.gpu.power_clocks", label: "GPU · potencia y clocks" },
      { key: "monitor.gpu.chart", label: "GPU · gráfico 60 s" },
      { key: "monitor.cpu.total", label: "CPU · utilización total" },
      { key: "monitor.cpu.cores", label: "CPU · núcleos" },
      { key: "monitor.cpu.freq", label: "CPU · frecuencia" },
      { key: "monitor.cpu.chart", label: "CPU · gráfico 60 s" },
      { key: "monitor.ram", label: "RAM" },
      { key: "monitor.history", label: "Histórico de hardware" },
      { key: "monitor.llm.live", label: "LLM Server · valores en vivo" },
      { key: "monitor.llm.live_charts", label: "LLM Server · gráficos de 5 min" },
      { key: "monitor.llm.history", label: "LLM Server · histórico" },
      {
        key: "monitor.processes",
        label: "Procesos activos",
        hint: "Oculto, el backend además deja de recorrer los procesos del sistema.",
      },
    ],
  },
  {
    page: "Chat",
    items: [
      { key: "chat.tps", label: "t/s" },
      { key: "chat.ttft", label: "TTFT" },
      { key: "chat.tokens", label: "Tokens" },
      { key: "chat.context", label: "Contexto" },
    ],
  },
  {
    page: "Launcher",
    items: [
      { key: "launcher.tg", label: "tg (velocidad)" },
      { key: "launcher.ctx", label: "ctx (contexto)" },
      { key: "launcher.queue", label: "cola" },
      { key: "launcher.cache", label: "caché" },
      { key: "launcher.mtp", label: "MTP (solo si está activo)" },
    ],
  },
];

export const ALL_DISPLAY_KEYS = DISPLAY_CATALOG.flatMap((group) => group.items.map((i) => i.key));

// Plantillas como listas de OCULTOS. PRESETS.default tiene que coincidir con
// DEFAULT_HIDDEN_METRICS de backend/config.py (lo verifica tests/test_config.py).
const MINIMAL_VISIBLE = [
  "monitor.alerts", "monitor.gpu.vram", "monitor.gpu.temp", "monitor.llm.live",
  "chat.tps", "chat.context", "launcher.tg", "launcher.ctx",
];

export const PRESETS = {
  default: ["monitor.gpu.power_clocks", "monitor.cpu.freq"],
  minimal: ALL_DISPLAY_KEYS.filter((k) => !MINIMAL_VISIBLE.includes(k)),
  full: [],
};

export const PRESET_LABELS = {
  default: "Predeterminada",
  minimal: "Mínima",
  full: "Completa",
  custom: "Personalizada",
};

/**
 * Plantilla que corresponde a una lista de ocultos. Solo compara claves del
 * catálogo actual: una clave vieja que quedó guardada no rompe la detección.
 */
export function detectPreset(hidden) {
  const current = new Set((hidden || []).filter((k) => ALL_DISPLAY_KEYS.includes(k)));
  for (const [name, keys] of Object.entries(PRESETS)) {
    if (keys.length === current.size && keys.every((k) => current.has(k))) return name;
  }
  return "custom";
}

// -- Estado compartido entre páginas ------------------------------------------

export const CONFIG_SAVED_EVENT = "glyvex:config-saved";

let cachedHidden = null;
let loading = null;
const listeners = new Set();

function publish(hidden) {
  cachedHidden = Array.isArray(hidden) ? hidden : PRESETS.default;
  listeners.forEach((fn) => fn(cachedHidden));
}

function loadOnce() {
  if (cachedHidden) return Promise.resolve(cachedHidden);
  if (!loading) {
    loading = fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((cfg) => publish(cfg?.display?.hidden))
      .catch(() => publish(PRESETS.default))
      .finally(() => { loading = null; });
  }
  return loading;
}

if (typeof window !== "undefined") {
  // Config.jsx lo dispara al guardar: las páginas abiertas se actualizan sin recargar.
  window.addEventListener(CONFIG_SAVED_EVENT, (event) => publish(event.detail?.display?.hidden));
}

/** Para tests: olvida lo cargado. */
export function resetDisplayCache() {
  cachedHidden = null;
  loading = null;
}

/**
 * isVisible(key) según la config guardada. Mientras carga usa la plantilla
 * predeterminada, que es lo que tiene la mayoría: así no aparece y desaparece
 * nada al abrir la página.
 *
 * `ready` indica que ya se leyó la config real. Lo que abre conexiones (un
 * WebSocket) tiene que esperarlo: con la plantilla provisoria, un elemento
 * que el usuario ocultó alcanzaría a conectarse un instante.
 */
export function useDisplay() {
  const [loaded, setLoaded] = useState(cachedHidden);
  useEffect(() => {
    listeners.add(setLoaded);
    loadOnce();
    if (cachedHidden) setLoaded(cachedHidden);
    return () => listeners.delete(setLoaded);
  }, []);
  const hidden = loaded || PRESETS.default;
  const hiddenSet = new Set(hidden);
  return {
    hidden,
    ready: loaded !== null,
    isVisible: (key) => !hiddenSet.has(key),
    anyVisible: (keys) => keys.some((k) => !hiddenSet.has(k)),
  };
}

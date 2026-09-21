import { useEffect, useState } from "react";

/**
 * Métricas visibles en Monitor, Chat y Launcher (config.display).
 *
 * Se guarda lo OCULTO (display.hidden), no lo visible: una métrica que se
 * agregue en el futuro aparece sola. Ocultar es solo visual; el histórico y
 * las exportaciones siguen con todo, salvo "monitor.processes", que además
 * hace que el backend deje de recorrer los procesos del sistema.
 */

// `page` y `labelKey`/`hintKey` son claves i18n (locales/es.json y en.json).
export const DISPLAY_CATALOG = [
  {
    page: "nav.monitor",
    items: [
      { key: "monitor.alerts", labelKey: "display.items.monitor.alerts" },
      { key: "monitor.gpu.vram", labelKey: "display.items.monitor.gpu.vram" },
      { key: "monitor.gpu.util", labelKey: "display.items.monitor.gpu.util" },
      { key: "monitor.gpu.temp", labelKey: "display.items.monitor.gpu.temp" },
      { key: "monitor.gpu.power_clocks", labelKey: "display.items.monitor.gpu.power_clocks" },
      { key: "monitor.gpu.chart", labelKey: "display.items.monitor.gpu.chart" },
      { key: "monitor.cpu.total", labelKey: "display.items.monitor.cpu.total" },
      { key: "monitor.cpu.cores", labelKey: "display.items.monitor.cpu.cores" },
      { key: "monitor.cpu.freq", labelKey: "display.items.monitor.cpu.freq" },
      { key: "monitor.cpu.chart", labelKey: "display.items.monitor.cpu.chart" },
      { key: "monitor.ram", labelKey: "display.items.monitor.ram" },
      { key: "monitor.history", labelKey: "display.items.monitor.history" },
      { key: "monitor.llm.live", labelKey: "display.items.monitor.llm.live" },
      { key: "monitor.llm.live_charts", labelKey: "display.items.monitor.llm.live_charts" },
      { key: "monitor.llm.history", labelKey: "display.items.monitor.llm.history" },
      {
        key: "monitor.processes",
        labelKey: "display.items.monitor.processes",
        hintKey: "display.items.monitor.processesHint",
      },
    ],
  },
  {
    page: "nav.chat",
    items: [
      { key: "chat.tps", labelKey: "display.items.chat.tps" },
      { key: "chat.ttft", labelKey: "display.items.chat.ttft" },
      { key: "chat.tokens", labelKey: "display.items.chat.tokens" },
      { key: "chat.context", labelKey: "display.items.chat.context" },
    ],
  },
  {
    page: "nav.launcher",
    items: [
      { key: "launcher.tg", labelKey: "display.items.launcher.tg" },
      { key: "launcher.ctx", labelKey: "display.items.launcher.ctx" },
      { key: "launcher.queue", labelKey: "display.items.launcher.queue" },
      { key: "launcher.cache", labelKey: "display.items.launcher.cache" },
      { key: "launcher.mtp", labelKey: "display.items.launcher.mtp" },
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

// Claves i18n de cada plantilla (ver locales/es.json y en.json).
export const PRESET_LABELS = {
  default: "display.presetDefault",
  minimal: "display.presetMinimal",
  full: "display.presetFull",
  custom: "display.presetCustom",
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

// Lógica pura del selector de presets de muestreo del Chat (CHAT-PRESETS U5).
// El backend ya decide CUÁL preset built-in es aplicable a un modelo (y lo
// marca con recommended); acá solo se decide cuál entrada usa la UI y qué
// alcance hereda un preset nuevo al guardarse.

export const PRESET_AUTO = "auto";
export const PRESET_CUSTOM = "custom";

/** Entrada a la que apunta la elección. PRESET_AUTO resuelve al recomendado
 *  de la lista (si hay); sin recomendado no se aplica nada. */
export function resolvePresetEntry(options, choice) {
  if (choice === PRESET_CUSTOM) return null;
  if (choice === PRESET_AUTO) return options.find((p) => p.recommended) || null;
  return options.find((p) => p.id === choice) || null;
}

/** Una elección guardada que ya no existe para este modelo (preset borrado,
 *  modelo cambiado) cae a auto en vez de dejar la UI apuntando a la nada. */
export function normalizePresetChoice(choice, options) {
  if (choice === PRESET_AUTO || choice === PRESET_CUSTOM) return choice;
  return options.some((p) => p.id === choice) ? choice : PRESET_AUTO;
}

/** Alcance del preset que se guarda: hereda el de la entrada de la que viene
 *  (familia o nombre exacto). Sin entrada, se ancla al modelo actual para
 *  que un preset custom no se aplique por accidente a otro modelo. */
export function presetSaveScope(entry, selectedModel) {
  if (entry?.family) return { family: entry.family, model_name: null };
  if (entry?.model_name) return { family: null, model_name: entry.model_name };
  return { family: null, model_name: selectedModel || null };
}

/** PRESET-ORIGIN: qué parámetros fija la entrada resuelta del preset. Al
 *  aplicarse, esos valores reemplazan a los defaults de la app (Chat.jsx hace
 *  { ...DEFAULT_PARAMS, ...entry.params }); la UI los marca para que se sepa
 *  qué valor viene del preset y cuál es default. Con 'custom' o 'auto' sin
 *  recomendado no hay entrada: ningún campo tiene origen de preset. */
export function presetOriginKeys(entry) {
  const params = entry?.params;
  if (!params || typeof params !== "object") return new Set();
  return new Set(Object.keys(params));
}

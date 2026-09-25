const THEME_KEY = "glyvex-theme";

/** Orden = orden del ciclo del botón del header. */
export const THEMES = ["dark", "light", "carbon", "metallic", "matrix"];

/** Metadatos por tema: clave i18n del nombre y color de muestra (swatch). */
export const THEME_META = {
  dark:     { labelKey: "config.appPrefs.themeDark",     swatch: "#0d9488" },
  light:    { labelKey: "config.appPrefs.themeLight",    swatch: "#bcb3d9" },
  carbon:   { labelKey: "config.appPrefs.themeCarbon",   swatch: "#14b8a6" },
  metallic: { labelKey: "config.appPrefs.themeMetallic", swatch: "#c4c8cd" },
  matrix:   { labelKey: "config.appPrefs.themeMatrix",   swatch: "#00ff66" },
};

export const THEME_CHANGED_EVENT = "glyvex-theme-changed";

/** Tema actual persistido (default: dark). */
export function getStoredTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  return THEMES.includes(stored) ? stored : "dark";
}

/** Siguiente tema en el ciclo. */
export function nextTheme(theme) {
  const i = THEMES.indexOf(theme);
  return THEMES[(i + 1) % THEMES.length];
}

/** Aplica el tema al DOM, lo persiste y avisa a los oyentes del evento. */
export function applyTheme(theme) {
  if (!THEMES.includes(theme)) theme = "dark";
  const root = document.documentElement;
  THEMES.forEach((t) => root.classList.remove(t));
  root.classList.add(theme);
  localStorage.setItem(THEME_KEY, theme);
  window.dispatchEvent(new CustomEvent(THEME_CHANGED_EVENT, { detail: theme }));
}

/** Avanza al siguiente tema del ciclo y lo devuelve. */
export function cycleTheme(current = getStoredTheme()) {
  const next = nextTheme(current);
  applyTheme(next);
  return next;
}

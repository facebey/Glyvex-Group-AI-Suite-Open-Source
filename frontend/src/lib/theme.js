const THEME_KEY = "glyvex-theme";

/** Orden = orden del ciclo del botón del header (temas internos). */
export const THEMES = ["dark", "light", "carbon", "metallic", "matrix"];

/**
 * Nombre válido de tema externo (carpeta de data/themes/): mismo
 * contrato que el backend (THEME_NAME_RE de backend/themes.py).
 */
export const EXTERNAL_THEME_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 _\-.]{0,63}$/;

export function isExternalThemeName(name) {
  // Los 5 internos también cumplen el regex: nunca contarlos como
  // externos, o el App los trataría como "carpeta ausente" y forzará
  // a dark cada vez que se elijan.
  if (typeof name !== "string" || THEMES.includes(name)) return false;
  return EXTERNAL_THEME_NAME_RE.test(name);
}

/** Metadatos por tema interno: clave i18n del nombre y swatch. */
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
  if (THEMES.includes(stored)) return stored;
  // Nombre externo bien formado: lo valida contra el listado de carpetas
  // el App (si la carpeta desapareció, vuelve a dark ahí).
  if (isExternalThemeName(stored)) return stored;
  return "dark";
}

/** Siguiente elemento de una lista (ciclo). Desconocido → el primero. */
export function nextInList(list, current) {
  if (!list || list.length === 0) return current;
  const i = list.indexOf(current);
  if (i === -1) return list[0];
  return list[(i + 1) % list.length];
}

/** Siguiente tema interno en el ciclo. */
export function nextTheme(theme) {
  return nextInList(THEMES, theme);
}

/**
 * Aplica el tema, lo persiste y avisa a los oyentes del evento.
 * Acepta temas internos y nombres externos válidos: el CSS externo lo
 * inyecta el App (él es quien conoce el listado de carpetas detectadas),
 * nunca esta lib.
 */
export function applyTheme(theme) {
  const valid = THEMES.includes(theme) || isExternalThemeName(theme);
  if (!valid) theme = "dark";
  const root = document.documentElement;
  THEMES.forEach((t) => root.classList.remove(t));
  if (THEMES.includes(theme)) root.classList.add(theme);
  localStorage.setItem(THEME_KEY, theme);
  window.dispatchEvent(new CustomEvent(THEME_CHANGED_EVENT, { detail: theme }));
}

/** URL de un archivo de tema (CSS o textura) servido por el backend. */
export function themeHref(name, file, mtime) {
  const v = Number.isFinite(mtime) ? `?v=${encodeURIComponent(mtime)}` : "";
  // Solo se codifican los segmentos, no el "/" separador: la ruta del
  // endpoint ({name}/{file_path:path}) depende de que queden los slashes.
  const safeFile = file.split("/").map(encodeURIComponent).join("/");
  return `/api/themes/${encodeURIComponent(name)}/${safeFile}${v}`;
}

const THEME_LINK_ATTR = "gx-ext-theme";

/**
 * Inyecta (o quita) los <link> del tema externo activo. Solo el CSS del
 * tema que está puesto vive en el documento: los archivos son
 * autocontenidos y no interfieren con los temas internos. Las texturas
 * se refieren con url() relativa, que resuelve contra el href del link.
 *
 * meta: null (tema interno activo) o {name, files, mtime} del listado.
 */
export function syncThemeLinks(meta) {
  const head = document.head;
  head.querySelectorAll(`link[${THEME_LINK_ATTR}]`).forEach((el) => el.remove());
  if (!meta) return;
  for (const file of meta.files) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = themeHref(meta.name, file, meta.mtime);
    link.setAttribute(THEME_LINK_ATTR, meta.name);
    head.appendChild(link);
  }
}

/** Avanza al siguiente tema interno del ciclo y lo devuelve. */
export function cycleTheme(current = getStoredTheme()) {
  const next = nextTheme(current);
  applyTheme(next);
  return next;
}

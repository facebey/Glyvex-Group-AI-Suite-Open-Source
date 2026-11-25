// Textos del splash de arranque (BootSplash en main.jsx) — I18N-SPLASH.
// El splash vive en el origen del bundle (https://tauri.localhost), donde el
// localStorage está aislado del origen de la app: no puede leer el idioma
// guardado ni inicializar i18n contra él. El idioma llega por IPC de la shell
// (comando `ui_lang`, espejado por `set_ui_lang` cuando se cambia en Config)
// y el fallback es "es", el mismo default inicial de i18n.js.
export const SUPPORTED_LANGS = ["es", "en"];

export function resolveSplashLang(raw) {
  const lang = String(raw ?? "").trim().toLowerCase();
  return SUPPORTED_LANGS.includes(lang) ? lang : "es";
}

// El tagline espeja `about.tagline` de los locales: el splash y el diálogo
// About deben decir exactamente lo mismo en cada idioma (verado por tests).
export const SPLASH_TEXTS = {
  es: { tagline: "IA local, sin nube", starting: "Iniciando servicios…" },
  en: { tagline: "Local AI, no cloud", starting: "Starting services…" },
};

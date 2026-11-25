import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import es from "../locales/es.json";
import en from "../locales/en.json";

const STORAGE_KEY = "glyvex-lang";
const SUPPORTED = ["es", "en"];

function initialLang() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw !== null) {
      const parsed = JSON.parse(raw);
      if (SUPPORTED.includes(parsed)) return parsed;
    }
  } catch {
    // localStorage corrupto o bloqueado: se usa el default
  }
  return "es";
}

export function setStoredLang(lang) {
  if (!SUPPORTED.includes(lang)) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(lang));
  } catch {
    // localStorage bloqueado: el idioma solo rige para esta sesión
  }
  // I18N-SPLASH: espejar a la shell para que el splash del próximo arranque
  // hable este idioma (su origen no puede leer este localStorage). Fire-and-
  // forget: si la shell no está o el comando falla, la app sigue igual.
  import("./tauri.js").then(({ setUiLang }) => setUiLang(lang)).catch(() => {});
}

i18n.use(initReactI18next).init({
  resources: {
    es: { translation: es },
    en: { translation: en },
  },
  lng: initialLang(),
  fallbackLng: "es",
  interpolation: { escapeValue: false },
});

export default i18n;

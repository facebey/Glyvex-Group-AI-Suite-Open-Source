import { useTranslation } from "react-i18next";
import { Languages } from "lucide-react";
import { setStoredLang } from "../lib/i18n.js";

const LANGS = [
  { code: "es", label: "ES" },
  { code: "en", label: "EN" },
];

/**
 * Selector de idioma del header. Persiste la elección en localStorage
 * (key glyvex-lang) y cambia el idioma al instante en toda la app.
 */
export default function LanguageSelector() {
  const { i18n, t } = useTranslation();

  function handleChange(e) {
    const lang = e.target.value;
    setStoredLang(lang);
    i18n.changeLanguage(lang);
  }

  return (
    <label className="flex items-center gap-1.5 text-glyvex-muted hover:text-glyvex-text transition-colors">
      <Languages size={16} aria-hidden="true" />
      <select
        value={i18n.resolvedLanguage || i18n.language}
        onChange={handleChange}
        title={t("language.label")}
        aria-label={t("language.label")}
        className="bg-transparent text-xs font-medium outline-none cursor-pointer max-w-[3rem]"
      >
        {LANGS.map(({ code, label }) => (
          <option key={code} value={code} className="bg-glyvex-card text-glyvex-text">
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}

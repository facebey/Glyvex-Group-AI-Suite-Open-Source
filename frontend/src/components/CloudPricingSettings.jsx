import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import Section from "./ui/Section.jsx";
import { formInputClasses } from "../lib/styles.js";
import { useTranslation } from "react-i18next";

/**
 * Sección "Precios de nube (referencia)" de Config (MON-2).
 *
 * Editor JSON libre: la tabla de precios cambia seguido y no conviene
 * mantenerla en código. Acá se valida la sintaxis; al leer, el backend
 * (get_cloud_pricing) cae a los defaults si la estructura quedó rota.
 */
export default function CloudPricingSettings({ pricing, onChange }) {
  const { t } = useTranslation();
  const [text, setText] = useState(() => JSON.stringify(pricing ?? {}, null, 2));
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);

  // Sincroniza solo si el VALOR real cambió (reset global, config recargada):
  // tras un onChange propio el JSON parseado ya es igual al prop y no se
  // reescribe el buffer (el cursor no salta mientras se tipea).
  useEffect(() => {
    try {
      const current = JSON.parse(text);
      if (JSON.stringify(current) === JSON.stringify(pricing ?? {})) return;
    } catch { /* buffer inválido: se re-sincroniza con el prop */ }
    setText(JSON.stringify(pricing ?? {}, null, 2));
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pricing]);

  function handleTextChange(value) {
    setText(value);
    setNote(null);
    try {
      onChange(JSON.parse(value));
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleRestore() {
    try {
      const res = await fetch("/api/config/pricing-defaults");
      if (!res.ok) throw new Error();
      const defaults = await res.json();
      setText(JSON.stringify(defaults, null, 2));
      onChange(defaults);
      setError(null);
      setNote(t("config.cloudPricing.restored"));
    } catch {
      setNote(t("config.cloudPricing.restoreError"));
    }
  }

  return (
    <Section title={t("config.cloudPricing.section")}>
      <p className="text-xs text-glyvex-muted mb-3">{t("config.cloudPricing.hint")}</p>
      <textarea
        value={text}
        onChange={(e) => handleTextChange(e.target.value)}
        rows={14}
        spellCheck={false}
        className={formInputClasses + " font-mono text-xs"}
      />
      {error && (
        <p className="mt-2 text-xs text-red-400">
          {t("config.cloudPricing.invalidJson", { error })}
        </p>
      )}
      {note && <p className="mt-2 text-xs text-glyvex-muted">{note}</p>}
      <div className="mt-3">
        <button
          type="button"
          onClick={handleRestore}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled transition-colors"
        >
          <RotateCcw size={12} />
          {t("config.cloudPricing.restore")}
        </button>
      </div>
    </Section>
  );
}

import { useTranslation } from "react-i18next";
import Section from "./ui/Section.jsx";
import { formInputClasses } from "../lib/styles.js";
import {
  ALL_DISPLAY_KEYS,
  DISPLAY_CATALOG,
  PRESETS,
  PRESET_LABELS,
  detectPreset,
} from "../lib/metricsDisplay.js";

/**
 * Sección "Métricas visibles" de Config. Componente controlado, como
 * ChatSettings: recibe `display` ({ preset, hidden }) y sube los cambios; el
 * guardado lo hace el botón de Config.
 */
export default function MetricsDisplaySettings({ display, onChange }) {
  const { t } = useTranslation();
  const hidden = Array.isArray(display?.hidden) ? display.hidden : PRESETS.default;
  const hiddenSet = new Set(hidden);
  const preset = detectPreset(hidden);

  // Claves guardadas que el catálogo actual no conoce (de otra versión): se
  // conservan tal cual al editar.
  const foreign = hidden.filter((k) => !ALL_DISPLAY_KEYS.includes(k));

  function commit(nextHidden) {
    const clean = [...new Set([...foreign, ...nextHidden.filter((k) => ALL_DISPLAY_KEYS.includes(k))])];
    onChange({ preset: detectPreset(clean), hidden: clean });
  }

  function toggle(key) {
    commit(hiddenSet.has(key) ? hidden.filter((k) => k !== key) : [...hidden, key]);
  }

  function setGroup(items, visible) {
    const keys = items.map((i) => i.key);
    commit(visible ? hidden.filter((k) => !keys.includes(k)) : [...hidden, ...keys]);
  }

  return (
    <Section title={t("display.sectionTitle")}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="block text-sm text-glyvex-muted mb-1">{t("display.preset")}</span>
          <select
            className={formInputClasses}
            value={preset}
            onChange={(e) => e.target.value !== "custom" && commit(PRESETS[e.target.value])}
          >
            {["default", "minimal", "full"].map((p) => (
              <option key={p} value={p}>{t(PRESET_LABELS[p])}</option>
            ))}
            {preset === "custom" && <option value="custom">{t(PRESET_LABELS.custom)}</option>}
          </select>
        </label>
        <button
          type="button"
          onClick={() => commit(PRESETS.default)}
          disabled={preset === "default"}
          className="px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30 disabled:opacity-40"
        >
          {t("display.restore")}
        </button>
      </div>

      <p className="text-xs text-glyvex-muted">
        {t("display.note")}
      </p>

      {DISPLAY_CATALOG.map((group) => {
        const visibleCount = group.items.filter((i) => !hiddenSet.has(i.key)).length;
        return (
          <div key={group.page} className="rounded-md border border-white/10 p-3">
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-glyvex-text">
                {t(group.page)}
                <span className="ml-2 text-xs font-normal text-glyvex-muted">
                  {t("display.visibleOf", { visible: visibleCount, total: group.items.length })}
                </span>
              </p>
              <div className="flex gap-2 text-xs">
                <button type="button" onClick={() => setGroup(group.items, true)}
                  className="text-glyvex-muted hover:text-glyvex-text">{t("display.showAll")}</button>
                <span className="text-glyvex-muted/50">·</span>
                <button type="button" onClick={() => setGroup(group.items, false)}
                  className="text-glyvex-muted hover:text-glyvex-text">{t("display.hideAll")}</button>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5">
              {group.items.map((item) => (
                <label key={item.key} className="flex items-start gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    className="mt-0.5 accent-glyvex-accent"
                    checked={!hiddenSet.has(item.key)}
                    onChange={() => toggle(item.key)}
                  />
                  <span>
                    <span className="text-glyvex-text">{t(item.labelKey)}</span>
                    {item.hintKey && <span className="block text-xs text-glyvex-muted">{t(item.hintKey)}</span>}
                  </span>
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </Section>
  );
}

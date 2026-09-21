import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Lightbulb } from "lucide-react";
import { inputClasses } from "../lib/styles.js";

/** Modos del control de razonamiento (popover de la lámpara). */
const REASONING_MODES = [
  { id: "off", labelKey: "reasoning.modeOff", hintKey: "reasoning.modeOffHint" },
  { id: "thinking", labelKey: "reasoning.modeThinking", hintKey: "reasoning.modeThinkingHint" },
  { id: "preserve", labelKey: "reasoning.modePreserve", hintKey: "reasoning.modePreserveHint" },
];

const EFFORT_PRESETS = [
  { id: "low", labelKey: "reasoning.low", tokens: 1024 },
  { id: "medium", labelKey: "reasoning.medium", tokens: 4096 },
  { id: "high", labelKey: "reasoning.high", tokens: 8192 },
  { id: "max", labelKey: "reasoning.max", tokens: 16384 },
];

const REASONING_EFFORTS = ["none", "low", "medium", "high"];

export const DEFAULT_REASONING = { mode: "off", budget: 8192, effort: "none" };

// ---------------------------------------------------------------------------
// Control de razonamiento (lámpara + popover)
//
// Se extrajo de Chat.jsx en la Conversación E: además del composer, el
// botón de regenerar (Bloque 4.3) va a necesitar el mismo control.
// ---------------------------------------------------------------------------

export default function ReasoningControl({ value, onChange }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const isPreset = EFFORT_PRESETS.some((p) => p.tokens === value.budget);
  const [customOpen, setCustomOpen] = useState(!isPreset);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = value.mode !== "off";
  const badge = value.mode === "thinking" ? "T" : value.mode === "preserve" ? "P" : null;

  function pick(patch) {
    onChange({ ...value, ...patch });
  }

  const optionRow =
    "w-full flex items-start gap-2 px-2 py-1.5 rounded-md text-left text-sm hover:bg-white/5";

  return (
    <div className="relative" ref={wrapRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title={
          active
            ? t("reasoning.titleActive", {
                mode: t(value.mode === "thinking" ? "reasoning.modeThinking" : "reasoning.modePreserve"),
              })
            : t("reasoning.titleOff")
        }
        className={
          "flex items-center gap-1 h-9 px-2 rounded-md border text-sm shrink-0 " +
          (active
            ? "border-yellow-500/40 bg-yellow-500/10 text-yellow-500"
            : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30")
        }
      >
        <Lightbulb size={16} />
        {badge && <span className="text-xs font-semibold tabular-nums">{badge}</span>}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-full left-0 mb-2 w-72 z-30 rounded-lg border border-white/10 bg-glyvex-card shadow-xl shadow-black/40 overflow-hidden"
        >
          <div className="px-3 py-2 text-xs font-medium text-glyvex-muted border-b border-white/10">
            {t("reasoning.heading")}
          </div>

          <div className="p-1.5 border-b border-white/10">
            {REASONING_MODES.map((mode) => (
              <button
                key={mode.id}
                type="button"
                role="menuitemradio"
                aria-checked={value.mode === mode.id}
                onClick={() => {
                  pick({ mode: mode.id });
                  setOpen(false);
                }}
                className={optionRow}
              >
                <span
                  className={
                    "mt-1 w-2.5 h-2.5 rounded-full shrink-0 border " +
                    (value.mode === mode.id
                      ? "bg-yellow-500 border-yellow-500"
                      : "border-white/30")
                  }
                />
                <span className="min-w-0">
                  <span className="block text-glyvex-text">{t(mode.labelKey)}</span>
                  <span className="block text-xs text-glyvex-muted">{t(mode.hintKey)}</span>
                </span>
              </button>
            ))}
          </div>

          <div className="px-3 py-2 border-b border-white/10 space-y-2">
              <span className="block text-xs text-glyvex-muted">
                {t("reasoning.effort", { value: value.budget === -1 ? "∞" : value.budget.toLocaleString() })}
              </span>
            <div className="flex flex-wrap gap-1">
              {EFFORT_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => {
                    pick({ budget: preset.tokens });
                    setCustomOpen(false);
                  }}
                  className={
                    "px-2 py-1 rounded-md text-xs border " +
                    (value.budget === preset.tokens && !customOpen
                      ? "border-yellow-500/50 bg-yellow-500/10 text-yellow-500"
                      : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-white/5")
                  }
                >
                  {t(preset.labelKey)}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setCustomOpen((v) => !v)}
                className={
                  "px-2 py-1 rounded-md text-xs border " +
                  (customOpen
                    ? "border-yellow-500/50 bg-yellow-500/10 text-yellow-500"
                    : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-white/5")
                }
              >
                Custom
              </button>
            </div>
            {customOpen && (
              <input
                type="number"
                min={-1}
                step={256}
                className={inputClasses}
                value={value.budget}
                onChange={(e) => pick({ budget: Number(e.target.value) })}
                placeholder={t("reasoning.budgetPlaceholder")}
              />
            )}
          </div>

          <div className="px-3 py-2 space-y-2">
            <span className="block text-xs text-glyvex-muted">reasoning_effort (llama.cpp)</span>
            <div className="flex flex-wrap gap-1">
              {REASONING_EFFORTS.map((effort) => (
                <button
                  key={effort}
                  type="button"
                  onClick={() => pick({ effort })}
                  className={
                    "px-2 py-1 rounded-md text-xs border " +
                    (value.effort === effort
                      ? "border-yellow-500/50 bg-yellow-500/10 text-yellow-500"
                      : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-white/5")
                  }
                >
                  {effort}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

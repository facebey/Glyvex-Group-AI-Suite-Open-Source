export default function Slider({ label, value, min, max, step = 1, onChange, formatValue, originTag }) {
  return (
    <label className="block">
      <span className="flex justify-between text-xs text-glyvex-muted mb-1">
        <span>
          {label}
          {originTag && (
            // PRESET-ORIGIN: el valor viene del preset activo, no del default
            // de la app (ver presetOriginKeys en lib/chatPresets.js).
            <span
              className="ml-1.5 px-1 rounded border border-glyvex-border-soft text-[10px] leading-4 align-middle"
              title={originTag}
            >
              {originTag}
            </span>
          )}
        </span>
        <span className="text-glyvex-text">{formatValue ? formatValue(value) : value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-glyvex-accent"
      />
    </label>
  );
}

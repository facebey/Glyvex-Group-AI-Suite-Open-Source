export default function Slider({ label, value, min, max, step = 1, onChange, formatValue }) {
  return (
    <label className="block">
      <span className="flex justify-between text-xs text-glyvex-muted mb-1">
        <span>{label}</span>
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

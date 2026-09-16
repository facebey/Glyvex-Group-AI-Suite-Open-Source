/** Campo etiquetado, con nota aclaratoria opcional debajo. */
export default function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="block text-sm text-glyvex-muted mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-glyvex-muted/70 mt-1">{hint}</span>}
    </label>
  );
}

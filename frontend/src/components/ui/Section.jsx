/** Tarjeta con título para agrupar campos en páginas de configuración. */
export default function Section({ title, children }) {
  return (
    <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5">
      <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide mb-4">
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

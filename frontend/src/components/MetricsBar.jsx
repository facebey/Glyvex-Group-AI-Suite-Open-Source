/**
 * Barra superior del área de chat: métricas en vivo de la generación actual
 * (o de la última respuesta terminada) + contexto usado/total.
 *
 * TTFT se muestra apenas llega el primer valor > 0 (antes esperaba al evento
 * "done" porque el primer chunk del stream trae ttft_ms == 0).
 *
 * Conversación E: "Contexto" dejó de ser los tokens generados por la última
 * respuesta — que no era contexto de nada — y pasó a ser la ocupación real
 * del historial + adjuntos + borrador, calculada por /api/chat/estimate. El
 * prefijo ~ aparece cuando el número salió de la heurística de caracteres
 * porque el endpoint no expone /tokenize.
 */
export default function MetricsBar({
  metrics,
  estimate = null,
  contextTotal = 0,
  streaming = false,
}) {
  const tps = metrics?.tps ?? 0;
  const ttftMs = metrics?.ttft_ms > 0 ? metrics.ttft_ms : null;
  const tokensTotal = metrics?.tokens_total ?? metrics?.total_tokens ?? 0;

  const contextUsed = estimate?.tokens ?? 0;
  const total = contextTotal || estimate?.context_size || 0;
  const approx = estimate?.source === "heuristic";

  const contextRatio = total > 0 ? contextUsed / total : 0;
  const contextColor =
    estimate && !estimate.fits
      ? "text-red-400"
      : contextRatio >= 0.9
        ? "text-red-400"
        : contextRatio >= 0.75
          ? "text-amber-400"
          : "text-glyvex-text";

  const contextValue = total
    ? `${approx ? "~" : ""}${contextUsed.toLocaleString()} / ${total.toLocaleString()}`
    : contextUsed
      ? `${approx ? "~" : ""}${contextUsed.toLocaleString()}`
      : "—";

  const contextTitle = estimate
    ? [
        approx
          ? "Estimado por caracteres: el endpoint no expone /tokenize."
          : "Contado con el tokenizer del modelo.",
        estimate.image_count
          ? `Incluye ${estimate.image_count} imagen(es) a ~${estimate.image_tokens.toLocaleString()} tokens.`
          : null,
        estimate.headroom
          ? `Margen tras la respuesta: ${estimate.headroom.toLocaleString()} tokens.`
          : null,
      ]
        .filter(Boolean)
        .join(" ")
    : undefined;

  const items = [
    { label: "t/s", value: tps ? tps.toFixed(1) : "—", live: true },
    { label: "TTFT", value: ttftMs ? `${Math.round(ttftMs)} ms` : "—" },
    { label: "Tokens", value: tokensTotal || "—" },
    {
      label: "Contexto",
      value: contextValue,
      className: contextColor,
      title: contextTitle,
    },
  ];

  return (
    <div className="flex items-center gap-6 px-4 py-2 bg-glyvex-card border-b border-white/10 text-sm">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5" title={item.title}>
          <span className="text-glyvex-muted">{item.label}</span>
          <span className={`font-medium tabular-nums ${item.className || "text-glyvex-text"}`}>
            {item.value}
          </span>
          {item.live && streaming && (
            <span
              className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse ml-0.5"
              title="Generando"
              aria-label="Generando"
            />
          )}
        </div>
      ))}
    </div>
  );
}

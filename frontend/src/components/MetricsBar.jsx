/**
 * Barra superior del área de chat: métricas en vivo de la generación actual
 * (o de la última respuesta terminada) + contexto usado/total.
 *
 * TTFT se muestra apenas llega el primer valor > 0 (antes esperaba al evento
 * "done" porque el primer chunk del stream trae ttft_ms == 0).
 *
 * Conversación E: "Contexto" dejó de ser los tokens generados por la última
 * respuesta — que no era contexto de nada — y pasó a ser la ocupación real
 * del historial + adjuntos + borrador, calculada por /api/chat/estimate.
 *
 * Métricas corregidas (stream_metrics.py): TTFT cuenta desde el primer token
 * de cualquier tipo, razonamiento incluido; t/s sale de los timings del
 * servidor cuando existen. "Contexto" usa lo que midió el servidor al
 * terminar la última respuesta más la estimación del borrador. El prefijo ~
 * aparece solo cuando algo salió de la heurística de caracteres.
 */

const SOURCE_LABEL = {
  timings: "Medido por el servidor (timings).",
  usage: "Tokens del servidor sobre el tiempo de generación medido.",
  chunks: "Aproximado: el endpoint no informa tokens, se cuentan fragmentos del stream.",
};

function formatMs(ms) {
  if (ms == null || ms <= 0) return null;
  return ms >= 10000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export default function MetricsBar({
  metrics,
  estimate = null,
  contextTotal = 0,
  streaming = false,
}) {
  const tps = metrics?.tps ?? null;
  const ppTps = metrics?.pp_tps ?? null;
  const ttft = formatMs(metrics?.ttft_ms);
  const ttftAnswer = formatMs(metrics?.ttft_answer_ms);
  const tokensTotal = metrics?.tokens_total ?? metrics?.total_tokens ?? 0;
  const tokensThinking = metrics?.tokens_thinking ?? 0;
  const source = metrics?.metrics_source;

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
        estimate.base_tokens
          ? `${estimate.base_tokens.toLocaleString()} tokens medidos por el servidor en la última respuesta (incluye plantilla, tools y razonamiento).`
          : null,
        estimate.source === "measured"
          ? null
          : approx
            ? "Lo nuevo está estimado por caracteres: el endpoint no expone /tokenize."
            : estimate.base_tokens
              ? "Lo nuevo está contado con el tokenizer del modelo."
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

  const tpsTitle = [
    source ? SOURCE_LABEL[source] : null,
    ppTps ? `Procesamiento de prompt: ${ppTps.toFixed(1)} t/s.` : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;

  const ttftTitle = ttft
    ? [
        "Tiempo hasta el primer token, razonamiento incluido.",
        ttftAnswer && ttftAnswer !== ttft ? `Primera palabra de la respuesta: ${ttftAnswer}.` : null,
      ]
        .filter(Boolean)
        .join(" ")
    : undefined;

  const tokensTitle = tokensThinking
    ? `${tokensThinking.toLocaleString()} de razonamiento y ${Math.max(0, tokensTotal - tokensThinking).toLocaleString()} de respuesta.`
    : undefined;

  const items = [
    {
      label: "t/s",
      value: tps ? `${source === "chunks" ? "~" : ""}${tps.toFixed(1)}` : "—",
      live: true,
      title: tpsTitle,
    },
    { label: "TTFT", value: ttft || "—", title: ttftTitle },
    { label: "Tokens", value: tokensTotal ? tokensTotal.toLocaleString() : "—", title: tokensTitle },
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

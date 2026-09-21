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

import { useTranslation } from "react-i18next";
import { useDisplay } from "../lib/metricsDisplay.js";

const SOURCE_KEYS = {
  timings: "metricsBar.sourceTimings",
  usage: "metricsBar.sourceUsage",
  chunks: "metricsBar.sourceChunks",
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
  const { t } = useTranslation();
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
          ? t("metricsBar.contextMeasured", { count: estimate.base_tokens.toLocaleString() })
          : null,
        estimate.source === "measured"
          ? null
          : approx
            ? t("metricsBar.contextEstimated")
            : estimate.base_tokens
              ? t("metricsBar.contextTokenizedNew")
              : t("metricsBar.contextTokenized"),
        estimate.image_count
          ? t("metricsBar.contextImages", { count: estimate.image_count, tokens: estimate.image_tokens.toLocaleString() })
          : null,
        estimate.headroom
          ? t("metricsBar.contextHeadroom", { count: estimate.headroom.toLocaleString() })
          : null,
      ]
        .filter(Boolean)
        .join(" ")
    : undefined;

  const tpsTitle = [
    source ? t(SOURCE_KEYS[source]) : null,
    ppTps ? t("metricsBar.promptProcessing", { value: ppTps.toFixed(1) }) : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;

  const ttftTitle = ttft
    ? [
        t("metricsBar.ttftHelp"),
        ttftAnswer && ttftAnswer !== ttft
          ? t("metricsBar.ttftFirstWord", { value: ttftAnswer })
          : null,
      ]
        .filter(Boolean)
        .join(" ")
    : undefined;

  const tokensTitle = tokensThinking
    ? t("metricsBar.tokensBreakdown", {
        thinking: tokensThinking.toLocaleString(),
        answer: Math.max(0, tokensTotal - tokensThinking).toLocaleString(),
      })
    : undefined;

  const { isVisible } = useDisplay();

  const items = [
    {
      key: "chat.tps",
      label: "t/s",
      value: tps ? `${source === "chunks" ? "~" : ""}${tps.toFixed(1)}` : "—",
      live: true,
      title: tpsTitle,
    },
    { key: "chat.ttft", label: "TTFT", value: ttft || "—", title: ttftTitle },
    { key: "chat.tokens", label: t("metricsBar.tokens"), value: tokensTotal ? tokensTotal.toLocaleString() : "—", title: tokensTitle },
    {
      key: "chat.context",
      label: t("metricsBar.context"),
      value: contextValue,
      className: contextColor,
      title: contextTitle,
    },
  ].filter((item) => isVisible(item.key));

  // Todo oculto en Config: la barra no ocupa lugar.
  if (items.length === 0) return null;

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
              title={t("metricsBar.generating")}
              aria-label={t("metricsBar.generating")}
            />
          )}
        </div>
      ))}
    </div>
  );
}

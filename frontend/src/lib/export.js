/**
 * export.js — Descarga de la conversación visible como JSON o Markdown.
 * Recibe el camino activo del árbol, no el árbol entero: se exporta lo que
 * el usuario ve, no las ramas descartadas.
 */
import i18n from "./i18n.js";

function nowIso() {
  return new Date().toISOString();
}

export function exportAsJson(messages, model) {
  const payload = { model, exported_at: nowIso(), messages };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `glyvex-chat-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function exportAsMarkdown(messages, model) {
  const date = new Date().toLocaleString();
  let md = `${i18n.t("export.title", { model: model || i18n.t("export.model"), date })}\n\n`;
  for (const m of messages) {
    const time = m.timestamp ? new Date(m.timestamp).toLocaleTimeString() : "";
    if (m.role === "user") {
      md += `${i18n.t("export.user")} ${time}\n`;
      if (m.attachments?.length) {
        md += `${i18n.t("export.attachments", { names: m.attachments.map((a) => a.filename).join(", ") })}\n\n`;
      }
      md += `${m.content}\n\n`;
    } else if (m.role === "assistant") {
      const tps = m.metrics?.tps ? `${m.metrics.tps.toFixed(1)} t/s` : "";
      const ttft = m.metrics?.ttft_ms ? `${Math.round(m.metrics.ttft_ms)}ms TTFT` : "";
      const suffix = [tps, ttft].filter(Boolean).join(" | ");
      md += `${i18n.t("export.assistant")} ${time}${suffix ? " | " + suffix : ""}\n${m.content}\n`;
      if (m.thinking) {
        md += `\n<details><summary>${i18n.t("export.reasoning")}</summary>\n\n${m.thinking}\n\n</details>\n`;
      }
      md += "\n";
    }
  }
  const blob = new Blob([md], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `glyvex-chat-${Date.now()}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

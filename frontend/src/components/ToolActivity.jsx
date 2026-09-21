import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Search, Globe, ChevronRight, Check, AlertTriangle, Loader2 } from "lucide-react";

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function iconFor(name) {
  return name === "fetch_url" ? Globe : Search;
}

/** Qué está haciendo la tool, en palabras del usuario y no del protocolo. */
function describe(entry, t) {
  if (entry.name === "web_search") {
    const query = entry.arguments?.query;
    return query ? t("tools.searching", { query }) : t("tools.searchingWeb");
  }
  if (entry.name === "fetch_url") {
    const url = entry.arguments?.url;
    return url ? t("tools.reading", { host: hostOf(url) }) : t("tools.readingPage");
  }
  return entry.name;
}

/**
 * Estado transitorio durante el streaming: las tools que el modelo está
 * usando ahora mismo. No es un mensaje del historial — desaparece cuando
 * llega la respuesta final y se reemplaza por ToolSummary.
 */
export function ToolActivityLive({ entries }) {
  const { t } = useTranslation();
  if (!entries || entries.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5">
      {entries.map((entry) => {
        const Icon = iconFor(entry.name);
        const done = entry.status !== "running";
        return (
          <div
            key={entry.id}
            className="inline-flex items-center gap-2 self-start max-w-full px-2.5 py-1.5 rounded-md border border-white/10 bg-black/30 text-xs text-glyvex-muted"
          >
            {done ? (
              entry.ok ? (
                <Check size={13} className="text-emerald-400 shrink-0" />
              ) : (
                <AlertTriangle size={13} className="text-amber-400 shrink-0" />
              )
            ) : (
              <Loader2 size={13} className="animate-spin shrink-0" />
            )}
            <Icon size={13} className="shrink-0" />
            <span className="truncate min-w-0">
              {done && entry.summary ? entry.summary : describe(entry, t)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Resumen colapsable de las tools que se usaron para producir una respuesta
 * ya terminada. Se guarda con el mensaje: sin esto, repreguntar sobre lo
 * buscado perdería las fuentes.
 */
export default function ToolSummary({ activity }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!activity || activity.length === 0) return null;

  const sources = activity.flatMap((entry) => entry.sources || []);
  const failed = activity.some((entry) => !entry.ok);

  return (
    <div className="rounded-md border border-white/10 bg-black/20 mb-2 overflow-hidden self-start max-w-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left text-glyvex-muted hover:text-glyvex-text"
      >
        <ChevronRight
          size={13}
          className={open ? "rotate-90 transition-transform" : "transition-transform"}
        />
        {failed ? (
          <AlertTriangle size={13} className="text-amber-400 shrink-0" />
        ) : (
          <Search size={13} className="shrink-0" />
        )}
        <span>
          {t("tools.searches", { count: activity.length })}
          {sources.length > 0 ? t("tools.sources", { count: sources.length }) : ""}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-2.5 space-y-2">
          {activity.map((entry) => {
            const Icon = iconFor(entry.name);
            return (
              <div key={entry.id} className="text-xs">
                <div className="flex items-center gap-1.5 text-glyvex-text">
                  <Icon size={12} className="shrink-0 text-glyvex-muted" />
                  <span>{describe(entry, t)}</span>
                </div>
                {entry.summary && (
                  <p className={"pl-5 " + (entry.ok ? "text-glyvex-muted" : "text-amber-400")}>
                    {entry.summary}
                  </p>
                )}
                {(entry.sources || []).length > 0 && (
                  <ul className="pl-5 mt-1 space-y-0.5">
                    {entry.sources.map((source) => (
                      <li key={source.url} className="truncate">
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-glyvex-accent hover:underline"
                          title={source.url}
                        >
                          {source.title || hostOf(source.url)}
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import { useTranslation } from "react-i18next";
import { Loader2, Plus, Search, Trash2, X } from "lucide-react";
import { inputClasses } from "../lib/styles.js";

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

export default function HistoryPanel({
  conversations, loading, search, currentId,
  onSearch, onSelect, onDelete, onNew, onClose,
}) {
  const { t } = useTranslation();
  return (
    <div className="absolute inset-y-0 left-0 w-[280px] z-20 flex flex-col bg-glyvex-card border-r border-white/10 shadow-xl shadow-black/40">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10">
        <span className="text-sm font-medium text-glyvex-text flex-1">{t("historyPanel.title")}</span>
        <button
          type="button"
          onClick={onNew}
          title={t("historyPanel.newTitle")}
          className="p-1 rounded-md text-glyvex-muted hover:text-glyvex-text hover:bg-white/5"
        >
          <Plus size={16} />
        </button>
        <button
          type="button"
          onClick={onClose}
          title={t("historyPanel.closeTitle")}
          className="p-1 rounded-md text-glyvex-muted hover:text-glyvex-text hover:bg-white/5"
        >
          <X size={16} />
        </button>
      </div>

      <div className="p-2 border-b border-white/10">
        <div className="relative">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-glyvex-muted" />
          <input
            className={inputClasses + " pl-7"}
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={t("historyPanel.searchPlaceholder")}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-6 text-xs text-glyvex-muted">
            <Loader2 size={14} className="animate-spin" /> {t("historyPanel.loading")}
          </div>
        )}

        {!loading && conversations.length === 0 && (
          <p className="text-xs text-glyvex-muted text-center py-6 px-2">
            {t("historyPanel.empty")}
          </p>
        )}

        {!loading &&
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={
                "group relative rounded-md border p-2 cursor-pointer " +
                (conv.id === currentId
                  ? "border-glyvex-accent/60 bg-glyvex-accent/10"
                  : "border-white/10 hover:bg-white/5")
              }
              onClick={() => onSelect(conv.id)}
            >
              <p className="text-sm text-glyvex-text truncate pr-6">
                {conv.title || t("historyPanel.untitled")}
              </p>
              <p className="text-xs text-glyvex-muted truncate">{conv.model_name || "—"}</p>
              <p className="text-xs text-glyvex-muted tabular-nums">
                {formatDate(conv.updated_at)} · {t("historyPanel.messages", { count: conv.message_count })}
              </p>
              <button
                type="button"
                title={t("historyPanel.deleteTitle")}
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(conv.id);
                }}
                className="absolute top-2 right-2 p-1 rounded-md text-glyvex-muted opacity-0 group-hover:opacity-100 hover:text-red-400 hover:bg-white/5"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Building2, ExternalLink, Globe, Star, X } from "lucide-react";
import GlyvexAiIcon from "./GlyvexAiIcon.jsx";

const WEB_SITE_URL = "https://ai-suite.glyvexgroup.com";
const GROUP_URL = "https://glyvexgroup.com";
const GITHUB_URL = "https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source";

export default function AboutModal({ onClose }) {
  const { t } = useTranslation();
  const [version, setVersion] = useState(null);

  useEffect(() => {
    import("@tauri-apps/api/app")
      .then(({ getVersion }) => getVersion())
      .then((v) => setVersion(v))
      .catch(() => {}); // fuera de Tauri (navegador): se oculta la versión
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={t("about.title")}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-glyvex-border bg-glyvex-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-glyvex-border">
          <h2 className="text-sm font-semibold">{t("about.title")}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("about.close")}
            className="p-1 rounded-md text-glyvex-bg-muted hover:text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex flex-col items-center px-4 pt-6 pb-4 gap-1.5">
          <GlyvexAiIcon size={72} />
          <div className="font-bold text-lg tracking-tight">
            <span style={{ color: "#0d9488" }}>GLYVEX</span>
            <span style={{ color: "#7c3aed" }}> AI Suite</span>
          </div>
          <div className="text-xs text-glyvex-bg-muted">{t("about.tagline")}</div>
          {version && <div className="text-xs text-glyvex-muted-2 mt-1">v{version}</div>}
        </div>
        <div className="px-4 pb-3 space-y-1">
          <a
            href={WEB_SITE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <Globe size={15} className="text-glyvex-bg-muted shrink-0" />
            {t("about.webSite")}
          </a>
          <a
            href={GROUP_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <Building2 size={15} className="text-glyvex-bg-muted shrink-0" />
            {t("about.group")}
          </a>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <ExternalLink size={15} className="text-glyvex-bg-muted shrink-0" />
            {t("about.github")}
          </a>
        </div>
        <div className="px-4 pb-4">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 w-full px-4 py-2.5 rounded-md text-sm font-medium bg-glyvex-accent text-white hover:opacity-90 transition-opacity"
          >
            <Star size={16} />
            {t("about.star")}
          </a>
        </div>
        <div className="px-4 py-3 border-t border-glyvex-border text-center text-xs text-glyvex-bg-muted">
          {t("about.license")}
        </div>
      </div>
    </div>
  );
}

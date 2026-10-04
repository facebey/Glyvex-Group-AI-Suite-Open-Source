import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Building2, Check, Download, ExternalLink as ExternalLinkIcon, Globe, RefreshCw, Star, X } from "lucide-react";
import { checkForUpdate, isTauri, relaunchApp, stopSidecar } from "../lib/tauri.js";
import ExternalLink from "./ExternalLink.jsx";
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

  const [update, setUpdate] = useState(null);
  const [updateState, setUpdateState] = useState("idle");
  const [progress, setProgress] = useState(0);
  const [updateError, setUpdateError] = useState(null);

  const handleCheck = async () => {
    setUpdateState("checking");
    try {
      const u = await checkForUpdate();
      if (u) {
        setUpdate(u);
        setUpdateState("available");
      } else {
        setUpdateState("up-to-date");
      }
    } catch (e) {
      setUpdateError(e instanceof Error ? e.message : String(e));
      setUpdateState("error");
    }
  };

  const handleInstall = async () => {
    setUpdateState("downloading");
    setProgress(0);
    let stage = "inicio";
    try {
      // El instalador NSIS no puede sobrescribir _internal/ del backend con
      // glyvex-backend.exe vivo (.dll/.pyd con lock): se detiene el sidecar
      // antes de lanzar el instalador. La SPA ya está cargada en memoria, así
      // que el update sigue aunque el backend caiga.
      stage = "stopSidecar";
      await stopSidecar();
      stage = "download";
      await update.downloadAndInstall((downloaded, total) => {
        if (total) setProgress(Math.round((downloaded / total) * 100));
      });
      stage = "relaunch";
      await relaunchApp();
    } catch (e) {
      const msg = e instanceof Error ? e.message || e.stack : String(e);
      setUpdateError(`[${stage}] ${msg}`);
      setUpdateState("error");
    }
  };

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
          <ExternalLink
            href={WEB_SITE_URL}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <Globe size={15} className="text-glyvex-bg-muted shrink-0" />
            {t("about.webSite")}
          </ExternalLink>
          <ExternalLink
            href={GROUP_URL}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <Building2 size={15} className="text-glyvex-bg-muted shrink-0" />
            {t("about.group")}
          </ExternalLink>
          <ExternalLink
            href={GITHUB_URL}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-glyvex-text hover:bg-glyvex-card-hi transition-colors"
          >
            <ExternalLinkIcon size={15} className="text-glyvex-bg-muted shrink-0" />
            {t("about.github")}
          </ExternalLink>
        </div>
        <div className="px-4 pb-4">
          <ExternalLink
            href={GITHUB_URL}
            className="flex items-center justify-center gap-2 w-full px-4 py-2.5 rounded-md text-sm font-medium bg-glyvex-accent text-white hover:opacity-90 transition-opacity"
          >
            <Star size={16} />
            {t("about.star")}
          </ExternalLink>
        </div>
        {isTauri() && (
          <div className="px-4 pb-3 space-y-2">
            {updateState === "idle" && (
              <button
                type="button"
                onClick={handleCheck}
                className="flex items-center justify-center gap-2 w-full px-3 py-2 rounded-md text-sm border border-glyvex-border hover:bg-glyvex-card-hi transition-colors"
              >
                <RefreshCw size={15} />
                {t("about.updateCheck")}
              </button>
            )}
            {updateState === "checking" && (
              <div className="flex items-center justify-center gap-2 px-3 py-2 text-sm text-glyvex-muted-2">
                <RefreshCw size={15} className="animate-spin" />
                {t("about.updateChecking")}
              </div>
            )}
            {updateState === "up-to-date" && (
              <div className="flex flex-col items-center gap-2">
                <div className="flex items-center gap-2 text-sm text-emerald-400">
                  <Check size={15} />
                  {t("about.updateUpToDate")}
                </div>
                <button type="button" onClick={handleCheck} className="text-xs text-glyvex-muted-2 underline hover:text-glyvex-text">
                  {t("about.updateCheck")}
                </button>
              </div>
            )}
            {updateState === "available" && (
              <div className="flex flex-col items-center gap-2">
                <div className="text-sm text-glyvex-text">{t("about.updateAvailable", { v: update?.version })}</div>
                <button
                  type="button"
                  onClick={handleInstall}
                  className="flex items-center justify-center gap-2 w-full px-3 py-2 rounded-md text-sm font-medium bg-glyvex-accent text-white hover:opacity-90 transition-opacity"
                >
                  <Download size={15} />
                  {t("about.updateInstall")}
                </button>
              </div>
            )}
            {updateState === "downloading" && (
              <div className="flex flex-col gap-2">
                <div className="text-sm text-glyvex-muted-2">{t("about.updateDownloading", { pct: progress })}</div>
                <div className="w-full h-1.5 rounded-full bg-glyvex-card-hi overflow-hidden">
                  <div className="h-full bg-glyvex-accent transition-all" style={{ width: `${progress}%` }} />
                </div>
              </div>
            )}
            {updateState === "error" && (
              <div className="flex flex-col items-center gap-2">
                <div className="text-sm text-red-400">{t("about.updateError")}</div>
                {updateError && (
                  <div className="text-[11px] text-red-300/80 break-all text-center max-h-16 overflow-auto">{updateError}</div>
                )}
                <button type="button" onClick={handleCheck} className="text-xs text-glyvex-muted-2 underline hover:text-glyvex-text">
                  {t("about.updateCheck")}
                </button>
              </div>
            )}
          </div>
        )}
        <div className="px-4 py-3 border-t border-glyvex-border text-center text-xs text-glyvex-bg-muted">
          {t("about.license")}
        </div>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import Section from "./ui/Section.jsx";
import { consumeSSE } from "../lib/sse.js";

// Estimación compartida con el onboarding (RT-10): motor base ~19 MB
// (cualquier GPU) + aceleración NVIDIA ~530 MB.
const SIZE_MB = 549;
const SIZE_MB_BASE = 19;

/**
 * Card "Runtime" de Config (RT-5). Mantiene el mismo orden de vistas que el
 * onboarding: experto > downloading > error > ready > skipped > action.
 */
export default function RuntimeSettings({ expertBinary }) {
  const { t } = useTranslation();
  const [runtime, setRuntime] = useState(null);
  const [dl, setDl] = useState(null);
  const [resetting, setResetting] = useState(false);
  const abortRef = useRef(null);

  async function fetchStatus() {
    try {
      const res = await fetch("/api/runtime/status");
      if (res.ok) setRuntime(await res.json());
    } catch {
      // backend reiniciando: se conserva el último estado conocido
    }
  }

  useEffect(() => {
    fetchStatus();
    return () => abortRef.current?.abort();
  }, []);

  // Descarga iniciada en el onboarding: si la page se abre a mitad de
  // descarga, se sigue con polling corto hasta que termine.
  useEffect(() => {
    if (runtime?.state !== "downloading") return undefined;
    const id = setInterval(fetchStatus, 2500);
    return () => clearInterval(id);
  }, [runtime?.state]);

  async function handleDownload() {
    const controller = new AbortController();
    abortRef.current = controller;
    setDl({ phase: "downloading", pct: 0, detail: "" });
    try {
      const res = await fetch("/api/runtime/download", { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || t("config.runtime.downloadError"));
      }
      await consumeSSE(res, controller.signal, (event) => {
        if (event.type === "progress") {
          setDl({ phase: "downloading", pct: event.pct, detail: event.detail });
        } else if (event.type === "done") {
          setDl(null);
          setRuntime(event.status);
        } else if (event.type === "error") {
          setDl({ phase: "error", error: event.message });
        }
      });
    } catch (err) {
      if (err.name !== "AbortError") setDl({ phase: "error", error: err.message });
    }
  }

  async function handleReinstall() {
    if (
      !window.confirm(
        t("config.runtime.reinstallConfirm", { pin: runtime?.pin, size: runtime?.size_mb ?? SIZE_MB })
      )
    ) return;
    setResetting(true);
    setDl(null);
    try {
      const res = await fetch("/api/runtime/reset", { method: "POST" });
      if (!res.ok) throw new Error();
      const data = await res.json();
      setRuntime(data.status);
    } catch {
      setDl({ phase: "error", error: t("config.runtime.resetError") });
    } finally {
      setResetting(false);
    }
  }

  if (!runtime) {
    return (
      <Section title={t("config.runtime.section")}>
        <p className="text-sm text-glyvex-muted">{t("config.runtime.loading")}</p>
      </Section>
    );
  }

  const isWindows = runtime.platform === "Windows";
  const gpuLabel = runtime.gpu === "cpu" ? "CPU" : runtime.gpu || "CPU";
  const isNvidia = runtime.gpu_family === "nvidia";
  const sizeMb = isNvidia ? SIZE_MB : SIZE_MB_BASE;

  let view;
  if (expertBinary) view = "expert";
  else if (dl?.phase === "downloading" || runtime.state === "downloading") view = "downloading";
  else if (dl?.phase === "error" || runtime.state === "error") view = "error";
  else if (runtime.state === "ready") view = "ready";
  else if (!isWindows) view = "skipped";
  else view = "action";

  return (
    <Section title={t("config.runtime.section")}>
      {view === "ready" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
            {t("config.runtime.ready", { pin: runtime.pin })}
          </div>
          <p className="text-xs text-glyvex-muted">
            {t("config.runtime.readySub", {
              pin: runtime.pin,
              size: runtime.size_mb ?? SIZE_MB,
              source: runtime.source,
            })}
          </p>
          {isNvidia && !runtime.accel && (
            <p className="text-xs text-amber-400">{t("config.runtime.degraded")}</p>
          )}
          <button
            type="button"
            onClick={handleReinstall}
            disabled={resetting}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card disabled:opacity-50"
          >
            <RotateCcw size={16} className={resetting ? "animate-spin" : ""} />
            {t("config.runtime.reinstall")}
          </button>
        </div>
      )}

      {view === "expert" && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sm font-medium">
            <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
            {t("config.runtime.expert")}
          </div>
          <p className="text-xs text-glyvex-muted">{t("config.runtime.expertSub")}</p>
        </div>
      )}

      {view === "downloading" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Loader2 size={18} className="text-glyvex-accent animate-spin shrink-0" />
            {t("config.runtime.downloading")}
          </div>
          <div className="h-2 rounded-full bg-glyvex-border overflow-hidden">
            <div
              className="h-full bg-glyvex-accent transition-all"
              style={{ width: `${dl?.pct ?? 0}%` }}
            />
          </div>
          {dl?.detail && (
            <p className="text-xs text-glyvex-muted">{Math.round(dl.pct)}% · {dl.detail}</p>
          )}
        </div>
      )}

      {view === "error" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-red-400">
            <AlertTriangle size={18} className="shrink-0" />
            {t("config.runtime.downloadError")}
          </div>
          <p className="text-xs text-red-400/80 break-all">{dl?.error || runtime.error}</p>
          <button
            type="button"
            onClick={handleDownload}
            className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25"
          >
            {t("config.runtime.retry")}
          </button>
        </div>
      )}

      {view === "action" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Cpu size={18} className="text-glyvex-accent shrink-0" />
            {t("config.runtime.gpuDetected", { gpu: gpuLabel })}
          </div>
          <p className="text-xs text-glyvex-muted">
            {t("config.runtime.size", { size: sizeMb })} · {t("config.runtime.tested", { pin: runtime.pin })}
            {!isNvidia && <> · {t("config.runtime.cpuNote")}</>}
          </p>
          <button
            type="button"
            onClick={handleDownload}
            className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-500"
          >
            <Download size={16} />
            {t("config.runtime.download")}
          </button>
        </div>
      )}

      {view === "skipped" && (
        <div className="flex items-start gap-2">
          <AlertTriangle size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <p className="text-sm text-glyvex-text">
            {t("config.runtime.unsupported")}
          </p>
        </div>
      )}
    </Section>
  );
}

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Loader2,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import Section from "./ui/Section.jsx";
import { consumeSSE } from "../lib/sse.js";
import { sizeDeltaMb, formatDeltaMb, runtimeSizeMb, accelOffered, accelChoiceFromSource } from "../lib/runtimeSize.js";

/**
 * Card "Runtime" de Config (RT-5). Mantiene el mismo orden de vistas que el
 * onboarding: experto > downloading > error > ready > skipped > action.
 */
export default function RuntimeSettings({ expertBinary }) {
  const { t } = useTranslation();
  const [runtime, setRuntime] = useState(null);
  const [dl, setDl] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [keepPrevious, setKeepPrevious] = useState(false);
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

  async function handleDownload(keep = false) {
    const controller = new AbortController();
    abortRef.current = controller;
    setDl({ phase: "downloading", pct: 0, detail: "" });
    try {
      const url = keep
        ? "/api/runtime/download?keep_previous=true"
        : "/api/runtime/download";
      const res = await fetch(url, { method: "POST" });
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
        t("config.runtime.reinstallConfirm", { pin: runtime?.pin, size: runtime?.size_mb ?? runtimeSizeMb(runtime?.gpu_family, selectedChoice) })
      )
    ) return;
    setDl(null);
    try {
      const res = await fetch("/api/runtime/reset", { method: "POST" });
      if (!res.ok) throw new Error();
      const data = await res.json();
      setRuntime(data.status);
    } catch {
      setDl({ phase: "error", error: t("config.runtime.resetError") });
      return;
    }
    await handleDownload(false);
  }

  async function handleAccelChange(choice) {
    // (RT-10 B2) Persiste la elección (config.runtime.accel). Cambiar la backend
    // de un runtime ya instalado requiere reinstalar: la nota ámbar lo indica y
    // el botón Reinstalar (reset + download) aplica la selección guardada sin
    // necesidad de parámetros en el download.
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ runtime: { accel: choice } }),
      });
      if (!res.ok) throw new Error();
      setRuntime((prev) => ({ ...prev, accel_choice: choice }));
    } catch {
      setDl({ phase: "error", error: t("config.runtime.accelSaveError") });
    }
  }

  async function handleUninstall() {
    if (!window.confirm(t("config.runtime.uninstallConfirm", { pin: runtime?.pin }))) return;
    setResetting(true);
    setDl(null);
    try {
      const res = await fetch("/api/runtime/reset", { method: "POST" });
      if (!res.ok) throw new Error();
      const data = await res.json();
      setRuntime(data.status);
      setKeepPrevious(false);
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

  const gpuLabel = runtime.gpu === "cpu" ? "CPU" : runtime.gpu || "CPU";
  const offerAccel = accelOffered(runtime.gpu_family);
  // (RT-10 B2) Familias con rutas de aceleración seleccionables (AMD): manda la
  // elección guardada; si no hay, la fuente instalada; si tampoco, el default
  // del catálogo. Con menos de 2 choices no se pinta selector (NVIDIA se fija
  // por CUDA; Intel/CPU no ofrecen aceleración gestionada).
  const accelChoices = runtime.accel_choices ?? [];
  const activeChoice = accelChoiceFromSource(runtime.accel_source);
  const selectedChoice = runtime.accel_choice ?? activeChoice ?? accelChoices[0] ?? null;
  const sizeMb = runtimeSizeMb(runtime.gpu_family, selectedChoice);
  const accelSelect = (
    <label className="flex flex-wrap items-center gap-2 text-xs text-glyvex-muted">
      <span>{t("config.runtime.accelTitle")}</span>
      <select
        value={selectedChoice ?? ""}
        onChange={(e) => handleAccelChange(e.target.value)}
        className="bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-2 py-1.5 text-xs text-glyvex-text"
      >
        {accelChoices.map((choice) => (
          <option key={choice} value={choice}>
            {t(`config.runtime.accelOptions.${choice}`)}
          </option>
        ))}
      </select>
    </label>
  );
  // Delta de tamaño de la actualización (RT-12): depende del checkbox de
  // rollback (reemplazo vs. conservación de la build anterior). Null → se
  // oculta (build anterior de tamaño desconocido).
  const prevBuild = runtime.previous_builds?.[0];
  const deltaText = formatDeltaMb(sizeDeltaMb(sizeMb, prevBuild?.size_mb, keepPrevious));

  let view;
  if (expertBinary) view = "expert";
  else if (dl?.phase === "downloading" || runtime.state === "downloading") view = "downloading";
  else if (dl?.phase === "error" || runtime.state === "error") view = "error";
  else if (runtime.state === "ready") view = "ready";
  else if (runtime.state === "outdated") view = "outdated";
  else if (runtime.state === "unsupported") view = "skipped";
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
              size: runtime.size_mb ?? runtimeSizeMb(runtime.gpu_family),
              source: runtime.source,
            })}
          </p>
          {offerAccel && !runtime.accel && (
            <p className="text-xs text-amber-400">{t("config.runtime.degraded")}</p>
          )}
          {runtime.cuda_note && (
            <p className="text-xs text-amber-400">
              {t("config.runtime.cudaNote", { cc: runtime.cuda_note })}
            </p>
          )}
          {activeChoice !== null && (
            <p className="text-xs text-glyvex-muted">
              {t("config.runtime.accelActive", {
                backend: t(`config.runtime.accelOptions.${activeChoice}`),
              })}
            </p>
          )}
          {accelChoices.length > 1 && accelSelect}
          {accelChoices.length > 1 && selectedChoice !== activeChoice && (
            <p className="text-xs text-amber-400">
              {t("config.runtime.accelReinstall", {
                choice: t(`config.runtime.accelOptions.${selectedChoice}`),
                size: sizeMb,
              })}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleReinstall}
              className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
            >
              <RotateCcw size={16} />
              {t("config.runtime.reinstall")}
            </button>
            <button
              type="button"
              onClick={handleUninstall}
              disabled={resetting}
              className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-red-400 hover:bg-glyvex-card disabled:opacity-50"
            >
              <Trash2 size={16} />
              {t("config.runtime.uninstall")}
            </button>
          </div>
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

      {view === "outdated" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Cpu size={18} className="text-amber-400 shrink-0" />
            {t("config.runtime.outdated", { pin: runtime.pin })}
          </div>
          {runtime.previous_builds?.length > 0 && (
            <p className="text-xs text-glyvex-muted">
              {t("config.runtime.outdatedSub", {
                prev: runtime.previous_builds[0].pin,
                prevSize: runtime.previous_builds[0].size_mb ?? "—",
                size: sizeMb,
              })}
            </p>
          )}
          {deltaText !== null && (
            <p className="text-xs text-glyvex-muted">
              {t("config.runtime.outdatedDelta", { delta: deltaText })}
            </p>
          )}
          <button
            type="button"
            onClick={() => handleDownload(keepPrevious)}
            className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-500"
          >
            <Download size={16} />
            {t("config.runtime.update")}
          </button>
          <label className="flex items-center gap-2 text-xs text-glyvex-muted cursor-pointer">
            <input
              type="checkbox"
              checked={keepPrevious}
              onChange={(e) => setKeepPrevious(e.target.checked)}
            />
            {t("config.runtime.keepPrevious")}
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleReinstall}
              className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
            >
              <RotateCcw size={16} />
              {t("config.runtime.reinstall")}
            </button>
            <button
              type="button"
              onClick={handleUninstall}
              disabled={resetting}
              className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-glyvex-border-soft text-glyvex-muted hover:text-red-400 hover:bg-glyvex-card disabled:opacity-50"
            >
              <Trash2 size={16} />
              {t("config.runtime.uninstall")}
            </button>
          </div>
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
            {!offerAccel && <> · {t("config.runtime.cpuNote")}</>}
          </p>
          {accelChoices.length > 1 && accelSelect}
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

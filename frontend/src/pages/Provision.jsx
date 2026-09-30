import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowLeft, CheckCircle2, Circle, Cpu, Download, Mic, Settings, Volume2 } from "lucide-react";
import { consumeSSE } from "../lib/sse.js";

// Estimaciones compartidas con RuntimeSettings: motor base ~19 MB
// (cualquier GPU) + aceleración NVIDIA ~530 MB; runtime STT ~162 MB.
const SIZE_MB = 549;
const SIZE_MB_BASE = 19;
const STT_SIZE_MB = 162;

export default function Provision() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [runtime, setRuntime] = useState(null);
  const [stt, setStt] = useState(null);
  const [models, setModels] = useState(null);
  const [tts, setTts] = useState(null);
  const [dl, setDl] = useState({ runtime: null, stt: null, model: null });
  const abortRef = useRef({});

  async function fetchAll() {
    const [r1, r2, r3, r4] = await Promise.allSettled([
      fetch("/api/runtime/status").then((r) => (r.ok ? r.json() : null)),
      fetch("/api/stt/runtime/status").then((r) => (r.ok ? r.json() : null)),
      fetch("/api/stt/runtime/models").then((r) => (r.ok ? r.json() : null)),
      fetch("/api/tts/status").then((r) => (r.ok ? r.json() : null)),
    ]);
    if (r1.value) setRuntime(r1.value);
    if (r2.value) setStt(r2.value);
    if (r3.value) setModels(r3.value);
    if (r4.value) setTts(r4.value);
  }

  const defaultModel = models?.models?.find((m) => m.name === models?.default);

  useEffect(() => {
    fetchAll();
    return () => Object.values(abortRef.current).forEach((c) => c?.abort());
  }, []);

  // Descargas que arrancaron en otra pestaña o relanzamiento: seguimiento por polling.
  const downloading = [runtime?.state, stt?.state, defaultModel?.state,
    dl.runtime?.phase, dl.stt?.phase, dl.model?.phase].some((s) => s === "downloading");
  useEffect(() => {
    if (!downloading) return undefined;
    const id = setInterval(fetchAll, 2500);
    return () => clearInterval(id);
  }, [downloading]);

  const setKind = (kind, value) => setDl((d) => ({ ...d, [kind]: value }));

  async function startDownload(kind, url, onDone) {
    const controller = new AbortController();
    abortRef.current[kind] = controller;
    setKind(kind, { phase: "downloading", pct: 0, detail: "" });
    try {
      const res = await fetch(url, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || t("provision.error"));
      }
      await consumeSSE(res, controller.signal, (event) => {
        if (event.type === "progress") {
          setKind(kind, { phase: "downloading", pct: event.pct, detail: event.detail });
        } else if (event.type === "done") {
          setKind(kind, null);
          onDone(event.status);
        } else if (event.type === "error") {
          setKind(kind, { phase: "error", error: event.message });
          fetchAll();
        }
      });
    } catch (err) {
      if (err.name !== "AbortError") setKind(kind, { phase: "error", error: err.message });
    }
  }

  const Progress = ({ s }) => (
    <div className="space-y-2">
      <div className="h-2 rounded-full bg-glyvex-border overflow-hidden">
        <div className="h-full bg-glyvex-accent transition-all" style={{ width: `${s.pct ?? 0}%` }} />
      </div>
      <p className="text-xs text-glyvex-muted">
        {Math.round(s.pct ?? 0)}%{s.detail ? ` · ${s.detail}` : ""}
      </p>
    </div>
  );

  const ErrorRow = ({ message, onRetry }) => (
    <div className="space-y-2">
      <p className="text-xs text-red-400/80 break-all">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25"
      >
        <Download size={14} />
        {t("provision.retry")}
      </button>
    </div>
  );

  const Step = ({ icon: Icon, title, badge, done, children }) => (
    <div className="px-4 py-3 rounded-md border border-glyvex-border bg-glyvex-card space-y-2">
      <div className="flex items-center gap-2">
        <Icon size={18} className="text-glyvex-accent shrink-0" />
        <p className="text-sm font-medium flex-1">{title}</p>
        <span className="text-xs text-glyvex-muted shrink-0">{badge}</span>
        {done ? (
          <CheckCircle2 size={16} className="text-emerald-400 shrink-0" />
        ) : (
          <Circle size={14} className="text-glyvex-muted shrink-0" />
        )}
      </div>
      {children}
    </div>
  );

  const isWindows = runtime ? runtime.platform === "Windows" : true;
  const sizeMb = runtime?.gpu_family === "nvidia" ? SIZE_MB : SIZE_MB_BASE;

  // Paso 1 — runtime llama.cpp (obligatorio)
  let runtimeView;
  let runtimeDone = false;
  if (dl.runtime?.phase === "downloading" || runtime?.state === "downloading") {
    runtimeView = <Progress s={dl.runtime || { pct: 0 }} />;
  } else if (dl.runtime?.phase === "error" || runtime?.state === "error") {
    runtimeView = (
      <ErrorRow
        message={dl.runtime?.error || runtime?.error}
        onRetry={() => startDownload("runtime", "/api/runtime/download", setRuntime)}
      />
    );
  } else if (runtime?.state === "ready") {
    runtimeDone = true;
    runtimeView = (
      <p className="text-xs text-glyvex-muted">
        {t("config.runtime.ready", { pin: runtime.pin })} ·{" "}
        {t("config.runtime.readySub", { pin: runtime.pin, size: runtime.size_mb ?? sizeMb, source: runtime.source })}
      </p>
    );
  } else if (!isWindows) {
    runtimeView = <p className="text-xs text-amber-400">{t("config.runtime.unsupported")}</p>;
  } else {
    runtimeView = (
      <div className="space-y-2">
        <p className="text-xs text-glyvex-muted">
          {t("config.runtime.gpuDetected", { gpu: runtime?.gpu || "CPU" })} ·{" "}
          {t("config.runtime.size", { size: sizeMb })}
        </p>
        <button
          type="button"
          onClick={() => startDownload("runtime", "/api/runtime/download", setRuntime)}
          className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-500"
        >
          <Download size={16} />
          {t("config.runtime.download")}
        </button>
      </div>
    );
  }

  // Paso 2 — STT whisper.cpp + modelo (opcional)
  let sttView;
  let sttDone = false;
  if (dl.stt?.phase === "downloading" || stt?.state === "downloading") {
    sttView = <Progress s={dl.stt || { pct: 0 }} />;
  } else if (dl.stt?.phase === "error" || stt?.state === "error") {
    sttView = (
      <ErrorRow
        message={dl.stt?.error || stt?.error}
        onRetry={() => startDownload("stt", "/api/stt/runtime/download", setStt)}
      />
    );
  } else if (stt?.state === "ready") {
    sttDone = defaultModel?.state === "ready";
    if (dl.model?.phase === "downloading" || defaultModel?.state === "downloading") {
      sttView = <Progress s={dl.model || { pct: 0 }} />;
    } else if (dl.model?.phase === "error" || defaultModel?.state === "error") {
      sttView = (
        <ErrorRow
          message={dl.model?.error || defaultModel?.error}
          onRetry={() =>
            startDownload("model", `/api/stt/runtime/models/${defaultModel?.name}/download`, setModels)
          }
        />
      );
    } else if (defaultModel?.state === "ready") {
      sttView = (
        <p className="text-xs text-glyvex-muted">
          {t("provision.sttReady")} · {t("provision.modelReady")}
        </p>
      );
    } else {
      sttView = (
        <div className="space-y-2">
          <p className="text-xs text-glyvex-muted">
            {t("provision.model", { name: defaultModel?.name, label: defaultModel?.label })}
          </p>
          <button
            type="button"
            onClick={() =>
              startDownload("model", `/api/stt/runtime/models/${defaultModel?.name}/download`, setModels)
            }
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25"
          >
            <Download size={14} />
            {t("provision.modelDownload", { size: Math.round(defaultModel?.size_mb ?? 0) })}
          </button>
        </div>
      );
    }
  } else if (!isWindows) {
    sttView = <p className="text-xs text-amber-400">{t("config.runtime.unsupported")}</p>;
  } else {
    sttView = (
      <div className="space-y-2">
        <p className="text-xs text-glyvex-muted">{t("provision.sttSize", { size: STT_SIZE_MB })}</p>
        <button
          type="button"
          onClick={() => startDownload("stt", "/api/stt/runtime/download", setStt)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25"
        >
          <Download size={14} />
          {t("provision.sttDownload")}
        </button>
      </div>
    );
  }

  // Paso 3 — TTS SAPI (verificación, sin descargas)
  let ttsView;
  let ttsDone = false;
  if (tts?.platform_ok) {
    ttsDone = true;
    ttsView = (
      <div className="space-y-2">
        <p className="text-xs text-glyvex-muted">
          {t("provision.ttsVoices", { count: tts.voices?.length ?? 0, voice: tts.voice || "—" })}
        </p>
        <div className="space-y-1.5 pt-2 border-t border-glyvex-border">
          <p className="text-xs text-glyvex-muted">{t("provision.ttsNeuralHint")}</p>
          <button
            type="button"
            onClick={() => navigate("/config")}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25"
          >
            <Settings size={14} />
            {t("provision.ttsNeuralConfig")}
          </button>
        </div>
      </div>
    );
  } else if (tts) {
    ttsView = (
      <p className="text-xs text-amber-400">{t("provision.ttsUnavailable", { reason: tts.reason || "—" })}</p>
    );
  } else {
    ttsView = <p className="text-xs text-glyvex-muted">{t("config.runtime.loading")}</p>;
  }

  const canEnter = runtime ? runtime.state === "ready" || !isWindows : false;

  return (
    <div className="max-w-lg mx-auto space-y-5">
      <button
        type="button"
        onClick={() => navigate("/")}
        className="flex items-center gap-2 text-sm text-glyvex-muted hover:text-glyvex-text"
      >
        <ArrowLeft size={16} />
        {t("provision.back")}
      </button>
      <div>
        <h1 className="text-xl font-semibold">{t("provision.title")}</h1>
        <p className="text-sm text-glyvex-muted mt-1">{t("provision.subtitle")}</p>
      </div>
      <div className="space-y-3">
        <Step icon={Cpu} title={t("provision.stepRuntime")} badge={t("provision.required")} done={runtimeDone}>
          {runtimeView}
        </Step>
        <Step icon={Mic} title={t("provision.stepStt")} badge={t("provision.optional")} done={sttDone}>
          {sttView}
        </Step>
        <Step icon={Volume2} title={t("provision.stepTts")} badge={t("provision.optional")} done={ttsDone}>
          {ttsView}
        </Step>
      </div>
      <div className="space-y-2">
        <button
          type="button"
          onClick={() => navigate("/")}
          disabled={!canEnter}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-md text-sm font-semibold bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {!canEnter && <AlertTriangle size={16} />}
          {t("provision.enter")}
        </button>
        {!canEnter && <p className="text-xs text-glyvex-muted text-center">{t("provision.enterHint")}</p>}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic, Loader2, Download, Languages } from "lucide-react";
import {
  useSpeechRecognition,
  browserSpeechSupported,
} from "../hooks/useSpeechRecognition.js";
import { useAudioRecorder, mediaRecorderSupported } from "../hooks/useAudioRecorder.js";

/** Debajo de esto, soltar el botón cuenta como click y queda grabando. */
const HOLD_THRESHOLD_MS = 400;

/** Subconjunto para el composer: override por dictado (STT-4). El listado
 *  completo de idiomas sigue en Configuración → Voz. Nombres nativos. */
const COMPOSER_LANGUAGES = [
  { id: "es", label: "Español" },
  { id: "en", label: "Inglés" },
  { id: "pt", label: "Portugués" },
  { id: "fr", label: "Francés" },
  { id: "de", label: "Alemán" },
];

/** Barras animadas mientras se graba. */
function LevelBars() {
  return (
    <span className="flex items-end gap-[2px] h-3.5" aria-hidden="true">
      {[0, 140, 70, 210].map((delay, index) => (
        <span
          key={index}
          className="w-[2px] bg-current rounded-full animate-pulse"
          style={{ height: `${[40, 100, 70, 55][index]}%`, animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}

/**
 * Decide qué motor de dictado usar.
 *
 * Con engine "auto" se prefiere whisper.cpp si está completo (binario +
 * modelo descargados): es el único que funciona dentro del WebView de Tauri
 * sin nada más que bajar. Si no, el del navegador cuando existe (cero
 * latencia), y si no faster-whisper.
 */
function resolveEngine(configured, status) {
  const browserOk = browserSpeechSupported();
  const cpp = status?.whispercpp || {};
  const cppReady = Boolean(cpp.installed && cpp.model_ready);
  const whisperInstalled = Boolean(status?.whisper?.installed);

  if (configured === "browser") return browserOk ? "browser" : "none";
  if (configured === "whispercpp") return cppReady ? "whispercpp" : "none";
  if (configured === "whisper") return whisperInstalled ? "whisper" : "none";
  if (cppReady) return "whispercpp";
  if (browserOk) return "browser";
  if (whisperInstalled) return "whisper";
  return "none";
}

export default function MicButton({ status, onTranscript, onInterim, onError, disabled }) {
  const { t } = useTranslation();
  const [warming, setWarming] = useState(false);
  const pressStartRef = useRef(0);
  const heldRef = useRef(false);

  // Override por dictado (STT-4): no toca la configuración, solo este chat.
  const [langOverride, setLangOverride] = useState("");
  // STT-5: el default viene de stt.translate_english (vía /api/stt/status)
  // hasta que el usuario toque el toggle.
  const [translate, setTranslate] = useState(false);
  const translateTouchedRef = useRef(false);
  useEffect(() => {
    if (status && !translateTouchedRef.current) {
      setTranslate(Boolean(status.translate_english));
    }
  }, [status]);

  const configured = status?.engine || "auto";
  const whisper = status?.whisper || {};
  const whispercpp = status?.whispercpp || {};
  const engine = resolveEngine(configured, status);
  // La traducción nativa solo existe en los motores locales; el del
  // navegador transcribe directo al idioma elegido, sin paso extra.
  const translateActive = translate && engine !== "browser";

  const speech = useSpeechRecognition({
    language: langOverride || status?.language || "es-AR",
    onError,
  });
  const recorder = useAudioRecorder({ onError });

  const recording = engine === "browser" ? speech.recording : recorder.recording;
  const busy =
    (engine === "whisper" || engine === "whispercpp") &&
    (recorder.transcribing || warming);

  // El dictado del navegador va llegando de a pedazos: el texto confirmado se
  // entrega y el provisorio se muestra aparte, para que el textarea no quede
  // con palabras que el motor todavía puede corregir.
  const lastFinalRef = useRef("");
  useEffect(() => {
    if (engine !== "browser") return;
    if (speech.finalText && speech.finalText !== lastFinalRef.current) {
      const addition = speech.finalText.slice(lastFinalRef.current.length).trim();
      lastFinalRef.current = speech.finalText;
      if (addition) onTranscript(addition);
    }
  }, [engine, speech.finalText, onTranscript]);

  useEffect(() => {
    if (engine !== "browser") return;
    onInterim?.(speech.interimText);
  }, [engine, speech.interimText, onInterim]);

  useEffect(() => {
    if (!speech.recording) lastFinalRef.current = "";
  }, [speech.recording]);

  const startRecording = useCallback(async () => {
    if (engine === "browser") {
      speech.start();
      return;
    }
    if (engine === "whispercpp") {
      // whisper-cli decodifica solo WAV: el audio se captura a 16 kHz mono
      // y se arma el WAV en JS (sin paso intermedio de webm/opus).
      await recorder.start({ format: "wav" });
      return;
    }
    if (engine === "whisper") {
      // El primer uso carga (y si hace falta descarga) el modelo. Se dispara
      // antes de grabar para que la espera no quede después de hablar.
      if (!whisper.loaded) {
        setWarming(true);
        try {
          const res = await fetch("/api/stt/warmup", { method: "POST" });
          if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            onError?.(detail.detail || t("mic.loadModel"));
            return;
          }
        } catch {
          onError?.(t("mic.loadModel"));
          return;
        } finally {
          setWarming(false);
        }
      }
      await recorder.start();
    }
  }, [engine, speech, recorder, whisper.loaded, onError]);

  const stopRecording = useCallback(async () => {
    if (engine === "browser") {
      speech.stop();
      onInterim?.("");
      return;
    }
    const blob = await recorder.stop();
    if (!blob) return;
    const text = await recorder.transcribe(blob, langOverride, translateActive);
    if (text) onTranscript(text);
  }, [engine, speech, recorder, langOverride, translateActive, onTranscript, onInterim]);

  // Push-to-talk híbrido: mantener apretado graba mientras lo sostenés;
  // un click corto lo deja grabando hasta el siguiente click.
  function handlePointerDown(event) {
    if (disabled || engine === "none" || busy) return;
    event.preventDefault();
    if (recording) {
      stopRecording();
      heldRef.current = false;
      return;
    }
    pressStartRef.current = Date.now();
    heldRef.current = true;
    startRecording();
  }

  function handlePointerUp() {
    if (!heldRef.current) return;
    heldRef.current = false;
    const elapsed = Date.now() - pressStartRef.current;
    if (elapsed >= HOLD_THRESHOLD_MS) stopRecording();
  }

  if (engine === "none") {
    // En el bundle empaquetado (STT-2) el fallback real de "auto" es el
    // runtime de whisper.cpp: el tooltip debe mostrar su razón (estado +
    // botón de descarga), no la del faster-whisper, que ahí no existe.
    const cppReason = (configured === "whispercpp" || configured === "auto") && whispercpp.reason;
    const reason = !mediaRecorderSupported()
      ? t("mic.noRecording")
      : !window.isSecureContext
        ? t("mic.insecure")
        : cppReason
          ? cppReason
          : whisper.reason || t("mic.noEngine");
    return (
      <button
        type="button"
        disabled
        title={reason}
        className="flex items-center h-9 px-2 rounded-md border border-glyvex-border-soft text-glyvex-bg-muted opacity-40 shrink-0"
      >
        <Mic size={16} />
      </button>
    );
  }

  const baseTitle = recording
    ? t("mic.stopDictation")
    : engine === "whispercpp"
      ? t("mic.dictateWhisperCpp", { model: whispercpp.model })
      : engine === "whisper"
        ? whisper.loaded || whisper.cached
          ? t("mic.dictateWhisperLocal", { model: whisper.model })
          : t("mic.dictateWhisperDownload", { model: whisper.model, mb: whisper.download_mb || "?" })
        : t("mic.dictateBrowser");
  const title = baseTitle + (translateActive ? ` · ${t("mic.translateSuffix")}` : "");

  return (
    <>
      <select
        value={langOverride}
        onChange={(e) => setLangOverride(e.target.value)}
        disabled={translateActive}
        title={translateActive ? t("mic.langDisabledTranslate") : t("mic.langTitle")}
        className="h-9 rounded-md border border-glyvex-border-soft bg-glyvex-card px-1.5 text-xs text-glyvex-text disabled:opacity-40 shrink-0"
      >
        <option value="" className="bg-glyvex-card text-glyvex-text">{t("mic.langAuto")}</option>
        {COMPOSER_LANGUAGES.map((l) => (
          <option key={l.id} value={l.id} className="bg-glyvex-card text-glyvex-text">
            {l.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
        disabled={disabled || busy}
        aria-pressed={recording}
        title={title}
        className={
          "flex items-center gap-1.5 h-9 px-2 rounded-md border shrink-0 disabled:opacity-50 " +
          (recording
            ? "border-red-500/50 bg-red-500/10 text-red-400"
            : "border-glyvex-border-soft text-glyvex-bg-muted hover:text-glyvex-bg-text hover:bg-glyvex-veil-disabled")
        }
      >
        {busy ? (
          <Loader2 size={16} className="animate-spin" />
        ) : recording ? (
          <>
            <Mic size={16} className="animate-pulse" />
            <LevelBars />
          </>
        ) : whisper.installed && !whisper.cached && engine === "whisper" ? (
          <>
            <Mic size={16} />
            <Download size={11} className="opacity-70" />
          </>
        ) : (
          <Mic size={16} />
        )}
      </button>
      <button
        type="button"
        onClick={() => {
          if (engine === "browser") return;
          translateTouchedRef.current = true;
          setTranslate((v) => !v);
        }}
        disabled={engine === "browser"}
        aria-pressed={translateActive}
        title={
          engine === "browser"
            ? t("mic.translateBrowserUnavailable")
            : translateActive
              ? t("mic.translateActive")
              : t("mic.translateToggle")
        }
        className={
          "flex items-center h-9 px-2 rounded-md border shrink-0 disabled:opacity-40 " +
          (translateActive
            ? "border-sky-500/40 bg-sky-500/10 text-sky-400"
            : "border-glyvex-border-soft text-glyvex-bg-muted hover:text-glyvex-bg-text hover:bg-glyvex-veil-disabled")
        }
      >
        <Languages size={16} />
      </button>
    </>
  );
}

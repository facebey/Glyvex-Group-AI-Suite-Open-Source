import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Loader2, Download } from "lucide-react";
import {
  useSpeechRecognition,
  browserSpeechSupported,
} from "../hooks/useSpeechRecognition.js";
import { useAudioRecorder, mediaRecorderSupported } from "../hooks/useAudioRecorder.js";

/** Debajo de esto, soltar el botón cuenta como click y queda grabando. */
const HOLD_THRESHOLD_MS = 400;

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
 * Con engine "auto" se prefiere el del navegador cuando existe, porque no
 * tiene latencia de descarga ni de modelo. Dentro del WebView de Tauri la
 * Web Speech API no está disponible, así que ahí cae a Whisper solo.
 */
function resolveEngine(configured, whisperInstalled) {
  const browserOk = browserSpeechSupported();
  if (configured === "browser") return browserOk ? "browser" : "none";
  if (configured === "whisper") return whisperInstalled ? "whisper" : "none";
  if (browserOk) return "browser";
  if (whisperInstalled) return "whisper";
  return "none";
}

export default function MicButton({ status, onTranscript, onInterim, onError, disabled }) {
  const [warming, setWarming] = useState(false);
  const pressStartRef = useRef(0);
  const heldRef = useRef(false);

  const configured = status?.engine || "auto";
  const whisper = status?.whisper || {};
  const engine = resolveEngine(configured, Boolean(whisper.installed));

  const speech = useSpeechRecognition({ language: status?.language || "es-AR", onError });
  const recorder = useAudioRecorder({ onError });

  const recording = engine === "browser" ? speech.recording : recorder.recording;
  const busy = engine === "whisper" && (recorder.transcribing || warming);

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
    if (engine === "whisper") {
      // El primer uso carga (y si hace falta descarga) el modelo. Se dispara
      // antes de grabar para que la espera no quede después de hablar.
      if (!whisper.loaded) {
        setWarming(true);
        try {
          const res = await fetch("/api/stt/warmup", { method: "POST" });
          if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            onError?.(detail.detail || "No se pudo cargar el modelo de voz.");
            return;
          }
        } catch {
          onError?.("No se pudo cargar el modelo de voz.");
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
    const text = await recorder.transcribe(blob);
    if (text) onTranscript(text);
  }, [engine, speech, recorder, onTranscript, onInterim]);

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
    const reason = !mediaRecorderSupported()
      ? "Este navegador no puede grabar audio."
      : !window.isSecureContext
        ? "El micrófono necesita https o localhost. Estás entrando por IP."
        : whisper.reason || "No hay ningún motor de dictado disponible.";
    return (
      <button
        type="button"
        disabled
        title={reason}
        className="flex items-center h-9 px-2 rounded-md border border-white/10 text-glyvex-muted opacity-40 shrink-0"
      >
        <Mic size={16} />
      </button>
    );
  }

  const title = recording
    ? "Soltá o hacé click para terminar el dictado"
    : engine === "whisper"
      ? whisper.loaded || whisper.cached
        ? `Dictar (Whisper ${whisper.model}, local)`
        : `Dictar (Whisper ${whisper.model} — el primer uso descarga ~${whisper.download_mb || "?"} MB)`
      : "Dictar (motor del navegador — el audio sale a internet)";

  return (
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
          : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30")
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
  );
}

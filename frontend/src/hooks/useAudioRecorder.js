import { useCallback, useEffect, useRef, useState } from "react";

/** El primero que el navegador acepte. Chrome da webm/opus, Safari mp4. */
const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

function pickMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  for (const type of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

export const mediaRecorderSupported = () =>
  typeof MediaRecorder !== "undefined" &&
  typeof navigator !== "undefined" &&
  Boolean(navigator.mediaDevices?.getUserMedia);

function extensionFor(mimeType) {
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("mp4")) return "mp4";
  return "webm";
}

/**
 * Graba audio del micrófono y lo entrega como blob.
 *
 * El stream se corta siempre al terminar (el `stop()` del recorder no libera
 * los tracks por sí solo, y si no se liberan el indicador de micrófono en uso
 * queda prendido en la barra del navegador).
 */
export function useAudioRecorder({ onError } = {}) {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const resolveRef = useRef(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (recorderRef.current) return;

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (err) {
      const message =
        err.name === "NotAllowedError"
          ? "Falta permiso para usar el micrófono."
          : err.name === "NotFoundError"
            ? "No se encontró ningún micrófono."
            : "No se pudo acceder al micrófono.";
      onErrorRef.current?.(message);
      return;
    }

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
    };

    recorder.onstop = () => {
      const type = recorder.mimeType || mimeType || "audio/webm";
      const blob = new Blob(chunksRef.current, { type });
      chunksRef.current = [];
      recorderRef.current = null;
      releaseStream();
      setRecording(false);
      resolveRef.current?.(blob);
      resolveRef.current = null;
    };

    streamRef.current = stream;
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  }, [releaseStream]);

  /** Detiene la grabación y resuelve con el blob completo. */
  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder) return Promise.resolve(null);
    return new Promise((resolve) => {
      resolveRef.current = resolve;
      try {
        recorder.stop();
      } catch {
        resolve(null);
      }
    });
  }, []);

  const cancel = useCallback(() => {
    const recorder = recorderRef.current;
    resolveRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
    try {
      recorder?.stop();
    } catch {
      // ya estaba detenido
    }
    releaseStream();
    setRecording(false);
  }, [releaseStream]);

  /** Manda el blob al backend y devuelve el texto transcripto. */
  const transcribe = useCallback(async (blob, language = "") => {
    if (!blob || blob.size === 0) return "";
    setTranscribing(true);
    try {
      const form = new FormData();
      form.append("audio", blob, `dictado.${extensionFor(blob.type)}`);
      if (language) form.append("language", language);

      const res = await fetch("/api/stt/transcribe", { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `El servidor respondió ${res.status}`);
      }
      const data = await res.json();
      return data.text || "";
    } catch (err) {
      onErrorRef.current?.(String(err.message || err));
      return "";
    } finally {
      setTranscribing(false);
    }
  }, []);

  useEffect(() => {
    return () => {
      try {
        recorderRef.current?.stop();
      } catch {
        // nada que limpiar
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  return { recording, transcribing, start, stop, cancel, transcribe };
}

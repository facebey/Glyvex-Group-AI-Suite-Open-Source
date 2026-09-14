import { useCallback, useEffect, useRef, useState } from "react";

/** El constructor está sin prefijar en algunos motores y con webkit- en otros. */
function getRecognitionCtor() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export const browserSpeechSupported = () => Boolean(getRecognitionCtor());

const ERROR_MESSAGES = {
  "not-allowed": "Falta permiso para usar el micrófono.",
  "service-not-allowed": "El navegador bloqueó el servicio de reconocimiento.",
  "no-speech": "No se escuchó nada.",
  "audio-capture": "No se encontró ningún micrófono.",
  network: "El servicio de reconocimiento no está disponible sin conexión.",
  aborted: null, // cancelación del usuario, no es error
};

/**
 * Dictado con la Web Speech API.
 *
 * Los resultados llegan en dos tandas: `interim` mientras el motor todavía
 * está decidiendo, y `final` cuando se compromete. Se acumulan los finales y
 * se expone el interim aparte, para que el textarea muestre el dictado en
 * curso sin que quede pegado texto que el motor después corrige.
 *
 * Ojo: en Chrome esto NO es local. El audio sale hacia los servidores de
 * Google. Para dictado sin salida a internet está el motor `whisper`.
 */
export function useSpeechRecognition({ language = "es-AR", onError } = {}) {
  const [recording, setRecording] = useState(false);
  const [finalText, setFinalText] = useState("");
  const [interimText, setInterimText] = useState("");

  const recognitionRef = useRef(null);
  const finalRef = useRef("");
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const stop = useCallback(() => {
    const recognition = recognitionRef.current;
    if (!recognition) return;
    try {
      recognition.stop();
    } catch {
      // ya estaba detenido
    }
  }, []);

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor || recognitionRef.current) return;

    const recognition = new Ctor();
    recognition.lang = language;
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript || "";
        if (result.isFinal) {
          finalRef.current = `${finalRef.current} ${transcript}`.trim();
        } else {
          interim += transcript;
        }
      }
      setFinalText(finalRef.current);
      setInterimText(interim);
    };

    recognition.onerror = (event) => {
      const message = ERROR_MESSAGES[event.error];
      if (message !== null) {
        onErrorRef.current?.(message || `Error de reconocimiento: ${event.error}`);
      }
    };

    recognition.onend = () => {
      recognitionRef.current = null;
      setRecording(false);
      setInterimText("");
    };

    recognitionRef.current = recognition;
    finalRef.current = "";
    setFinalText("");
    setInterimText("");

    try {
      recognition.start();
      setRecording(true);
    } catch (err) {
      recognitionRef.current = null;
      onErrorRef.current?.("No se pudo iniciar el dictado.");
    }
  }, [language]);

  useEffect(() => {
    return () => {
      try {
        recognitionRef.current?.abort();
      } catch {
        // nada que limpiar
      }
    };
  }, []);

  return { recording, start, stop, finalText, interimText };
}

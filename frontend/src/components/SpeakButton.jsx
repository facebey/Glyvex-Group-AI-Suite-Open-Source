import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Volume2, VolumeX } from "lucide-react";
import { toSpokenText } from "../lib/tts.js";

/**
 * Lectura en voz alta de la respuesta (SAPI, 100% local). Pide el WAV a
 * /api/tts/speak, lo guarda en un object URL y lo reproduce con <audio>; al
 * terminar (o si se detiene a mano) el URL se libera para no filtrar memoria.
 */
export default function SpeakButton({ content, onError }) {
  const { t } = useTranslation();
  const [state, setState] = useState("idle"); // idle | loading | playing
  const audioRef = useRef(null);
  const urlRef = useRef(null);

  function stop() {
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.pause();
    }
    audioRef.current = null;
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setState("idle");
  }

  useEffect(() => stop, []);

  async function toggle() {
    if (state === "playing") {
      stop();
      return;
    }
    if (state === "loading") return;

    const text = toSpokenText(content);
    if (!text) return;

    setState("loading");
    try {
      const res = await fetch("/api/tts/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      urlRef.current = url;
      audio.onended = () => {
        URL.revokeObjectURL(url);
        urlRef.current = null;
        audioRef.current = null;
        setState("idle");
      };
      setState("playing");
      await audio.play();
    } catch (err) {
      stop();
      onError?.(err?.message || String(err));
    }
  }

  const Icon = state === "loading" ? Loader2 : state === "playing" ? VolumeX : Volume2;
  const title =
    state === "playing"
      ? t("message.speakStopTitle")
      : state === "loading"
        ? t("message.speakLoadingTitle")
        : t("message.speakTitle");

  return (
    <button
      type="button"
      onClick={toggle}
      title={title}
      disabled={state === "loading"}
      className="p-0.5 rounded text-glyvex-bg-muted hover:text-glyvex-bg-text hover:bg-glyvex-veil-strong disabled:opacity-50"
    >
      <Icon size={12} className={state === "loading" ? "animate-spin" : state === "playing" ? "text-glyvex-accent" : ""} />
    </button>
  );
}

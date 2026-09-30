/**
 * tts.js — Preparación del texto del chat para la lectura en voz alta (SAPI).
 *
 * SAPI lee lo que le pasás tal cual: un bloque de código o los markers de
 * markdown se escucharían como basura. Por eso `toSpokenText` lo limpia antes
 * de mandarlo a /api/tts/speak: los bloques de código se sacan del todo (no
 * se lee código en voz alta), el inline code queda como texto plano y los
 * markers (#, *, |, ...) desaparecen.
 */

const MAX_CHARS = 3000; // espeja MAX_TEXT_CHARS del backend

export function toSpokenText(markdown, maxChars = MAX_CHARS) {
  if (!markdown) return "";
  const text = String(markdown)
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, " ")
    .replace(/\|/g, " ")
    .replace(/[*_~>]/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
  return text.slice(0, maxChars);
}

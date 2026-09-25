import { useEffect, useRef } from "react";

/**
 * Lluvia digital de fondo para el tema "matrix".
 *
 * - Canvas fijo detrás del contenido (los paneles son vidrio semitransparente).
 * - ~24 fps con throttle; se pausa con la pestaña oculta.
 * - Con prefers-reduced-motion dibuja un cuadro estático y se detiene.
 * - Easter egg: de vez en cuando una columna deletrea "GLYVEX".
 */
const GLYPHS =
  "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ" +
  "0123456789" +
  "ΞΣΛΔ<>/*+=:;|#$%";
const WORD = "GLYVEX";
const FONT_PX = 16;
const FPS = 24;

function randGlyph() {
  return GLYPHS[(Math.random() * GLYPHS.length) | 0];
}

export default function MatrixRain({ opacity = 0.55 }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d", { alpha: false });
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    let width = 0;
    let height = 0;
    let cols = [];
    let raf = 0;
    let last = 0;

    const rows = () => Math.ceil(height / FONT_PX);

    function newColumn(startAbove = true) {
      return {
        // y en FILAS (no píxeles)
        y: startAbove ? -Math.random() * rows() * 0.6 : Math.random() * rows(),
        speed: 0.45 + Math.random() * 0.9,          // filas por frame
        word: Math.random() < 0.035 ? 0 : -1,       // índice en WORD, -1 = sin palabra
        prev: "",
      };
    }

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#000301";
      ctx.fillRect(0, 0, width, height);
      ctx.font = `${FONT_PX}px 'JetBrains Mono', ui-monospace, monospace`;
      ctx.textBaseline = "top";
      const n = Math.ceil(width / FONT_PX);
      cols = Array.from({ length: n }, () => newColumn(false));
    }

    function frame() {
      // estela: velo negro-verdoso translúcido
      ctx.fillStyle = "rgba(0, 3, 1, 0.085)";
      ctx.fillRect(0, 0, width, height);

      for (let i = 0; i < cols.length; i++) {
        const c = cols[i];
        const x = i * FONT_PX;
        const row = Math.floor(c.y);
        const yPx = row * FONT_PX;

        // glifo previo pasa a verde normal (la cabeza es casi blanca)
        if (c.prev) {
          ctx.fillStyle = "#00e05a";
          ctx.fillText(c.prev, x, yPx - FONT_PX);
        }

        let ch;
        if (c.word >= 0 && c.word < WORD.length) {
          ch = WORD[c.word];
        } else {
          ch = randGlyph();
        }

        const isWord = c.word >= 0 && c.word < WORD.length;
        ctx.fillStyle = isWord ? "#ffffff" : "#d6ffe4";
        ctx.shadowColor = isWord ? "#00ff66" : "transparent";
        ctx.shadowBlur = isWord ? 12 : 0;
        ctx.fillText(ch, x, yPx);
        ctx.shadowBlur = 0;

        const before = row;
        c.y += c.speed;
        if (Math.floor(c.y) !== before) {
          c.prev = ch;
          if (c.word >= 0) c.word += 1;
        }

        // mutación ocasional de un glifo ya caído
        if (Math.random() < 0.004) {
          ctx.fillStyle = "#7dffb2";
          ctx.fillText(randGlyph(), x, Math.random() * yPx);
        }

        if (yPx > height && Math.random() > 0.975) cols[i] = newColumn(true);
      }
    }

    function loop(ts) {
      raf = requestAnimationFrame(loop);
      if (document.hidden) return;
      if (ts - last < 1000 / FPS) return;
      last = ts;
      frame();
    }

    resize();
    window.addEventListener("resize", resize);

    if (reduced) {
      for (let k = 0; k < 60; k++) frame();   // un cuadro "asentado", sin animar
    } else {
      raf = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-0"
      style={{ opacity }}
    />
  );
}

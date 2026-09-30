import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./lib/i18n.js";
import App from "./App.jsx";
import "./index.css";

// Shell Tauri en producción: la ventana carga la SPA desde el bundle
// (https://tauri.localhost en Windows) y la API vive en el backend
// (http://127.0.0.1:7981) que recién queda sano unos segundos después.
// Mientras se espera, se muestra un splash neutro y NO la app completa:
// el localStorage del tema vive en el origen del backend, no en el del
// bundle, así que renderizar la app acá la mostraría en el tema por
// defecto (dark) hasta el redirect — un "flash" de tema perceptible.
// En dev (Vite con proxy a 7981) y en el navegador esto no corre.
async function tauriBootstrapRedirect() {
  if (!import.meta.env.PROD) return null;
  // Ya estamos en el backend (http://127.0.0.1:<puerto>): no redirigir de
  // nuevo (evita loop). El bundle Tauri es https://tauri.localhost o
  // tauri://..., nunca http: sobre 127.0.0.1.
  if (location.protocol === "http:" && location.hostname === "127.0.0.1") return null;
  const { isTauri, onSidecarState, hasProvisionFlag } = await import("./lib/tauri.js");
  if (!isTauri()) return null;
  // T5.2: con --provision la app abre directo a la pantalla de provisión.
  const suffix = (await hasProvisionFlag().catch(() => false)) ? "/provision" : "/";
  // La puerta del redirect es el evento "running": la shell solo lo emite
  // después de verificar /api/health (nunca con el backend a medio arrancar)
  // y lo repite cada 5 s (heartbeat) para captar páginas que cargan tarde.
  // Sin fetch cross-origin (CORS/mixed-content fuera del camino crítico) y
  // sin comandos IPC: los eventos están cubiertos por core:default.
  const timer = setTimeout(() => location.reload(), 180000);
  return await new Promise((resolve) => {
    onSidecarState((ev) => {
      if (ev.state === "running" && ev.url) {
        clearTimeout(timer);
        location.replace(ev.url + suffix);
        resolve("redirect");
      } else if (ev.state === "error") {
        clearTimeout(timer);
        resolve("error");
      }
    })
      .then(() => {})
      .catch(() => {
        clearTimeout(timer);
        resolve("error");
      });
  });
}

// Splash de arranque (solo en el origen del bundle): branding "A oscuro
// centrado" del brand kit (assets/brand), con detalles de la opción C:
// eyebrow "GLYVEX GROUP", título en 2 líneas y borde inferior degradado.
// Neutro a propósito: no usa el tema guardado (no es visible desde este
// origen) y las fuentes Poppins van embebidas (app 100% offline).
function BootSplash() {
  const [version, setVersion] = useState(null);
  useEffect(() => {
    import("@tauri-apps/api/app")
      .then(({ getVersion }) => getVersion())
      .then((v) => setVersion(v))
      .catch(() => {}); // sin IPC: se oculta la versión
  }, []);
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "radial-gradient(ellipse at 50% 35%, #1a1530 0%, #0b0d14 70%)",
        color: "#e8eaf0",
        fontFamily: "Poppins, 'Segoe UI', sans-serif",
        userSelect: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "12px",
        }}
      >
        <div style={{ fontSize: "11px", letterSpacing: "4px", color: "#8a90a6", fontWeight: 500 }}>
          GLYVEX GROUP
        </div>
        <BootLogo />
        <div style={{ fontSize: "30px", fontWeight: 700, lineHeight: 1.15, letterSpacing: "-0.4px", marginTop: "14px", textAlign: "center" }}>
          Glyvex
          <br />
          <span className="gx-splash-grad">AI Suite</span>
        </div>
        <div style={{ fontSize: "12.5px", color: "#8a90a6", marginTop: "2px" }}>
          IA local, sin nube
        </div>
        <div className="gx-splash-bar" style={{ width: "220px", height: "3px", borderRadius: "3px", background: "#1e2133", marginTop: "26px", overflow: "hidden" }}>
          <i />
        </div>
        <div style={{ fontSize: "10.5px", color: "#6b7089", marginTop: "8px" }}>
          Iniciando servicios…
        </div>
      </div>
      {version && (
        <div style={{ position: "absolute", bottom: "14px", right: "18px", fontSize: "10.5px", color: "#6b7089" }}>
          v{version}
        </div>
      )}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: "3px",
          background: "linear-gradient(90deg, #7c3aed, #0ea5e9 60%, #06b6d4)",
        }}
      />
      <style>{`
        @font-face { font-family: Poppins; font-weight: 400; src: url(/fonts/poppins-regular.woff2) format("woff2"); }
        @font-face { font-family: Poppins; font-weight: 500; src: url(/fonts/poppins-medium.woff2) format("woff2"); }
        @font-face { font-family: Poppins; font-weight: 700; src: url(/fonts/poppins-bold.woff2) format("woff2"); }
        .gx-splash-grad {
          background: linear-gradient(90deg, #7c3aed, #0ea5e9 60%, #06b6d4);
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
        }
        .gx-splash-bar i {
          display: block; height: 100%; width: 40%; border-radius: 3px;
          background: linear-gradient(90deg, #7c3aed, #0ea5e9, #06b6d4);
          animation: gx-splash-load 1.6s ease-in-out infinite;
        }
        @keyframes gx-splash-load { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }
        .gx-splash-logo { animation: gx-splash-in 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) both; }
        @keyframes gx-splash-in { from { opacity: 0; transform: scale(0.85) rotate(-30deg); } to { opacity: 1; transform: none; } }
        @media (prefers-reduced-motion: reduce) {
          .gx-splash-logo { animation: none; }
          .gx-splash-bar i { animation-duration: 3s; }
        }
      `}</style>
    </div>
  );
}

// Isotipo del brand kit (assets/brand/logo/glyvex-ai-suite-logo.svg).
function BootLogo() {
  return (
    <svg
      className="gx-splash-logo"
      width="112"
      height="112"
      viewBox="100 100 824 824"
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle cx="512" cy="512" r="356" fill="none" stroke="#7c3aed" strokeWidth="22" />
      <line x1="203.7" y1="334.0" x2="820.3" y2="690.0" stroke="#a77bf3" strokeOpacity="1" strokeWidth="16" strokeLinecap="round" />
      <line x1="203.7" y1="690.0" x2="820.3" y2="334.0" stroke="#06b6d4" strokeOpacity="0.9" strokeWidth="16" strokeLinecap="round" />
      <circle cx="512" cy="512" r="102" fill="#7c3aed" />
      <line x1="512.0" y1="156.0" x2="512.0" y2="868.0" stroke="#0ea5e9" strokeOpacity="0.95" strokeWidth="16" strokeLinecap="round" />
      <circle cx="512.0" cy="156.0" r="57" fill="#0ea5e9" />
      <circle cx="820.3" cy="334.0" r="57" fill="#06b6d4" />
      <circle cx="820.3" cy="690.0" r="57" fill="#7c3aed" />
      <circle cx="512.0" cy="868.0" r="57" fill="#0ea5e9" />
      <circle cx="203.7" cy="690.0" r="57" fill="#06b6d4" />
      <circle cx="203.7" cy="334.0" r="57" fill="#7c3aed" />
    </svg>
  );
}

async function boot() {
  const root = createRoot(document.getElementById("root"));
  const inTauriBundle =
    import.meta.env.PROD &&
    "__TAURI_INTERNALS__" in window &&
    !(location.protocol === "http:" && location.hostname === "127.0.0.1");
  if (inTauriBundle) {
    root.render(
      <StrictMode>
        <BootSplash />
      </StrictMode>
    );
    const outcome = await tauriBootstrapRedirect();
    // "redirect": el documento ya está siendo reemplazado por la SPA del
    // backend (que aplica el tema guardado antes de pintar). "error": se
    // monta la app completa, cuyo overlay muestra el error + Reintentar.
    if (outcome === "error") {
      root.render(
        <StrictMode>
          <App />
        </StrictMode>
      );
    }
    return;
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}

boot();

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const HERE = dirname(fileURLToPath(import.meta.url));
const ENVIRONMENTS_DIR = resolve(HERE, "..", "environments");

/**
 * Parser mínimo de los environments/<modo>.env del backend.
 *
 * Esos archivos son la ÚNICA fuente de los puertos de cada instancia: para
 * mover una instancia de puerto se toca solo ese archivo, y tanto uvicorn como
 * este dev server quedan alineados. Si en cambio el frontend tuviera su propio
 * .env con el puerto repetido, habría dos lugares que se desincronizan.
 *
 * Mismas reglas que start.sh / start.ps1: KEY=value por línea, se ignoran
 * vacías y comentarios, se recortan espacios alrededor del "=", el valor va
 * literal (sin expansión ni desescapado de comillas).
 */
function readEnvironmentFile(mode) {
  const file = resolve(ENVIRONMENTS_DIR, `${mode}.env`);
  let raw;
  try {
    raw = readFileSync(file, "utf8");
  } catch {
    // Modos propios de Vite (development, production) que no tienen un
    // archivo en environments/: no es un error, se cae a los defaults.
    return {};
  }

  const out = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return out;
}

function toPort(value, fallback) {
  const port = Number.parseInt(value, 10);
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : fallback;
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // `npm run dev` corre en modo "development" y no tiene archivo propio en
  // environments/, así que se lo trata como la instancia de producción: es la
  // que usa el ./data y los puertos de siempre.
  const envName = mode === "development" ? "production" : mode;
  const shared = readEnvironmentFile(envName);

  // loadEnv sigue disponible encima: un frontend/.env.<modo> con VITE_DEV_PORT
  // o VITE_API_PROXY_TARGET pisa lo de environments/, para poder apuntar el
  // dev server a un backend remoto sin tocar la config de la instancia.
  const viteEnv = loadEnv(mode, HERE, "VITE_");

  const devPort = toPort(viteEnv.VITE_DEV_PORT ?? shared.GLYVEX_DEV_PORT, 5173);

  const backendHost = shared.GLYVEX_HOST || "127.0.0.1";
  const backendPort = toPort(shared.GLYVEX_PORT, 7981);
  const proxyTarget =
    viteEnv.VITE_API_PROXY_TARGET || `http://${backendHost}:${backendPort}`;

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: devPort,
      // Sin esto Vite busca el próximo puerto libre si el elegido está
      // ocupado: con dos instancias en la misma máquina eso hace que testing
      // arranque callado en el puerto de producción. Mejor que falle.
      strictPort: true,
      proxy: {
        // En desarrollo, el dev server de Vite reenvía /api al backend FastAPI
        // de ESTA instancia (ver environments/<modo>.env).
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
          ws: true, // necesario para el WebSocket de logs en vivo (M2: /api/launcher/logs/*/stream)
        },
      },
    },
    build: {
      outDir: "dist",
      // Rolldown (Vite 8) no acepta manualChunks como objeto: solo función
      // id -> nombre de chunk (o undefined para dejarlo en el chunk de la página).
      rollupOptions: {
        output: {
          manualChunks(id) {
            const m = id.match(/node_modules[\\/]+((?:@[^\\/]+[\\/]+)?[^\\/]+)/);
            if (!m) return;
            const pkg = m[1];
            // Chunks de vendor compartidos: cada librería pesada carga una sola
            // vez y se cachea; las páginas (lazy en App.jsx) van aparte.
            if (
              pkg === "react" || pkg === "react-dom" ||
              pkg === "react-router-dom" || pkg === "react-router"
            ) {
              return "react";
            }
            if (pkg === "i18next" || pkg === "react-i18next") return "i18n";
            if (pkg === "recharts") return "recharts";
            if (
              pkg === "react-markdown" || pkg === "remark-gfm" ||
              pkg === "rehype-highlight" || pkg === "highlight.js"
            ) {
              return "markdown";
            }
          },
        },
      },
    },
  };
});

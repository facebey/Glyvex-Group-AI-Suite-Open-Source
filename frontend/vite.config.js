import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // En desarrollo, el dev server de Vite reenvía /api al backend FastAPI
      // que corre en :7981 (ver backend/main.py y data/config.json).
      "/api": {
        target: "http://localhost:7981",
        changeOrigin: true,
        ws: true, // necesario para el WebSocket de logs en vivo (M2: /api/launcher/logs/*/stream)
      },
    },
  },
  build: {
    outDir: "dist",
  },
});

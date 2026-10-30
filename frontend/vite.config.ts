/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production : `npm run build` → app/static/app (servi par FastAPI, pas de Node en production).
// Développement : `npm run dev` → http://localhost:5173, API et WebSockets vidéo relayés vers CamSup.
const backend = process.env.CAMSUP_URL ?? "https://localhost:8080";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../app/static/app", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 600 },
  server: {
    proxy: {
      "/api": {
        target: backend, changeOrigin: true, secure: false, ws: true,
        // le serveur refuse un WebSocket vidéo dont l'origine n'est pas lui-même : en dev, on la réécrit
        configure: (proxy) => proxy.on("proxyReqWs", (req) => req.setHeader("origin", backend)),
      },
      "/media": { target: backend, changeOrigin: true, secure: false },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
});

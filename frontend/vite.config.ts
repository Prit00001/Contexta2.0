/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `pnpm dev` serves the UI on :5173 and proxies API calls to uvicorn on :8000.
// Prod: `pnpm build` writes dist/, which FastAPI serves (app/main.py).
const API = ["/ask", "/health", "/ready", "/auth", "/workspaces", "/metrics", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(API.map((p) => [p, "http://127.0.0.1:8000"])) },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"] },
});

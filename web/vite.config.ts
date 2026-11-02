/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (`uv run uvicorn ... --port 8000`); the
// dev server forwards to it, so the browser sees one origin, as in production.
const backend = process.env.ZRW_BACKEND ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    // One ~200 kB (gzipped) bundle: a conversation needs nearly all of it (Markdown,
    // popovers, routing, i18n) from the first screen, so splitting would only add
    // requests before the first answer.
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: {
      "/api": backend,
      "/healthz": backend,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // e2e/ holds the Playwright browser tests, which Vitest cannot run.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});

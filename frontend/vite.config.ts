/// <reference types="vitest" />
/// <reference types="node" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite config.
 *
 * Dev proxy (npm run dev):
 *  - `/api` → `VITE_DEV_API_TARGET` (default `http://127.0.0.1:8011`)
 *
 * Production build (npm run build):
 *  - Static files in `dist/` can be served by the Flask backend
 *    or by a separate static server. The frontend's API base URL
 *    is read from `VITE_API_BASE` at build time, defaulting to
 *    `/api` (same-origin). Override per-environment via
 *    `VITE_API_BASE=https://example.com` etc.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const devApiTarget = env.VITE_DEV_API_TARGET || "http://127.0.0.1:8011";
  const proxy = {
    "/api": {
      target: devApiTarget,
      changeOrigin: false,
    },
    "/ws": {
      target: devApiTarget,
      ws: true,
      changeOrigin: false,
    },
  };

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5273,
      proxy,
    },
    preview: {
      host: "0.0.0.0",
      port: 5273,
      strictPort: true,
      proxy,
    },
    test: {
      globals: true,
      environment: "happy-dom",
      setupFiles: "./src/test/setup.ts",
      css: false,
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      exclude: ["e2e/**", "node_modules/**", "dist/**"],
    },
    build: {
      // The workbench is a single-screen console; after trimming highlight.js to
      // registered languages, the main chunk is expected to sit just above
      // Vite's generic 500 kB warning threshold.
      chunkSizeWarningLimit: 700,
      rollupOptions: {
        output: {
          // Split the React core into its own long-lived vendor chunk so it is
          // cached independently of page chunks and the app shell. Page chunks
          // produced by route-level code splitting carry only their own code.
          manualChunks(id) {
            if (id.includes("node_modules")) {
              if (
                /[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(
                  id,
                )
              ) {
                return "vendor-react";
              }
            }
          },
        },
      },
    },
  };
});

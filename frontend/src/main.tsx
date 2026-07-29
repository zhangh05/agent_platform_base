import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles/global.css";

// Theme initialization — read from Zustand persist store (na_ui) or
// fall back to prefers-color-scheme. We do this BEFORE React mounts so
// the first paint uses the correct tokens and there's no flash.
(function initTheme() {
  try {
    const raw = localStorage.getItem("na_ui");
    if (raw) {
      const ui = JSON.parse(raw);
      if (ui.state?.theme === "light" || ui.state?.theme === "dark") {
        document.documentElement.setAttribute("data-theme", ui.state.theme);
        return;
      }
    }
  } catch {
    /* ignore */
  }
  const prefersDark =
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");
})();

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found in index.html");
}

// v3.27.1: StrictMode restored. The previous "Maximum update depth exceeded"
// turned out to come from `Diagnostics.tsx` returning a fresh
// `JSON.parse(...)` object from `useSyncExternalStore`'s getSnapshot on
// every render, violating React 18's snapshot-stability contract — not
// from StrictMode double-invocation.
ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);

import { onCLS, onFCP, onINP, onLCP, onTTFB } from "web-vitals";

// Opt-in endpoint. When unset (default), vitals are collected but NOT shipped,
// so we never spam the console with 404s against a non-existent endpoint.
// Enable by setting VITE_VITALS_ENDPOINT=https://your-collector/metrics/vitals
const ENDPOINT = import.meta.env.VITE_VITALS_ENDPOINT as string | undefined;

/**
 * Best-effort Real User Monitoring. Collects Core Web Vitals and (optionally)
 * ships them via sendBeacon. Network shipping is opt-in so that, without a
 * collector wired up, the console stays clean (no 404 noise).
 */
export function initWebVitals(onReport?: (metric: unknown) => void): void {
  const sink = (metric: { name: string; value: number; rating: string; id: string }) => {
    // Always hand the metric to the caller for local collection / debugging.
    onReport?.(metric);
    // Network shipping is opt-in to avoid 404 noise when no collector exists.
    if (!ENDPOINT || typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
      return;
    }
    try {
      const payload = JSON.stringify({
        name: metric.name,
        value: Math.round(metric.value),
        rating: metric.rating,
        id: metric.id,
        path: location.pathname,
        t: Date.now(),
      });
      navigator.sendBeacon(ENDPOINT, payload);
    } catch {
      /* collector may be unavailable — ignore */
    }
  };

  onLCP(sink);
  onCLS(sink);
  onINP(sink);
  onFCP(sink);
  onTTFB(sink);
}

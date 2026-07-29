/**
 * Global setup — verify the backend is reachable before any test runs.
 * If the backend is down, the whole suite is reported as failed (and
 * the failure message tells the operator how to start it).
 */

import { request } from "@playwright/test";

const BACKEND_URL = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8010";

export default async function globalSetup() {
  const ctx = await request.newContext({ baseURL: BACKEND_URL });
  const paths = ["/api/health", "/api/version", "/api/capabilities"];
  let ok = false;
  for (const p of paths) {
    try {
      const r = await ctx.get(p, { timeout: 3000 });
      if (r.status() < 500) {
        ok = true;
        break;
      }
    } catch {
      /* try next */
    }
  }
  await ctx.dispose();
  if (!ok) {
    throw new Error(
      `Backend not reachable at ${BACKEND_URL}. Start the Flask server (e.g. ./scripts/run-server.sh or PYTHONPATH=. python3 -m backend.main) and re-run.`,
    );
  }
}

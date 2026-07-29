import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for frontend E2E.
 *
 * The E2E suite needs both the Flask backend and the Vite dev server
 * running. We use `webServer` to start the dev server automatically
 * (with the API proxy → backend). The backend is started externally
 * (see scripts/run-e2e.sh) so its lifecycle is independent of the test
 * runner.
 *
 * Backend env: AGENT_PLATFORM_BACKEND_URL — defaults to http://127.0.0.1:8011.
 * Frontend env: VITE_DEV_API_TARGET — defaults to http://127.0.0.1:8011.
 */
const FRONTEND_URL = process.env.E2E_FRONTEND_URL ?? "http://127.0.0.1:5273";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? "dot" : "list",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: FRONTEND_URL,
    trace: "off",
    headless: true,
    actionTimeout: 8_000,
    navigationTimeout: 12_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  globalSetup: "./e2e/global-setup.ts",
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5273",
    url: FRONTEND_URL,
    reuseExistingServer: true,
    timeout: 60_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});

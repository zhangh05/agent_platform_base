import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

/**
 * Isolated Playwright environment.
 *
 * Each invocation owns its backend, Vite server, storage root and workspace.
 * The suite never reuses the developer's running services or workspace data.
 */
const CONFIG_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CONFIG_DIR, "..");
const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? "18011");
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? "15273");
const BACKEND_URL = process.env.E2E_BACKEND_URL ?? `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_URL = process.env.E2E_FRONTEND_URL ?? `http://127.0.0.1:${FRONTEND_PORT}`;
const STORAGE_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "agent-platform-e2e-"));
const STORAGE_TOKEN = crypto.randomUUID();
const STORAGE_MARKER = path.join(STORAGE_ROOT, ".agent-platform-e2e-owner");
const ADMIN_PASSWORD = crypto
  .createHash("sha256")
  .update(`${REPO_ROOT}:${BACKEND_PORT}:${FRONTEND_PORT}:e2e-admin`)
  .digest("base64url");
const API_TOKEN = crypto
  .createHash("sha256")
  .update(`${REPO_ROOT}:${BACKEND_PORT}:${FRONTEND_PORT}:e2e-api-token`)
  .digest("hex");
const PYTHON_BIN = process.env.E2E_PYTHON_BIN ?? path.join(REPO_ROOT, ".venv", "bin", "python3");

fs.writeFileSync(STORAGE_MARKER, STORAGE_TOKEN, { encoding: "utf8", flag: "wx" });

const cleanupOwnedStorage = () => {
  try {
    const resolvedRoot = fs.realpathSync(STORAGE_ROOT);
    const resolvedTmp = fs.realpathSync(os.tmpdir());
    const ownedName = path.basename(resolvedRoot).startsWith("agent-platform-e2e-");
    const underTmp = path.dirname(resolvedRoot) === resolvedTmp;
    const markerMatches = fs.readFileSync(STORAGE_MARKER, "utf8") === STORAGE_TOKEN;
    if (ownedName && underTmp && markerMatches) fs.rmSync(resolvedRoot, { recursive: true, force: true });
  } catch {
    // The explicit global teardown reports cleanup errors. The exit hook is a
    // final best-effort guard for --list, startup failures and interruptions.
  }
};
process.once("exit", cleanupOwnedStorage);

process.env.E2E_BACKEND_URL = BACKEND_URL;
process.env.E2E_FRONTEND_URL = FRONTEND_URL;
process.env.E2E_STORAGE_ROOT = STORAGE_ROOT;
process.env.E2E_STORAGE_TOKEN = STORAGE_TOKEN;
process.env.E2E_ADMIN_PASSWORD = ADMIN_PASSWORD;
process.env.E2E_API_TOKEN = API_TOKEN;

const commonEnv = {
  ...process.env,
  NA_WORKSPACE_ROOT: STORAGE_ROOT,
  AGENT_PLATFORM_RUNTIME_BIND_HOST: "127.0.0.1",
  AGENT_PLATFORM_TRUSTED_LOCAL_PYTHON_EXECUTION: "true",
  AGENT_PLATFORM_IDENTITY_ENABLED: "true",
  AGENT_PLATFORM_AUTH_ENABLED: "true",
  AGENT_PLATFORM_API_TOKEN: API_TOKEN,
  AGENT_PLATFORM_LOGIN_ENABLED: "true",
  AGENT_PLATFORM_LOGIN_USERNAME: "E2EAdmin",
  AGENT_PLATFORM_LOGIN_PASSWORD: ADMIN_PASSWORD,
  AGENT_PLATFORM_SESSION_SECRET: crypto.randomBytes(32).toString("hex"),
};

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
    trace: "retain-on-failure",
    headless: true,
    actionTimeout: 8_000,
    navigationTimeout: 12_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  globalSetup: "./e2e/global-setup.ts",
  webServer: [
    {
      command: `PYTHONPATH=. "${PYTHON_BIN}" -m backend.main --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: REPO_ROOT,
      env: commonEnv,
      url: `${BACKEND_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${FRONTEND_PORT}`,
      cwd: path.resolve(REPO_ROOT, "frontend"),
      env: { ...commonEnv, VITE_DEV_API_TARGET: BACKEND_URL },
      url: FRONTEND_URL,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});

/**
 * Playwright fixtures — shared backend lifecycle.
 *
 * The backend is started **once** before all tests via a global setup
 * (see `e2e/global-setup.ts`). The `request` fixture talks directly to
 * the backend so we can prepare / clean data before the UI sees it.
 */

import { test as base, expect, type APIRequestContext, type Page, type Playwright } from "@playwright/test";
import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BACKEND_URL = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8011";
const CONFIG_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CONFIG_DIR, "../..");
const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? "18011");
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? "15273");
const ADMIN_PASSWORD = crypto
  .createHash("sha256")
  .update(`${REPO_ROOT}:${BACKEND_PORT}:${FRONTEND_PORT}:e2e-admin`)
  .digest("base64url");

async function authenticatedContext(playwright: Playwright) {
  const ctx = await playwright.request.newContext({ baseURL: BACKEND_URL });
  const login = await ctx.post("/api/auth/login", {
    data: { username: "E2EAdmin", password: ADMIN_PASSWORD },
  });
  if (!login.ok()) {
    await ctx.dispose();
    throw new Error(`failed to authenticate E2E admin: ${login.status()}`);
  }
  return ctx;
}

type TestFixtures = {
  api: APIRequestContext;
  backendUrl: string;
};
type WorkerFixtures = { workspaceId: string };

export const test = base.extend<TestFixtures, WorkerFixtures>({
  backendUrl: BACKEND_URL,
  storageState: async ({ playwright }, use) => {
    const ctx = await authenticatedContext(playwright);
    await use(await ctx.storageState());
    await ctx.dispose();
  },
  workspaceId: [async ({}, use) => {
    // The product intentionally exposes one fixed workspace. User isolation is
    // provided by the backend principal directory, not by a UI workspace picker.
    await use("default");
  }, { scope: "worker" }],
  api: async ({ playwright }, use) => {
    const ctx = await authenticatedContext(playwright);
    await use(ctx);
    await ctx.dispose();
  },
});

export async function selectWorkspace(page: Page, workspaceId: string) {
  expect(workspaceId).toBe("default");
  await expect(page.getByTestId("btn-new-session")).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('[data-testid^="ws-"]')).toHaveCount(0);
}

export { expect };

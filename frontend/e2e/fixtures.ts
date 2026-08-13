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
  workspaceId: [async ({ playwright }, use, workerInfo) => {
    const workspaceId = `e2e_${process.pid}_${workerInfo.workerIndex}`;
    const ctx = await authenticatedContext(playwright);
    const created = await ctx.post("/api/workspaces", { data: { workspace_id: workspaceId } });
    if (!created.ok()) {
      throw new Error(`failed to create worker workspace ${workspaceId}: ${created.status()} ${await created.text()}`);
    }
    await use(workspaceId);
    await ctx.dispose();
  }, { scope: "worker" }],
  api: async ({ playwright }, use) => {
    const ctx = await authenticatedContext(playwright);
    await use(ctx);
    await ctx.dispose();
  },
});

export async function selectWorkspace(page: Page, workspaceId: string) {
  const button = page.locator(`[data-testid="ws-${workspaceId}"]`);
  await expect(button).toBeVisible({ timeout: 8_000 });
  await button.click();
  await expect(button).toHaveClass(/active/, { timeout: 5_000 });
}

export { expect };

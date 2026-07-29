/**
 * Playwright fixtures — shared backend lifecycle.
 *
 * The backend is started **once** before all tests via a global setup
 * (see `e2e/global-setup.ts`). The `request` fixture talks directly to
 * the backend so we can prepare / clean data before the UI sees it.
 */

import { test as base, expect, type APIRequestContext } from "@playwright/test";

const BACKEND_URL = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8010";

export const test = base.extend<{
  api: APIRequestContext;
  backendUrl: string;
}>({
  backendUrl: BACKEND_URL,
  api: async ({ playwright }, use) => {
    const ctx = await playwright.request.newContext({
      baseURL: BACKEND_URL,
    });
    await use(ctx);
    await ctx.dispose();
  },
});

export { expect };

/** Verify the isolated backend and safely tear down its run-owned storage. */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { request } from "@playwright/test";

const BACKEND_URL = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:18011";
const STORAGE_ROOT = process.env.E2E_STORAGE_ROOT ?? "";
const STORAGE_TOKEN = process.env.E2E_STORAGE_TOKEN ?? "";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "";

async function validateOwnedStorage() {
  if (!STORAGE_ROOT || !STORAGE_TOKEN) throw new Error("missing E2E storage ownership metadata");
  const resolvedRoot = await fs.realpath(STORAGE_ROOT);
  const resolvedTmp = await fs.realpath(os.tmpdir());
  const marker = path.join(resolvedRoot, ".lzcore-e2e-owner");
  const markerToken = await fs.readFile(marker, "utf8");
  if (path.dirname(resolvedRoot) !== resolvedTmp || !path.basename(resolvedRoot).startsWith("lzcore-e2e-") || markerToken !== STORAGE_TOKEN) {
    throw new Error(`refusing to remove unowned E2E storage root: ${resolvedRoot}`);
  }
  return resolvedRoot;
}

async function cleanupOwnedStorage() {
  const resolvedRoot = await validateOwnedStorage();
  await fs.rm(resolvedRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 });
}

export default async function globalSetup() {
  const ctx = await request.newContext({ baseURL: BACKEND_URL });
  try {
    const health = await ctx.get("/api/health", { timeout: 5_000 });
    if (health.status() >= 500) {
      throw new Error(`isolated backend health failed: ${health.status()}`);
    }
    const login = await ctx.post("/api/auth/login", {
      data: { username: "E2EAdmin", password: ADMIN_PASSWORD },
    });
    if (!login.ok()) throw new Error(`isolated E2E admin login failed: ${login.status()}`);
  } catch (error) {
    await cleanupOwnedStorage();
    throw error;
  } finally {
    await ctx.dispose();
  }

  return async () => {
    // Playwright invokes this callback while webServer children can still be
    // finishing requests. Validate ownership here; the process exit hook in
    // playwright.config.ts removes the directory after those children stop.
    await validateOwnedStorage();
  };
}

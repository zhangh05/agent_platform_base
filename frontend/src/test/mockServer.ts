/**
 * Lightweight MSW-free mock for the Axios-based apiRequest.
 * Each test sets its own queue of responses per URL.
 */

import { vi } from "vitest";
import type { AxiosRequestConfig } from "axios";
import * as clientModule from "../api/client";

interface MockResp {
  status: number;
  data: unknown;
}

const responseQueue = new Map<string, MockResp[]>();
let defaultResp: MockResp = { status: 200, data: { ok: true } };
const requests: AxiosRequestConfig[] = [];

export function enqueue(url: string, resp: MockResp): void {
  const key = url.split("?")[0];
  const arr = responseQueue.get(key) ?? [];
  arr.push(resp);
  responseQueue.set(key, arr);
}

export function setDefault(resp: MockResp): void {
  defaultResp = resp;
}

export function resetMocks(): void {
  responseQueue.clear();
  requests.length = 0;
  defaultResp = { status: 200, data: { ok: true } };
}

export function getRequests(): AxiosRequestConfig[] {
  return [...requests];
}

export function installMockApi(): void {
  vi.spyOn(clientModule, "apiRequest").mockImplementation(
    async (config: AxiosRequestConfig) => {
      requests.push(config);
      const url = (config.url ?? "").toString().split("?")[0];
      const queue = responseQueue.get(url);
      const next = queue?.shift() ?? defaultResp;
      if (next.status >= 400) {
        const err: { ok: false; code: string; status: number; message: string; timestamp: string } = {
          ok: false,
          code: next.status >= 500 ? "http_5xx" : "http_4xx",
          status: next.status,
          message: typeof next.data === "object" && next.data && "message" in next.data
            ? String((next.data as { message: unknown }).message)
            : `mock ${next.status}`,
          timestamp: new Date().toISOString(),
        };
        throw err;
      }
      return next.data;
    },
  );
}

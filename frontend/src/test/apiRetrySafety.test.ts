import { afterEach, describe, expect, it, vi } from "vitest";
import { AxiosError } from "axios";
import { apiClient, apiRequest } from "../api/client";

function networkError(method: string): AxiosError {
  return new AxiosError(
    "network lost after request dispatch",
    "ERR_NETWORK",
    { method, url: "/test", headers: {} } as never,
  );
}

describe("apiRequest retry safety", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("does not replay a side-effecting POST after an uncertain network failure", async () => {
    const request = vi.spyOn(apiClient, "request").mockRejectedValue(networkError("POST"));

    await expect(apiRequest({ method: "POST", url: "/agent/message", data: { message: "write" } }))
      .rejects.toMatchObject({ code: "network" });

    expect(request).toHaveBeenCalledTimes(1);
  });

  it("retries a safe GET after a transient network failure", async () => {
    vi.useFakeTimers();
    const request = vi.spyOn(apiClient, "request")
      .mockRejectedValueOnce(networkError("GET"))
      .mockResolvedValueOnce({ data: { ok: true } } as never);

    const response = apiRequest<{ ok: boolean }>({ method: "GET", url: "/ready" });
    await vi.runAllTimersAsync();

    await expect(response).resolves.toEqual({ ok: true });
    expect(request).toHaveBeenCalledTimes(2);
  });
});

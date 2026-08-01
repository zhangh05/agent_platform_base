import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../app/App";
import { authApi } from "../api";
import { installMockApi, resetMocks } from "./mockServer";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

describe("authenticated refresh", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    window.history.replaceState({}, "", "/workbench");
  });

  it("never flashes the login screen when StrictMode cancels the first status request", async () => {
    const authenticated = deferred<Awaited<ReturnType<typeof authApi.status>>>();
    let calls = 0;

    vi.spyOn(authApi, "status").mockImplementation((signal?: AbortSignal) => {
      calls += 1;
      if (calls === 1) {
        return new Promise((_, reject) => {
          const rejectAsAborted = () => reject({
            ok: false,
            code: "aborted",
            status: 0,
            message: "请求已取消",
            timestamp: new Date().toISOString(),
          });
          if (signal?.aborted) rejectAsAborted();
          else signal?.addEventListener("abort", rejectAsAborted, { once: true });
        });
      }
      return authenticated.promise;
    });

    render(
      <React.StrictMode>
        <App />
      </React.StrictMode>,
    );

    await waitFor(() => expect(calls).toBe(2));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText("登录工作台")).not.toBeInTheDocument();

    await act(async () => {
      authenticated.resolve({
        ok: true,
        login_enabled: true,
        authenticated: true,
        username: "Admin",
      });
    });

    expect(await screen.findByRole("button", { name: "退出登录" })).toBeInTheDocument();
    expect(screen.queryByText("登录工作台")).not.toBeInTheDocument();
  });

  it("remounts the route stage so every page switch gets a transition", async () => {
    render(<App />);

    const dataLink = await screen.findByTestId("nav-data");
    const previousStage = await waitFor(() => {
      const node = document.querySelector<HTMLElement>('[data-route="/workbench"]');
      expect(node).not.toBeNull();
      return node as HTMLElement;
    });

    fireEvent.click(dataLink);

    const nextStage = await waitFor(() => {
      const node = document.querySelector<HTMLElement>('[data-route="/data"]');
      expect(node).not.toBeNull();
      return node as HTMLElement;
    });
    expect(nextStage).not.toBe(previousStage);
    expect(window.location.pathname).toBe("/data");
  });
});

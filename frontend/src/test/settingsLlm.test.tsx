/**
 * Settings — LLM Provider configuration tests (v2).
 *
 * Validates: provider sidebar, card selection, form fields,
 * save / apply / test / reset flows, api_key 3-state.
 */

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { Settings } from "../pages/Settings/Settings";
import { settingsApi } from "../api";
import { useSessionStore } from "../stores/session";
import * as confirmModule from "../components/ConfirmDialog";
import type { ProviderConfig, ProviderListResponse, LlmTestResult } from "../types";

// Settings.reset() now uses the shared `confirm()` portal modal — swap the
// module export for a promise-returning stub so consumers always auto-approve
// and the tests don't need to drive the dialog DOM. Stubbing at the module
// level ensures the same function reference is returned to Settings.tsx and
// to the test, so the spy assertion matches.
vi.mock("../components/ConfirmDialog", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../components/ConfirmDialog")>();
  return {
    ...actual,
    confirm: vi.fn(() => Promise.resolve(true)),
    ConfirmHost: () => null,
  };
});

const baseProvider: ProviderConfig = {
  provider: "minimax",
  label: "MiniMax",
  enabled: false,
  base_url: "https://api.minimaxi.com/v1",
  model: "MiniMax-M3",
  temperature: 0.2,
  max_tokens: 1200,
  safe_mode: true,
  key_configured: false,
  key_preview: null,
  hint: "api.minimaxi.com",
  is_active: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  useSessionStore.setState({ currentWorkspaceId: "ws-settings", currentSessionId: null });
});

function makeProviders(active: string = "minimax"): ProviderListResponse {
  const ids = ["minimax", "deepseek", "ark", "openai", "anthropic", "ollama", "custom"];
  const labels = ["MiniMax", "DeepSeek", "方舟 (豆包)", "OpenAI", "Anthropic", "Ollama (本地)", "自定义"];
  const hints = ["api.minimaxi.com", "api.deepseek.com", "ark.volces.com", "api.openai.com", "api.anthropic.com", "localhost:11434", "OpenAI 兼容 API"];
  return {
    ok: true,
    active,
    providers: ids.map((id, i) => ({
      provider: id,
      label: labels[i],
      enabled: id === active,
      base_url: id === "minimax" ? "https://api.minimaxi.com/v1" : "",
      model: id === "minimax" ? "MiniMax-M3" : "",
      temperature: 0.2,
      max_tokens: 1200,
      safe_mode: true,
      key_configured: id === "minimax",
      key_preview: id === "minimax" ? "eyJ0****8a3f" : null,
      hint: hints[i],
      is_active: id === active,
    })),
  };
}

function mockApi(overrides: Partial<{
  providersList: ProviderListResponse;
  providerSave: ReturnType<typeof vi.fn>;
  providerGet: ReturnType<typeof vi.fn>;
  providerDelete: ReturnType<typeof vi.fn>;
  llmActivate: ReturnType<typeof vi.fn>;
  llmTest: ReturnType<typeof vi.fn>;
  workspaceSettings: ReturnType<typeof vi.fn>;
  updateWorkspaceSettings: ReturnType<typeof vi.fn>;
}> = {}) {
  const providers = overrides.providersList ?? makeProviders("minimax");
  const spy = {
    providersList: vi.fn().mockResolvedValue(providers),
    providerSave: overrides.providerSave ?? vi.fn().mockResolvedValue({ ok: true, config: providers.providers[0] }),
    providerGet: overrides.providerGet ?? vi.fn().mockResolvedValue({ ok: true, config: providers.providers[0] }),
    providerDelete: overrides.providerDelete ?? vi.fn().mockResolvedValue({ ok: true, deleted: true }),
    llmActivate: overrides.llmActivate ?? vi.fn().mockResolvedValue({ ok: true, config: { ...providers.providers[0], is_active: true }, active: "minimax", message: "已切换到 MiniMax" }),
    llmTest: overrides.llmTest ?? vi.fn().mockResolvedValue({} as LlmTestResult),
    workspaceSettings: overrides.workspaceSettings ?? vi.fn().mockResolvedValue({ workspace: { memory_enabled: true } }),
    updateWorkspaceSettings: overrides.updateWorkspaceSettings ?? vi.fn().mockResolvedValue({ ok: true, workspace: { memory_enabled: false } }),
  };
  vi.spyOn(settingsApi, "providersList").mockImplementation(spy.providersList);
  vi.spyOn(settingsApi, "providerSave").mockImplementation(spy.providerSave);
  vi.spyOn(settingsApi, "providerGet").mockImplementation(spy.providerGet);
  vi.spyOn(settingsApi, "providerDelete").mockImplementation(spy.providerDelete);
  vi.spyOn(settingsApi, "llmActivate").mockImplementation(spy.llmActivate);
  vi.spyOn(settingsApi, "llmTest").mockImplementation(spy.llmTest);
  vi.spyOn(settingsApi, "workspaceSettings").mockImplementation(spy.workspaceSettings);
  vi.spyOn(settingsApi, "updateWorkspaceSettings").mockImplementation(spy.updateWorkspaceSettings);
  return spy;
}

beforeEach(() => {
  vi.restoreAllMocks();
  useSessionStore.setState({ currentWorkspaceId: "ws-settings", currentSessionId: null });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Settings — LLM Provider configuration v2", () => {
  it("加载后渲染 provider sidebar + 表单", async () => {
    mockApi();
    render(<Settings />);
    await waitFor(() => {
      expect(screen.getByTestId("provider-sidebar")).toBeInTheDocument();
    });
    // 7 provider cards
    expect(screen.getByTestId("provider-minimax")).toBeInTheDocument();
    expect(screen.getByTestId("provider-deepseek")).toBeInTheDocument();
    expect(screen.getByTestId("provider-ark")).toBeInTheDocument();
    expect(screen.getByTestId("provider-openai")).toBeInTheDocument();
    expect(screen.getByTestId("provider-anthropic")).toBeInTheDocument();
    expect(screen.getByTestId("provider-ollama")).toBeInTheDocument();
    expect(screen.getByTestId("provider-custom")).toBeInTheDocument();
    // Form fields
    expect(screen.getByTestId("field-base_url")).toBeInTheDocument();
    expect(screen.getByTestId("field-model")).toBeInTheDocument();
    expect(screen.getByTestId("field-api_key")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-enabled")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-safe_mode")).toBeInTheDocument();
    // Buttons
    expect(screen.getByTestId("btn-save-llm")).toBeInTheDocument();
    expect(screen.getByTestId("btn-apply-llm")).toBeInTheDocument();
    expect(screen.getByTestId("btn-test-llm")).toBeInTheDocument();
    expect(screen.getByTestId("btn-reset-llm")).toBeInTheDocument();
  });

  it("活跃 provider 卡片显示 '当前' 徽章", async () => {
    mockApi({ providersList: makeProviders("minimax") });
    render(<Settings />);
    await waitFor(() => screen.getByTestId("provider-minimax"));
    const card = screen.getByTestId("provider-minimax");
    expect(card.className).toContain("active");
    expect(card.textContent).toContain("当前");
  });

  it("点击 provider 卡片切换编辑", async () => {
    const providers = makeProviders("minimax");
    mockApi({
      providersList: providers,
      providerGet: vi.fn().mockResolvedValue({
        ok: true,
        config: {
          ...providers.providers[3], // openai
          provider: "openai",
          label: "OpenAI",
          hint: "api.openai.com",
          is_active: false,
        },
      }),
    });

    render(<Settings />);
    await waitFor(() => screen.getByTestId("provider-openai"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("provider-openai"));
    });

    // Should show OpenAI in form header (getAllByText since sidebar also shows it)
    await waitFor(() => {
      const els = screen.getAllByText("OpenAI");
      expect(els.length).toBeGreaterThanOrEqual(2); // sidebar card + form header
    });
  });

  it("点保存调 providerSave", async () => {
    const spy = mockApi();
    render(<Settings />);
    await waitFor(() => screen.getByTestId("toggle-enabled"));

    // Change temperature only (minimax starts enabled=true from makeProviders)
    fireEvent.change(screen.getByTestId("field-temperature"), { target: { value: "0.5" } });

    await act(async () => {
      fireEvent.click(screen.getByTestId("btn-save-llm"));
    });

    expect(spy.providerSave).toHaveBeenCalledWith(
      "minimax",
      expect.objectContaining({ enabled: true, temperature: 0.5 }),
    );
  });

  it("点应用调 llmActivate", async () => {
    const spy = mockApi();
    render(<Settings />);
    await waitFor(() => screen.getByTestId("btn-apply-llm"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("btn-apply-llm"));
    });

    expect(spy.llmActivate).toHaveBeenCalled();
    const call = spy.llmActivate.mock.calls[0];
    expect(call[0]).toBe("minimax"); // providerId
  });

  it("测试连接调 llmTest, 成功后显示 result", async () => {
    const testResult: LlmTestResult = {
      ok: true,
      provider: "minimax",
      model: "MiniMax-M3",
      llm_used: true,
      config_source: "ui_settings",
      policy_pass: true,
      response: "pong",
      safe_to_show: true,
      warnings: [],
      metadata: {},
    };
    mockApi({ llmTest: vi.fn().mockResolvedValue(testResult) });
    render(<Settings />);
    await waitFor(() => screen.getByTestId("btn-test-llm"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("btn-test-llm"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-result").textContent).toMatch(/LLM 可用/);
    });
    expect(screen.getByTestId("test-result").textContent).toContain("pong");
  });

  it("点重置弹 confirm, 确认后调 providerDelete", async () => {
    const del = vi.fn().mockResolvedValue({ ok: true, deleted: true });
    const get = vi.fn().mockResolvedValue({ ok: true, config: baseProvider });
    mockApi({ providerDelete: del, providerGet: get });
    render(<Settings />);
    await waitFor(() => screen.getByTestId("btn-reset-llm"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("btn-reset-llm"));
    });
    await waitFor(() => expect(confirmModule.confirm).toHaveBeenCalled());

    expect(del).toHaveBeenCalled();
  });

  it("api_key 显示/隐藏切换 password ↔ text", async () => {
    mockApi({ providersList: makeProviders("minimax") });
    render(<Settings />);
    await waitFor(() => screen.getByTestId("field-api_key"));

    const input = screen.getByTestId("field-api_key") as HTMLInputElement;
    expect(input.type).toBe("password");

    fireEvent.click(screen.getByTestId("btn-toggle-key-reveal"));
    expect((screen.getByTestId("field-api_key") as HTMLInputElement).type).toBe("text");

    fireEvent.click(screen.getByTestId("btn-toggle-key-reveal"));
    expect((screen.getByTestId("field-api_key") as HTMLInputElement).type).toBe("password");
  });

  it("safe_mode toggle 切换状态", async () => {
    mockApi();
    render(<Settings />);
    await waitFor(() => screen.getByTestId("toggle-safe_mode"));

    const toggle = screen.getByTestId("toggle-safe_mode");
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  it("记忆门控读取并写入当前 workspace", async () => {
    const spy = mockApi();
    render(<Settings />);
    await waitFor(() => screen.getByTestId("toggle-memory-enabled"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("toggle-memory-enabled"));
    });

    expect(spy.workspaceSettings).toHaveBeenCalledWith("ws-settings", expect.any(AbortSignal));
    expect(spy.updateWorkspaceSettings).toHaveBeenCalledWith(
      { memory_enabled: false },
      "ws-settings",
    );
  });

  it("加载失败时显示 error card", async () => {
    vi.spyOn(settingsApi, "workspaceSettings").mockResolvedValue({ workspace: { memory_enabled: true } });
    vi.spyOn(settingsApi, "providersList").mockRejectedValue(new Error("boom"));
    render(<Settings />);
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });
});

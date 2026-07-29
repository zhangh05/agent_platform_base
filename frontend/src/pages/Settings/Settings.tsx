/**
 * Settings — LLM Provider configuration (v2).
 *
 * Layout: left provider sidebar (cards) → right form panel.
 * Each provider has its own config file; click to edit, "应用" to activate.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { settingsApi } from "../../api";
import { EmptyState, LoadingState } from "../../components/common";
import { Button, Input, FormField } from "../../components/ui";
import { confirm } from "../../components/ConfirmDialog";
import { useSessionStore } from "../../stores/session";
import { useToastStore } from "../../stores/toast";
import { isApiError } from "../../types";
import type { ProviderConfig, LlmTestResult } from "../../types";
import { sanitizeAssistantText } from "../../utils/displayText";
import { formatDate } from "../../utils/format";

/* ──────────────────────── Provider Presets ──────────────────────── */

interface ProviderPreset {
  id: string;
  label: string;
  base_url: string;
  model: string;
  hint: string;
}

const PROVIDER_PRESETS: ProviderPreset[] = [
  { id: "minimax", label: "MiniMax", base_url: "https://api.minimaxi.com/v1", model: "MiniMax-M3", hint: "api.minimaxi.com" },
  { id: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat", hint: "api.deepseek.com" },
  { id: "ark", label: "方舟 (豆包)", base_url: "https://ark.cn-beijing.volces.com/api/coding/v3", model: "ark-code-latest", hint: "ark.volces.com" },
  { id: "openai", label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", hint: "api.openai.com" },
  { id: "anthropic", label: "Anthropic", base_url: "https://api.anthropic.com/v1", model: "claude-3-haiku-20240307", hint: "api.anthropic.com" },
  { id: "ollama", label: "Ollama (本地)", base_url: "http://localhost:11434/v1", model: "llama3.1", hint: "localhost:11434" },
  { id: "custom", label: "自定义", base_url: "", model: "", hint: "OpenAI 兼容 API" },
];

const presetMap = new Map(PROVIDER_PRESETS.map((p) => [p.id, p]));

/* ──────────────────────── Settings Page ──────────────────────── */

export function Settings() {
  const toast = useToastStore((s) => s.show);
  const currentWorkspaceId = useSessionStore((s) => s.currentWorkspaceId);

  // ── State ──
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Partial<ProviderConfig> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null);
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [apiKeyDirty, setApiKeyDirty] = useState(false);
  const [apiKeyRevealed, setApiKeyRevealed] = useState(false);
  const [clearKeyOnSave, setClearKeyOnSave] = useState(false);
  const aliveRef = useRef(true);

  // ── Workspace long-term memory setting ──
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [memorySaving, setMemorySaving] = useState(false);
  const [memoryLoaded, setMemoryLoaded] = useState(false);

  // ── Load workspace settings on mount ──
  useEffect(() => {
    const ctrl = new AbortController();
    if (!currentWorkspaceId) {
      setMemoryLoaded(true);
      return () => ctrl.abort();
    }
    setMemoryLoaded(false);
    settingsApi.workspaceSettings(currentWorkspaceId, ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setMemoryEnabled(res?.workspace?.memory_enabled !== false);
        setMemoryLoaded(true);
      })
      .catch(() => {
        if (ctrl.signal.aborted) return;
        setMemoryLoaded(true);
      });
    return () => ctrl.abort();
  }, [currentWorkspaceId]);

  // ── Load on mount ──
  useEffect(() => {
    aliveRef.current = true;
    const ctrl = new AbortController();
    setLoading(true);
    settingsApi.providersList(ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;

        const list = Array.isArray(res?.providers) ? res.providers : [];
        const active = res?.active ?? "";

        if (list.length === 0) {
          setProviders([]);
          setActiveId("");
          setSelectedId("");
          setLoading(false);
          return;
        }

        setProviders(list);
        setActiveId(active);
        const act = list.find((p) => p.is_active) ?? list[0];
        if (act) {
          setSelectedId(act.provider);
          setDraft({ ...act });
        }
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(isApiError(e) ? e.message : String(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => {
      aliveRef.current = false;
      ctrl.abort();
    };
  }, []);

  // ── Select provider → load its config ──
  const selectProvider = useCallback(async (providerId: string) => {
    setSelectedId(providerId);
    setTestResult(null);
    setApiKeyDirty(false);
    setApiKeyDraft("");
    setClearKeyOnSave(false);
    setApiKeyRevealed(false);

    // Check local cache first
    const cached = providers.find((p) => p.provider === providerId);
    if (cached) {
      setDraft({ ...cached });
      return;
    }
    // Fetch from server
    try {
      const res = await settingsApi.providerGet(providerId);
      setDraft({ ...res.config });
    } catch {
      // Fallback to preset
      const preset = presetMap.get(providerId);
      if (preset) {
        setDraft({
          provider: preset.id,
          label: preset.label,
          enabled: false,
          base_url: preset.base_url,
          model: preset.model,
          temperature: 0.2,
          max_tokens: 4096,
          safe_mode: true,
          key_configured: false,
          is_active: false,
        });
      }
    }
  }, [providers]);

  // ── Refresh single provider in list ──
  const refreshProvider = useCallback((config: ProviderConfig) => {
    setProviders((prev) =>
      prev.map((p) => (p.provider === config.provider
        ? { ...config, api_key: undefined } // never store key in state
        : p))
    );
    setActiveId(config.is_active ? config.provider : activeId);
  }, [activeId]);

  // ── Actions ──

  async function onSave() {
    if (!draft || !selectedId) return;
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        enabled: draft.enabled,
        base_url: draft.base_url,
        model: draft.model,
        temperature: draft.temperature,
        max_tokens: draft.max_tokens,
        safe_mode: draft.safe_mode,
      };
      if (apiKeyDirty) {
        if (clearKeyOnSave) payload.clear_api_key = true;
        else if (apiKeyDraft) payload.api_key = apiKeyDraft;
      }
      const res = await settingsApi.providerSave(selectedId, payload);
      refreshProvider(res.config);
      setDraft({ ...res.config });
      setApiKeyDirty(false);
      setApiKeyDraft("");
      setClearKeyOnSave(false);
      setApiKeyRevealed(false);
      toast({ kind: "success", title: `${draft.label ?? selectedId} 配置已保存` });
    } catch (e: unknown) {
      toast({ kind: "error", title: "保存失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  async function onApply() {
    if (!draft || !selectedId) return;
    setApplying(true);
    try {
      const payload: Record<string, unknown> = {
        enabled: draft.enabled,
        base_url: draft.base_url,
        model: draft.model,
        temperature: draft.temperature,
        max_tokens: draft.max_tokens,
        safe_mode: draft.safe_mode,
      };
      if (apiKeyDirty) {
        if (clearKeyOnSave) payload.clear_api_key = true;
        else if (apiKeyDraft) payload.api_key = apiKeyDraft;
      }
      const res = await settingsApi.llmActivate(selectedId, payload);
      refreshProvider(res.config);
      setDraft({ ...res.config });
      setActiveId(selectedId);
      setApiKeyDirty(false);
      setApiKeyDraft("");
      setClearKeyOnSave(false);
      setApiKeyRevealed(false);
      toast({ kind: "success", title: res.message ?? `已切换到 ${draft.label ?? selectedId}` });
    } catch (e: unknown) {
      toast({ kind: "error", title: "应用失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setApplying(false);
    }
  }

  async function onTest() {
    if (!draft || !selectedId) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await settingsApi.llmTest({
        message: "Reply with OK.",
        base_url: draft.base_url ?? undefined,
        model: draft.model ?? undefined,
        provider: selectedId,
        api_key: apiKeyDirty ? (clearKeyOnSave ? undefined : (apiKeyDraft || undefined)) : undefined,
      });
      setTestResult(res);
      toast({
        kind: res.llm_used ? "success" : "warning",
        title: res.llm_used ? "LLM 可用" : "LLM 不可用",
        body: res.fallback_reason ? `fallback_reason=${res.fallback_reason}` : `model=${res.model ?? "?"}`,
      });
    } catch (e: unknown) {
      toast({ kind: "error", title: "测试请求失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  }

  async function onReset() {
    if (!selectedId) return;
    const label = draft?.label ?? selectedId;
    const ok = await confirm({ title: `确认重置 ${label} 配置？`, body: "将恢复为默认值。", destructive: true, confirmLabel: "重置" });
    if (!ok) return;
    setSaving(true);
    try {
      await settingsApi.providerDelete(selectedId);
      const res = await settingsApi.providerGet(selectedId);
      refreshProvider(res.config);
      setDraft({ ...res.config });
      setApiKeyDirty(false);
      setApiKeyDraft("");
      setClearKeyOnSave(false);
      setTestResult(null);
      toast({ kind: "success", title: `${label} 已重置为默认配置` });
    } catch (e: unknown) {
      toast({ kind: "error", title: "重置失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  // ── Memory gating toggle ──
  async function onMemoryEnabledChange(enabled: boolean) {
    if (!currentWorkspaceId) {
      toast({ kind: "warning", title: "未选择工作区", body: "请先在左侧选择工作区" });
      return;
    }
    setMemorySaving(true);
    try {
      await settingsApi.updateWorkspaceSettings({ memory_enabled: enabled }, currentWorkspaceId);
      setMemoryEnabled(enabled);
      toast({
        kind: "success",
        title: enabled ? "已启用长期记忆" : "已关闭自动长期记忆",
      });
    } catch (e: unknown) {
      toast({
        kind: "error",
        title: "设置失败",
        body: isApiError(e) ? e.message : String(e),
      });
    } finally {
      setMemorySaving(false);
    }
  }
  const selectedPreset = selectedId ? presetMap.get(selectedId) : null;
  const isActiveProvider = selectedId === activeId;
  const isBusy = saving || applying || testing;

  // ── Render states ──

  if (loading) {
    return (
      <div className="page" data-testid="page-settings">
        <PageHeader />
        <div className="page-body"><LoadingState /></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page" data-testid="page-settings">
        <PageHeader />
        <div className="page-body">
          <div className="card card-danger-border settings-error-text">{error}</div>
        </div>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="page" data-testid="page-settings">
        <PageHeader />
        <div className="page-body"><EmptyState text="后端未返回 LLM 配置" /></div>
      </div>
    );
  }

  return (
    <div className="page" data-testid="page-settings">
      <PageHeader activeId={activeId} />
      <div className="page-body no-pad">
        <details className="settings-help">
          <summary>💡 使用帮助</summary>
          <div className="settings-help-body">
            左侧选择 LLM 厂商 → 填写 API Key 和参数 → 保存生效。支持 DeepSeek、OpenAI、Claude 等。
          </div>
        </details>
        <div className="settings-layout">
          {/* ── Left: Provider sidebar ── */}
          <aside className="provider-sidebar" data-testid="provider-sidebar">
            <div className="provider-sidebar-label">LLM 厂商</div>
            {PROVIDER_PRESETS.map((preset) => {
              const prov = providers.find((p) => p.provider === preset.id);
              const active = preset.id === activeId;
              const selected = preset.id === selectedId;
              return (
                <button
                  key={preset.id}
                  type="button"
                  className={"provider-card" + (active ? " active" : "") + (selected ? " selected" : "")}
                  onClick={() => selectProvider(preset.id)}
                  data-testid={`provider-${preset.id}`}
                >
                  <div className="provider-card-top">
                    <span className="provider-card-label">{preset.label}</span>
                    {active && <span className="provider-badge active-badge">当前</span>}
                  </div>
                  <div className="provider-card-hint">{preset.hint}</div>
                  <div className="provider-card-meta">
                    {prov?.key_configured ? (
                      <span className="success-text text-xs">✓ key 已配置</span>
                    ) : (
                      <span className="muted text-xs">未配置 key</span>
                    )}
                  </div>
                </button>
              );
            })}
          </aside>

          {/* ── Right: Form panel ── */}
          <section className="settings-form-panel">
            {selectedPreset && (
              <div className="settings-form-card" data-testid="form-card">
                {/* Header */}
                <div className="settings-form-header">
                  <div>
                    <h2 className="settings-form-title">
                      {selectedPreset.label}
                      {isActiveProvider && (
                        <span className="badge ok ml-2">当前活跃</span>
                      )}
                    </h2>
                    <div className="muted text-xs">{selectedPreset.hint}</div>
                  </div>
                  <div className="settings-toggle-row">
                    <ToggleField
                      label="启用"
                      hint={draft.enabled ? "LLM 已启用" : "LLM 已关闭"}
                      checked={!!draft.enabled}
                      onChange={(v) => setDraft({ ...draft, enabled: v })}
                      testid="toggle-enabled"
                    />
                  </div>
                </div>

                {/* Fields */}
                <div className="settings-row">
                  <TextField label="base_url" value={draft.base_url ?? ""} onChange={(v) => setDraft({ ...draft, base_url: v })} testid="field-base_url" placeholder="http:// 或 https://" />
                </div>

                <div className="settings-row">
                  <TextField label="model" value={draft.model ?? ""} onChange={(v) => setDraft({ ...draft, model: v })} testid="field-model" placeholder="模型名称" />
                </div>

                <div className="settings-row">
                  <ApiKeyField
                    configured={!!draft.key_configured}
                    preview={draft.key_preview}
                    revealed={apiKeyRevealed}
                    onRevealToggle={() => setApiKeyRevealed((v) => !v)}
                    draft={apiKeyDraft}
                    onDraftChange={(v) => { setApiKeyDraft(v); setApiKeyDirty(true); setClearKeyOnSave(false); }}
                    clearRequested={clearKeyOnSave}
                    onClearToggle={(v) => {
                      setClearKeyOnSave(v);
                      if (v) { setApiKeyDraft(""); setApiKeyDirty(true); }
                    }}
                  />
                </div>

                <div className="settings-row-grid">
                  <NumberField label="temperature" value={draft.temperature ?? 0.2} min={0} max={2} step={0.1} onChange={(v) => setDraft({ ...draft, temperature: v })} testid="field-temperature" />
                  <NumberField label="max_tokens" value={draft.max_tokens ?? 4096} min={1} max={128000} step={100} onChange={(v) => setDraft({ ...draft, max_tokens: v })} testid="field-max_tokens" />
                </div>

                <div className="settings-row settings-row-compact">
                  <ToggleField
                    label="safe_mode"
                    hint="阻止伪造执行结果或泄露敏感输出"
                    checked={!!draft.safe_mode}
                    onChange={(v) => setDraft({ ...draft, safe_mode: v })}
                    testid="toggle-safe_mode"
                  />
                </div>

                {/* Test result */}
                {testResult && (
                  <div
                    className={"card mt-3 settings-test-result " + (testResult.llm_used ? "card-ok-border" : "card-warn-border")}
                    data-testid="test-result"
                  >
                    <div className="mb-1">
                      <strong>{testResult.llm_used ? "✓ LLM 可用" : "✗ LLM 不可用"}</strong>
                      <span className="muted ml-2">
                        provider={testResult.provider ?? "?"} · model={testResult.model ?? "?"} · source={testResult.config_source}
                      </span>
                    </div>
                    {testResult.fallback_reason && (
                      <div className="muted mb-1">fallback: {testResult.fallback_reason}</div>
                    )}
                    {testResult.response && (
                      <pre className="test-result-pre">
                        {sanitizeAssistantText(testResult.response)}
                      </pre>
                    )}
                    {testResult.warnings?.length > 0 && (
                      <div className="muted text-xs mt-1">
                        warnings: {testResult.warnings.join("; ")}
                      </div>
                    )}
                  </div>
                )}

                {/* Actions */}
                <div className="settings-actions">
                  <Button type="button" onClick={onTest} disabled={isBusy} data-testid="btn-test-llm">
                    {testing ? "测试中…" : "🧪 测试连接"}
                  </Button>
                  <Button type="button" onClick={onSave} disabled={isBusy} data-testid="btn-save-llm">
                    {saving ? "保存中…" : "💾 保存"}
                  </Button>
                  <Button type="button" variant="primary" onClick={onApply} disabled={isBusy} data-testid="btn-apply-llm">
                    {applying ? "应用中…" : "⚡ 应用"}
                  </Button>
                  <span className="spacer" />
                  {draft.updated_at && (
                    <span className="muted text-xs" data-testid="last-updated">
                      {formatDate(draft.updated_at, "compact")}
                    </span>
                  )}
                  <Button
                    type="button" variant="danger-ghost" onClick={onReset}
                    disabled={isBusy} title="重置为默认值" data-testid="btn-reset-llm"
                  >
                    🗑 重置
                  </Button>
                </div>
              </div>
            )}

            <LongTermMemoryCard
              enabled={memoryEnabled}
              loading={memorySaving}
              loaded={memoryLoaded}
              onChange={onMemoryEnabledChange}
            />
          </section>
        </div>
      </div>
    </div>
  );
}

/* ──────────────────────── Sub-components ──────────────────────── */

function PageHeader({ activeId }: { activeId?: string }) {
  const activePreset = activeId ? presetMap.get(activeId) : null;
  return (
    <div className="page-header">
      <div>
        <h1>系统设置<span className="title-suffix"> · Settings</span></h1>
        <div className="subtitle">
          LLM Provider 配置
          {activePreset && (
            <span className="badge ok ml-2 text-xs">
              {activePreset.label}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function TextField({ label, value, onChange, testid, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; testid?: string; placeholder?: string;
}) {
  return (
    <FormField label={label}>
      <Input type="text" value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid} spellCheck={false} autoComplete="off" placeholder={placeholder} />
    </FormField>
  );
}

function NumberField({ label, value, min, max, step, onChange, testid }: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; testid?: string;
}) {
  return (
    <FormField label={label}>
      <Input type="number" value={value} min={min} max={max} step={step} onChange={(e) => { const n = Number(e.target.value); if (!Number.isNaN(n)) onChange(n); }} data-testid={testid} />
    </FormField>
  );
}

function ToggleField({ label, hint, checked, onChange, testid }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; testid?: string;
}) {
  return (
    <FormField label={label}>
      <button
        type="button"
        className={"toggle" + (checked ? " on" : "")}
        onClick={() => onChange(!checked)}
        role="switch"
        aria-checked={checked}
        data-testid={testid}
      >
        <span className="toggle-knob" />
        <span className="toggle-label">{hint ? <span className="muted text-xs">{hint}</span> : null}</span>
      </button>
    </FormField>
  );
}

function ApiKeyField({
  configured, preview, revealed, onRevealToggle,
  draft, onDraftChange, clearRequested, onClearToggle,
}: {
  configured: boolean;
  preview: string | null | undefined;
  revealed: boolean;
  onRevealToggle: () => void;
  draft: string;
  onDraftChange: (v: string) => void;
  clearRequested: boolean;
  onClearToggle: (v: boolean) => void;
}) {
  const placeholder = configured
    ? revealed ? "粘贴新 key 替换当前" : "已配置 · 输入新 key 替换"
    : "粘贴 API key";
  return (
    <FormField label="api_key">
      <div className="row-flex-sm">
        <Input
          type={revealed ? "text" : "password"}
          value={clearRequested ? "" : draft}
          onChange={(e) => onDraftChange(e.target.value)}
          placeholder={placeholder}
          data-testid="field-api_key"
          autoComplete="off"
          spellCheck={false}
          className="mono flex-1"
        />
        {configured && !draft && !clearRequested && (
          <span className="muted text-xs" data-testid="api-key-preview"><code>{preview ?? "已配置"}</code></span>
        )}
        <Button type="button" onClick={onRevealToggle} data-testid="btn-toggle-key-reveal" title={revealed ? "隐藏" : "显示"}>
          {revealed ? "隐藏" : "显示"}
        </Button>
      </div>
      <div className="row-flex-sm mt-1">
        {configured ? <span className="success-text text-xs">✓ 已配置</span> : <span className="muted text-xs">未配置</span>}
        {configured && (
          <label className="row-flex-xs">
            <input type="checkbox" checked={clearRequested} onChange={(e) => onClearToggle(e.target.checked)} data-testid="cb-clear-key" />
            <span className="muted text-xs">保存时清空 key</span>
          </label>
        )}
      </div>
    </FormField>
  );
}

/* ──────────────────────── Long-term Memory Card ──────────────────────── */

function LongTermMemoryCard({
  enabled, loading, loaded, onChange,
}: {
  enabled: boolean;
  loading: boolean;
  loaded: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="card mt-3 memory-gating-card">
      <div className="row-flex memory-gating-header">
        <div className="flex-1">
          <div className="text-md memory-gating-title-text">长期记忆</div>
          <div className="muted text-xs memory-gating-desc">
            Agent 自动学习明确偏好、项目规则、稳定事实和可复用经验；你只需要管理结果。
          </div>
        </div>
        <div className="row-flex-sm">
          <span className={`text-xs memory-gating-status ${enabled ? "memory-gating-status-on" : "memory-gating-status-off"}`}>
            {enabled ? "已启用" : "已关闭"}
          </span>
          <button
            type="button"
            className={"toggle" + (enabled ? " on" : "")}
            disabled={loading}
            onClick={() => onChange(!enabled)}
            role="switch"
            aria-checked={enabled}
            data-testid="toggle-memory-enabled"
          >
            <span className="toggle-knob" />
          </button>
        </div>
      </div>
      {loaded && (
        <div className={"memory-gating-box " + (enabled ? "ok" : "default")}>
          <div className="memory-gating-title">
            <span className="mr-1">{enabled ? "🧠" : "⏸"}</span>
            当前：{enabled ? "自动长期记忆已启用" : "自动长期记忆已关闭"}
          </div>
          <div className="memory-gating-body">
            系统会先判断本轮是否发生了值得学习的事件；没有记忆信号会直接跳过，不会每轮硬写。
          </div>
          <div className="memory-gating-body">
            命中后由 LLM 生成 create/update/ignore/expire/conflict 提案，规则负责拦截密钥、垃圾内容和冲突风险。
          </div>
          <div className="memory-gating-footer">
            <span className="opacity-60">⏱</span>
            {enabled ? "用户可在记忆页查看、确认、编辑或删除结果" : "关闭后仍可手动新建记忆"}
          </div>
        </div>
      )}
    </div>
  );
}

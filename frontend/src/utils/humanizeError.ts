/**
 * Human-readable error classifier for agent / provider failures.
 *
 * Shared by AgentWorkbench so the same message + retry hint appears wherever
 * a run / message ends in an error. Behaviour-preserved extraction from the
 * `_humanFailure` private helper that previously lived in AgentWorkbench.tsx.
 */

export interface HumanFailure {
  msg: string;
  retryable: boolean;
}

export function humanFailure(errorType: string | undefined, errorText: string): HumanFailure {
  const et = (errorType ?? "").toLowerCase();
  const text = (errorText ?? "").toLowerCase();
  // Provider errors
  if (et.includes("provider_timeout") || text.includes("timed out") || text.includes("超时"))
    return { msg: "模型请求超时，可能是供应商响应慢或网络抖动。可重试或缩短问题。", retryable: true };
  if (et.includes("provider_error") || text.includes("provider"))
    return { msg: "模型服务异常，请稍后重试。", retryable: true };
  // Auth/permission
  if (text.includes("disabled") || text.includes("llm is disabled"))
    return { msg: "LLM 未启用，请前往系统设置开启并配置 API Key。", retryable: false };
  if (text.includes("api_key") || text.includes("authentication"))
    return { msg: "API 密钥未配置或已失效，请重新设置。", retryable: false };
  // Tool sandbox
  if (text.includes("forbidden function") || text.includes("forbidden_import"))
    return { msg: "Agent 尝试使用被限制的操作，系统自动拦截。可重新提问让 Agent 换一种方式。", retryable: true };
  if (text.includes("syntax error") || text.includes("unterminated"))
    return { msg: "Agent 生成的代码有语法错误，重新生成通常可解决。", retryable: true };
  // Caller identity
  if (text.includes("caller_identity") || text.includes("requested_by"))
    return { msg: "系统调用链身份缺失，请刷新页面后重试。", retryable: false };
  // Default
  return { msg: text, retryable: true };
}

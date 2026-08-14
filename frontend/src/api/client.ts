import axios, { AxiosError, AxiosInstance, AxiosRequestConfig } from "axios";
import type { ApiError, ApiErrorCode } from "../types";

/**
 * HTTP client wrapper. All API calls go through `apiClient`.
 * Network errors are uniformly converted to `ApiError` so callers
 * can handle them without inspecting axios internals.
 *
 * Base URL precedence:
 *  1. `VITE_API_BASE` environment variable (production / staging)
 *  2. Vite dev-server proxy at `/api` (default for `npm run dev`)
 *  3. Fallback to `/api` (same-origin)
 */

/**
 * Per-call timeout policy.
 *
 * 之前默认 12s 对所有调用一刀切, 但 agent turn (web search + LLM + tool
 * calls) 实测 30-60s, 偶尔 100s+. 12s 必然超时. 改成「默认 30s + 按
 * 端点可覆盖」, 同时把超时错误信息改成中文可读 + 给出建议.
 */
export const TIMEOUTS = {
  /** 默认: 列表/获取/健康检查等快调用 */
  default: 30_000,
  /** Agent turn: 含 LLM + 工具调用 + web search, 可达 60-120s */
  agentTurn: 180_000,
  /** LLM test 端点 (单次 LLM 调用) */
  llmTest: 60_000,
  /** knowledge 从 artifact 导入 (含索引) */
  knowledgeImport: 60_000,
  /** 后端摘要 (含 LLM 调用) */
  summarize: 60_000,
} as const;

const envBase = import.meta.env.VITE_API_BASE ?? "";

export function realtimeEndpoint(path: string, configuredBase = envBase): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (typeof window === "undefined") return normalizedPath;
  const origin = new URL(configuredBase || window.location.origin, window.location.origin);
  const protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${origin.host}${normalizedPath}`;
}

const baseURL = envBase
  ? envBase.replace(/\/+$/, "") + "/api"
  : "/api";

export const apiClient: AxiosInstance = axios.create({
  baseURL,
  timeout: TIMEOUTS.default,
  withCredentials: true,
  headers: {
    Accept: "application/json",
  },
});

export function getApiAccessToken(): string {
  if (typeof window === "undefined") return "";
  // Browser sessions are preferred. A build-time token would be embedded in
  // public assets, so explicit API-token mode is tab/session scoped only.
  return window.sessionStorage.getItem("LZCORE_API_TOKEN") || "";
}

let requestSeq = 0;
function nextRequestId(): string {
  requestSeq += 1;
  return `req-${Date.now()}-${requestSeq}`;
}

/**
 * Network failure cannot distinguish an unstarted write from a write whose
 * response was lost. Only replay inherently safe reads here. Endpoints with a
 * server-enforced idempotency contract must opt in explicitly at their call
 * site rather than inheriting a broad transport retry.
 */
function isRetryableReadMethod(method?: string): boolean {
  const normalized = String(method || "GET").toUpperCase();
  return normalized === "GET" || normalized === "HEAD" || normalized === "OPTIONS";
}

apiClient.interceptors.request.use((config) => {
  config.headers = config.headers ?? {};
  (config.headers as Record<string, string>)["X-Request-Id"] = nextRequestId();
  // Inject auth header if token is configured
  const token = getApiAccessToken();
  if (token) {
    (config.headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

function toApiError(err: unknown, url?: string): ApiError {
  if (axios.isAxiosError(err)) {
    const ax = err as AxiosError<{ message?: string; error?: string; summary?: string }>;
    if (ax.code === "ECONNABORTED") {
      return mkError(
        "timeout",
        0,
        `请求超时 (>${Math.round((ax.config?.timeout ?? 0) / 1000)}s) · Agent turn / LLM 调用可能耗时较长, 建议稍后重试或简化问题`,
        url,
        ax.response,
      );
    }
    if (ax.code === "ERR_CANCELED") {
      return mkError("aborted", 0, "请求已取消", url, ax.response);
    }
    if (!ax.response) {
      return mkError("network", 0, "后端不可达 · 检查 Flask 8011 端口 / Vite proxy", url, undefined);
    }
    const status = ax.response.status;
    const body = ax.response.data as
      | { message?: string; error?: string; summary?: string }
      | undefined;
    const msg =
      body?.message || body?.error || body?.summary || ax.message || "请求失败";
    let code: ApiErrorCode = status >= 500 ? "http_5xx" : "http_4xx";
    if (status === 401) code = "http_4xx";
    if (status === 403) code = "http_4xx";
    if (status === 404) code = "http_4xx";
    if (status === 408) code = "timeout";
    if (status === 413 || status === 422) code = "http_4xx";
    if (status === 429) code = "rate_limited";
    return mkError(code, status, msg, url, ax.response);
  }
  if (err instanceof SyntaxError) {
    return mkError("parse", 0, "响应解析失败", url, undefined);
  }
  return mkError("unknown", 0, "未知错误", url, undefined);
}

function mkError(
  code: ApiErrorCode,
  status: number,
  message: string,
  url: string | undefined,
  response: AxiosError["response"],
): ApiError {
  return {
    ok: false,
    code,
    status,
    message,
    url,
    request_id: (response?.headers as Record<string, string> | undefined)?.["x-request-id"],
    details: response?.data,
    timestamp: new Date().toISOString(),
  };
}

/**
 * Public request helper. Throws `ApiError` on any failure.
 * On success, returns the raw parsed JSON body. We do NOT normalize here —
 * pages normalize against their typed contracts.
 *
 * @param config    Axios request config
 * @param signal    AbortSignal (page-level cancellation)
 * @param timeoutMs Per-call timeout override. Defaults to TIMEOUTS.default.
 */
export async function apiRequest<T = unknown>(
  config: AxiosRequestConfig,
  signal?: AbortSignal,
  timeoutMs?: number,
): Promise<T> {
  const maxRetries = 3;
  let lastError: ApiError | null = null;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const combined: AxiosRequestConfig = {
        ...config,
        timeout: timeoutMs ?? TIMEOUTS.default,
      };
      if (signal) {
        combined.signal = signal;
      }
      const res = await apiClient.request<T>(combined);
      return res.data;
    } catch (err) {
      const ae = toApiError(err, (err as AxiosError)?.config?.url);
      const retryableTransportFailure = ae.code === "network" || ae.code === "timeout" || ae.code === "rate_limited" ||
        (ae.status >= 500 && ae.status < 600);
      const retryable = isRetryableReadMethod(config.method) && retryableTransportFailure;
      if (!retryable || signal?.aborted || attempt === maxRetries - 1) {
        throw ae;
      }
      lastError = ae;
      // Honour Retry-After header (seconds) when the upstream pinned it; otherwise
      // exponential backoff: 500ms, 1000ms, 2000ms — clamped to 5s ceiling.
      const axErr = err as AxiosError;
      const retryAfterHeader = (axErr.response?.headers as Record<string, string> | undefined)?.["retry-after"];
      const retryAfterMs = retryAfterHeader ? Math.max(0, parseInt(retryAfterHeader, 10) * 1000) : 0;
      const delay = retryAfterMs > 0
        ? Math.min(retryAfterMs, 5000)
        : Math.min(500 * (2 ** attempt), 3000);
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, delay);
        const onAbort = () => {
          clearTimeout(timer);
          reject(mkError("aborted", 0, "请求已取消", (err as AxiosError)?.config?.url, undefined));
        };
        if (signal?.aborted) {
          onAbort();
        } else {
          signal?.addEventListener("abort", onAbort, { once: true });
        }
      });
    }
  }
  throw lastError!;
}

export const apiBaseURL = baseURL;

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import type { ApiError, AsyncState } from "../types";
import { isApiError, isError, isLoading } from "../types";

/* ── AsyncState renderer ── */

export interface AsyncViewProps<T> {
  state: AsyncState<T>;
  children: (data: T) => ReactNode;
  emptyText?: string;
  emptyHint?: string;
  loadingText?: string;
  onRetry?: () => void;
  skeleton?: "list" | "table";
}

export function AsyncView<T>({
  state,
  children,
  emptyText = "暂无数据",
  emptyHint,
  loadingText = "加载中…",
  onRetry,
  skeleton,
}: AsyncViewProps<T>) {
  if (isLoading(state)) {
    if (skeleton === "list") return <SkeletonList />;
    if (skeleton === "table") return <SkeletonTable />;
    return (
      <div className="empty" data-testid="loading-state">
        <div className="empty-icon">
          <span className="spinner" />
        </div>
        <div className="empty-text">{loadingText}</div>
      </div>
    );
  }
  if (isError(state)) {
    return <ErrorState error={state.error} onRetry={onRetry} />;
  }
  if (state.kind === "empty") {
    return <EmptyState text={emptyText} hint={emptyHint} />;
  }
  if (state.kind === "success") {
    return <>{children(state.data)}</>;
  }
  return <div className="empty"><div className="empty-text">未初始化</div></div>;
}

import { IconAlert } from "./Icon";

/* ── Error state ── */

export function ErrorState({
  error,
  onRetry,
}: {
  error: ApiError;
  onRetry?: () => void;
}) {
  return (
    <div className="empty" data-testid="error-state">
      <div className="error-icon">
        <IconAlert size={20} />
      </div>
      <div className="error-text">{error.message}</div>
      <div className="error-hint">
        {error.code} · {error.status > 0 ? `HTTP ${error.status}` : "无响应"}
        {error.request_id ? ` · ${error.request_id}` : ""}
      </div>
      {onRetry && (
        <button className="btn sm" onClick={onRetry} type="button">
          重新加载
        </button>
      )}
    </div>
  );
}

/* ── Empty state ── */

export function EmptyState({
  text = "暂无数据",
  hint,
  action,
}: {
  text?: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty" data-testid="empty-state">
      <div className="empty-icon">
        <span>∅</span>
      </div>
      <div className="empty-text">{text}</div>
      {hint && <div className="empty-hint">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/* ── Skeleton loading placeholders ── */

type CssVars = CSSProperties & Record<`--${string}`, string>;

function skeletonSizeStyle(w: string, h: number): CssVars {
  return {
    "--skeleton-width": w,
    "--skeleton-height": `${h}px`,
  };
}

function skeletonListStyle(gap: number): CssVars {
  return { "--skeleton-gap": `${gap}px` };
}

function skeletonDelayStyle(index: number, step: number): CssVars {
  return { "--skeleton-delay": `${index * step}s` };
}

function sk(w: string, h: number) {
  return (
    <span
      className="skeleton"
      style={skeletonSizeStyle(w, h)}
    />
  );
}

export function SkeletonLine({ w = "60%", h = 14 }: { w?: string; h?: number }) {
  return sk(w, h);
}

export function SkeletonBlock({ h = 120, w = "100%" }: { h?: number; w?: string }) {
  return sk(w, h);
}

export function SkeletonList({ rows = 5, gap = 10 }: { rows?: number; gap?: number }) {
  return (
    <div className="skeleton-list" style={skeletonListStyle(gap)}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-list-row">
          <span className="skeleton skeleton-avatar" style={skeletonDelayStyle(i, 0.1)} />
          <span className="skeleton skeleton-line" style={skeletonDelayStyle(i, 0.1)} />
        </div>
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 4, cols = 3 }: { rows?: number; cols?: number }) {
  return (
    <div className="skeleton-table">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="skeleton-table-row">
          {Array.from({ length: cols }, (_, c) => (
            <span key={c} className="skeleton skeleton-cell" style={skeletonDelayStyle(r * cols + c, 0.08)} />
          ))}
        </div>
      ))}
    </div>
  );
}

/* ── Skeleton replace LoadingState ── */

export function LoadingState({ text = "加载中…", skeleton }: { text?: string; skeleton?: "list" | "table" }) {
  if (skeleton === "list") return <SkeletonList />;
  if (skeleton === "table") return <SkeletonTable />;
  return (
    <div className="empty" data-testid="loading-state">
      <div className="empty-icon">
        <span className="spinner" />
      </div>
      <div className="empty-text">{text}</div>
    </div>
  );
}

/* ── Badges & Status ── */

export type BadgeKind =
  | "ok"
  | "warn"
  | "err"
  | "info"
  | "pri"
  | "muted"
  | "planned"
  | "accent"
  | "s-pending"
  | "s-accepted"
  | "s-ignored"
  | "s-modified";

export function Badge({
  kind = "muted",
  children,
  withDot = false,
  style,
}: {
  kind?: BadgeKind;
  children: ReactNode;
  withDot?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <span className={`badge ${kind}`} data-testid={`badge-${kind}`} style={style}>
      {withDot && <span className="dot" />}
      {children}
    </span>
  );
}

export function StatusDot({
  status,
  label,
}: {
  status: "ok" | "warn" | "err" | "idle" | "loading" | "busy";
  label?: string;
}) {
  return (
    <span className="row-flex text-sm">
      <span className={`status-dot ${status}`} />
      {label && <span>{label}</span>}
    </span>
  );
}

/* ── Code block ── */

export function CodeBlock({
  children,
  language,
}: {
  children: string;
  language?: string;
}) {
  return (
    <pre data-testid="code-block" data-language={language}>
      {children}
    </pre>
  );
}

export function InlineCode({ children }: { children: ReactNode }) {
  return <code>{children}</code>;
}

/* ── Field (form row) ── */

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="col-flex field-col">
      <label
        htmlFor={htmlFor}
        className="field-label"
      >
        {label}
      </label>
      {children}
      {hint && (
        <span className="field-hint">{hint}</span>
      )}
    </div>
  );
}

/* ── Collapsible section (used by Inspector) ── */

export function Collapsible({
  title,
  defaultOpen = true,
  count,
  children,
  testid,
}: {
  title: ReactNode;
  defaultOpen?: boolean;
  count?: number;
  children: ReactNode;
  testid?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="inspector-section" data-testid={testid}>
      <button
        className={"inspector-section-title collapsible-toggle" + (open ? "" : " collapsed")}
        onClick={() => setOpen((o) => !o)}
        data-testid="collapsible-toggle"
        type="button"
      >
        <span className="collapsible-title">
          <span className="chev">▾</span>
          {title}
          {typeof count === "number" && count > 0 && (
            <span className="collapsible-count">
              {count}
            </span>
          )}
        </span>
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}

/* ── Hook: useAsync ── */

export function useAsync<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
  isEmpty?: (data: T) => boolean,
): {
  state: AsyncState<T>;
  reload: () => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ kind: "idle" });
  const [reloadSeq, setReloadSeq] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ kind: "loading" });
    fn(ctrl.signal)
      .then((data) => {
        if (ctrl.signal.aborted) return;
        if (isEmpty && isEmpty(data)) {
          setState({ kind: "empty", reason: "predicate" });
        } else if (Array.isArray(data) && data.length === 0) {
          setState({ kind: "empty", reason: "list is empty" });
        } else {
          setState({ kind: "success", data });
        }
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (isApiError(err)) {
          setState({ kind: "error", error: err });
        } else {
          setState({
            kind: "error",
            error: {
              ok: false,
              status: 0,
              code: "unknown",
              message: String(err),
              timestamp: new Date().toISOString(),
            },
          });
        }
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadSeq]);

  const reload = useCallback(() => setReloadSeq((s) => s + 1), []);
  return { state, reload };
}

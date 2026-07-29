import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary">
          <div className="error-boundary-icon">⚠</div>
          <h1 className="error-boundary-title">应用发生错误</h1>
          <p className="error-boundary-message">
            请刷新页面 (Cmd+Shift+R) 重试。如持续出现，请在系统设置的 LLM 配置中确认设置后，清除浏览器 localStorage 数据。
          </p>
          <details className="error-boundary-details">
            <summary className="error-boundary-summary">错误详情</summary>
            <pre className="error-boundary-stack">
              {this.state.error?.message ?? "Unknown"}
              {"\n\n"}
              {this.state.error?.stack ?? ""}
            </pre>
          </details>
          <button
            onClick={() => window.location.reload()}
            className="error-boundary-retry"
            type="button"
          >
            刷新页面
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

import { useEffect, useState } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { useSessionStore } from "../../../frontend/src/stores/session";

type Status = {
  extension_id: string;
  workspace_id: string;
  status: string;
  tool_id: string;
};

export default function ReferenceInsights() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    apiRequest<Status>({
      method: "GET",
      url: "/extensions/reference.insights/status",
      params: { workspace_id: workspaceId },
    }, controller.signal).then(setStatus).catch(() => setError("扩展状态暂时不可用"));
    return () => controller.abort();
  }, [workspaceId]);

  return (
    <div className="page">
      <header className="page-header ui-page-header">
        <div>
          <h1>扩展示例 <span>Extension Reference</span></h1>
          <p className="subtitle">验证独立扩展的工具、接口和页面均已接入平台。</p>
        </div>
      </header>
      <div className="page-body">
        <div className="stat-grid">
          <div className="stat-card">
            <div className="stat-value stat-value-ok">{status?.status === "ready" ? "正常" : "检查中"}</div>
            <div className="stat-label">扩展状态</div>
          </div>
          <div className="stat-card">
            <div className="stat-value stat-value-accent">1</div>
            <div className="stat-label">已注册工具</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">1</div>
            <div className="stat-label">已注册页面</div>
          </div>
        </div>
        <section className="ui-detail-panel stat-card-box">
          <div className="ui-detail-panel-head">
            <div>
              <h2 className="ui-detail-panel-title">端到端接入状态</h2>
              <p className="ui-detail-panel-subtitle">当前工作区：{workspaceId}</p>
            </div>
          </div>
          {error ? <p role="alert">{error}</p> : (
            <div className="ui-detail-panel-body">
              <p><strong>扩展标识：</strong>{status?.extension_id || "reference.insights"}</p>
              <p><strong>工具标识：</strong>{status?.tool_id || "reference.insights.summarize"}</p>
              <p><strong>接入方式：</strong>扩展清单自动发现，无需修改核心导航和路由表。</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

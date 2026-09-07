import { useEffect, useState } from "react";
import type { AgentResult } from "../../../types";
import { apiRequest } from "../../../api/client";
import { Button } from "../../../components/ui";

type Operation = {
  operation_id: string;
  status: string;
  digest: string;
  commands: string[];
  target?: { name?: string; host?: string; vendor?: string; connection?: { protocol?: string; port?: number } };
  decision?: { value?: string; note?: string };
  execution?: { result?: { status?: string; error?: string; command_results?: unknown[] } };
};

function interruptionIds(result?: AgentResult): string[] {
  const metadata = result?.metadata as Record<string, unknown> | undefined;
  const runtime = metadata?.ssot_runtime as Record<string, unknown> | undefined;
  const entries = runtime?.external_interruptions ?? metadata?.external_interruptions;
  if (!Array.isArray(entries)) return [];
  return entries.map((item) => String((item as Record<string, unknown>)?.interruption_id || "")).filter(Boolean);
}

export function ApprovalActions({ result, workspaceId, onResume }: {
  result?: AgentResult;
  workspaceId: string;
  onResume: (operationId: string) => void;
}) {
  const ids = interruptionIds(result);
  const [operations, setOperations] = useState<Record<string, Operation>>({});
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (!workspaceId || !ids.length) return;
    let active = true;
    void Promise.all(ids.map(async (operationId) => {
      const response = await apiRequest<{ operation: Operation }>({
        method: "GET",
        url: `/extensions/approval/operations/${encodeURIComponent(operationId)}`,
        params: { workspace_id: workspaceId },
      });
      return response.operation;
    })).then((items) => {
      if (!active) return;
      setOperations((previous) => ({ ...previous, ...Object.fromEntries(items.filter(Boolean).map((item) => [item.operation_id, item])) }));
    }).catch(() => {});
    return () => { active = false; };
  }, [workspaceId, ids.join(",")]);

  if (!ids.length) return null;
  const decide = async (operation: Operation, decision: "approve" | "reject" | "cancel") => {
    setBusy(operation.operation_id);
    try {
      const response = await apiRequest<{ operation: Operation }>({
        method: "POST",
        url: `/extensions/approval/operations/${encodeURIComponent(operation.operation_id)}/decision`,
        data: { workspace_id: workspaceId, decision },
      });
      const next = response.operation;
      setOperations((previous) => ({ ...previous, [operation.operation_id]: next }));
      if (["executed", "unknown", "rejected", "cancelled", "invalidated"].includes(next.status)) onResume(operation.operation_id);
    } finally {
      setBusy("");
    }
  };

  return <div className="approval-actions" aria-label="待决定操作">
    {ids.map((id) => {
      const operation = operations[id];
      if (!operation) return <div className="approval-card" key={id}>正在读取待决定操作…</div>;
      const target = operation.target || {};
      const waiting = operation.status === "pending";
      return <section className="approval-card" key={id} data-testid={`approval-${id}`}>
        <header><strong>{waiting ? "等待审批" : "审批结果"}</strong><span>{target.name || "设备"} · {target.host || "地址未知"}</span></header>
        <p>以下命令将按原始顺序执行；批准内容以 Digest <code>{operation.digest}</code> 绑定。</p>
        <pre>{operation.commands.join("\n")}</pre>
        {waiting ? <div className="approval-card-actions">
          <Button variant="primary" disabled={busy === id} onClick={() => void decide(operation, "approve")}>批准并执行</Button>
          <Button disabled={busy === id} onClick={() => void decide(operation, "reject")}>拒绝</Button>
          <Button variant="danger-ghost" disabled={busy === id} onClick={() => void decide(operation, "cancel")}>取消</Button>
        </div> : <p className="approval-state">当前状态：{operation.status}</p>}
      </section>;
    })}
  </div>;
}

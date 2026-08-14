/** E2E 15 — destructive workflow action reaches durable approval and reject resolution. */
import { test, expect } from "./fixtures";

test("15. workflow approval can be rejected and audited", async ({ api, workspaceId }) => {
  const workflowId = "e2e_approval_probe";
  const saved = await api.post("/api/workflows", {
    data: {
      workspace_id: workspaceId,
      workflow_id: workflowId,
      name: "E2E approval probe",
      description: "A harmless approval-only safety probe.",
      nodes: [{
        node_id: "destructive_probe",
        name: "Destructive approval probe",
        tool_id: "exec.run",
        arguments: { action: "shell", command: "rm -f /tmp/lzcore-e2e-never-created" },
        depends_on: [],
      }],
    },
  });
  expect(saved.status()).toBe(201);

  const executed = await api.post(`/api/workflows/${workflowId}/runs`, {
    data: { workspace_id: workspaceId, inputs: {} },
  });
  expect(executed.status()).toBeLessThan(500);

  const pending = await api.get(`/api/agent/approvals/pending?workspace_id=${workspaceId}`);
  expect(pending.ok()).toBeTruthy();
  const pendingBody = await pending.json();
  const request = (pendingBody.pending ?? []).find((item: { tool_id?: string }) => item.tool_id === "exec.run");
  expect(request?.approval_id).toBeTruthy();

  const resolved = await api.post(`/api/agent/approvals/${request.approval_id}/resolve`, {
    data: { workspace_id: workspaceId, decision: "reject", reason: "E2E safety probe" },
  });
  expect(resolved.ok()).toBeTruthy();

  const history = await api.get(`/api/agent/approvals/history?workspace_id=${workspaceId}`);
  const historyBody = await history.json();
  expect((historyBody.history ?? []).some((item: { approval_id?: string }) => item.approval_id === request.approval_id)).toBeTruthy();

  const rejectedRun = await api.get(`/api/workflow-runs/${(await executed.json()).run.run_id}?workspace_id=${workspaceId}`);
  expect((await rejectedRun.json()).run.status).toBe("rejected");

  const approvedWorkflowId = "e2e_approval_resume";
  expect((await api.post("/api/workflows", {
    data: {
      workspace_id: workspaceId,
      workflow_id: approvedWorkflowId,
      name: "E2E approval resume",
      nodes: [{
        node_id: "approved_probe",
        tool_id: "exec.run",
        arguments: { action: "shell", command: "rm -f /tmp/lzcore-e2e-never-created; echo RESUMED" },
        depends_on: [],
      }],
    },
  })).status()).toBe(201);
  const awaiting = await api.post(`/api/workflows/${approvedWorkflowId}/runs`, {
    data: { workspace_id: workspaceId, inputs: {} },
  });
  const awaitingBody = await awaiting.json();
  const approvePending = await api.get(`/api/agent/approvals/pending?workspace_id=${workspaceId}`);
  const approveRequest = ((await approvePending.json()).pending ?? []).find(
    (item: { run_id?: string }) => item.run_id === awaitingBody.run.run_id,
  );
  const approved = await api.post(`/api/agent/approvals/${approveRequest.approval_id}/resolve`, {
    data: { workspace_id: workspaceId, decision: "approve", reason: "E2E exact binding" },
  });
  expect(approved.ok()).toBeTruthy();
  const approvedBody = await approved.json();
  expect(approvedBody.runtime_result.workflow_run.status).toBe("succeeded");
  expect(approvedBody.runtime_result.workflow_run.run_id).toBe(awaitingBody.run.run_id);
});

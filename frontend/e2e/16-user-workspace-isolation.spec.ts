/** E2E 16 — authenticated users cannot cross workspace data boundaries. */
import { test, expect } from "./fixtures";

test("16. users are isolated across sessions, files, runs and memory", async ({ api, playwright, workspaceId }) => {
  const otherWorkspace = `${workspaceId}_other`;
  expect((await api.post("/api/workspaces", { data: { workspace_id: otherWorkspace } })).ok()).toBeTruthy();

  const suffix = `${process.pid}`;
  const userA = `e2e_user_a_${suffix}`;
  const userB = `e2e_user_b_${suffix}`;
  const passwordA = `A-${suffix}-safe-password`;
  const passwordB = `B-${suffix}-safe-password`;
  for (const user of [
    { username: userA, password: passwordA, workspace_ids: [workspaceId] },
    { username: userB, password: passwordB, workspace_ids: [otherWorkspace] },
  ]) {
    const created = await api.post("/api/identity/users", {
      data: { ...user, role: "operator", organization_id: "default" },
    });
    expect(created.status()).toBe(201);
  }

  const login = async (username: string, password: string) => {
    const context = await playwright.request.newContext({ baseURL: process.env.E2E_BACKEND_URL });
    const response = await context.post("/api/auth/login", { data: { username, password } });
    expect(response.ok()).toBeTruthy();
    return context;
  };

  const contextA = await login(userA, passwordA);
  const contextB = await login(userB, passwordB);
  try {
    const session = await contextA.post("/api/sessions", {
      data: { workspace_id: workspaceId, title: "user A private session" },
    });
    expect(session.ok()).toBeTruthy();
    const sessionBody = await session.json();
    const sessionId = String(sessionBody.session_id ?? sessionBody.session?.session_id ?? "");
    expect(sessionId).toBeTruthy();
    const artifact = await contextA.post(`/api/workspaces/${workspaceId}/artifacts`, {
      data: { title: "user-a-private", artifact_type: "test_seed", content: "private", sensitivity: "internal" },
    });
    expect(artifact.ok()).toBeTruthy();
    const artifactBody = await artifact.json();
    const artifactId = String(artifactBody.artifact_id ?? artifactBody.artifact?.artifact_id ?? "");
    expect(artifactId).toBeTruthy();

    for (const path of [
      `/api/sessions?workspace_id=${workspaceId}`,
      `/api/sessions/${sessionId}?workspace_id=${workspaceId}`,
      `/api/sessions/${sessionId}/messages?workspace_id=${workspaceId}`,
      `/api/runs/recent?workspace_id=${workspaceId}`,
      `/api/memory/status?workspace_id=${workspaceId}`,
      `/api/memory/list?workspace_id=${workspaceId}`,
      `/api/knowledge/sources?workspace_id=${workspaceId}`,
      `/api/jobs?workspace_id=${workspaceId}`,
      `/api/storage/files?workspace_id=${workspaceId}`,
      `/api/storage/overview?workspace_id=${workspaceId}`,
      `/api/workflows?workspace_id=${workspaceId}`,
      `/api/runtime/tasks?workspace_id=${workspaceId}`,
      `/api/tools/history?workspace_id=${workspaceId}`,
      `/api/agent/sse/stream/${sessionId}?workspace_id=${workspaceId}`,
      `/api/workspaces/${workspaceId}/artifacts`,
      `/api/workspaces/${workspaceId}/artifacts/${artifactId}`,
      `/api/workspaces/${workspaceId}/artifacts/${artifactId}/content`,
      `/api/workspaces/${workspaceId}/runs`,
      `/api/workspaces/${workspaceId}/traces`,
      `/api/workspaces/${workspaceId}/review-items`,
    ]) {
      const denied = await contextB.get(path);
      expect([401, 403]).toContain(denied.status());
    }
    for (const [path, data] of [
      ["/api/agent/message", { workspace_id: workspaceId, session_id: sessionId, message: "denied" }],
      ["/api/jobs", { workspace_id: workspaceId, job_type: "agent_run", title: "denied" }],
      ["/api/memory/search", { workspace_id: workspaceId, query: "private" }],
      ["/api/context/build", { workspace_id: workspaceId, query: "private" }],
      ["/api/tools/dry-run", { workspace_id: workspaceId, tool_id: "workspace.metadata.get", arguments: {} }],
    ] as const) {
      const denied = await contextB.post(path, { data });
      expect([401, 403]).toContain(denied.status());
    }
    expect((await contextB.get(`/api/sessions?workspace_id=${otherWorkspace}`)).ok()).toBeTruthy();
  } finally {
    await contextA.dispose();
    await contextB.dispose();
    await api.delete(`/api/identity/users/${userA}`);
    await api.delete(`/api/identity/users/${userB}`);
  }
});

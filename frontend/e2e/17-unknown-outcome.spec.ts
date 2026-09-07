import { test, expect, selectWorkspace } from "./fixtures";

test("17. unknown write outcome is visible without a runtime freeze claim", async ({ page, api, workspaceId }) => {
  await page.routeWebSocket("**/ws/agent", (ws) => {
    ws.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
        return;
      }
      if (frame.type !== "message") return;
      ws.send(JSON.stringify({
        type: "done",
        session_id: frame.session_id,
        turn_id: "turn-unknown-e2e",
        trace_id: "trace-unknown-e2e",
        final_response: "外部写操作等待受控核对。",
        events: [],
        tool_calls_count: 1,
        tool_calls: [{
          call_id: "call-write-e2e",
          tool_id: "workspace.file",
          ok: false,
          summary: "remote write timed out",
        }],
        metadata: {
          workspace_id: frame.workspace_id,
          execution_outcome: "unknown",
          unknown_outcome: {
            status: "unknown",
            tool_id: "workspace.file",
            call_id: "call-write-e2e",
            error_code: "TOOL_TIMEOUT_UNCERTAIN",
            execution_may_continue: true,
          },
        },
        errors: ["unknown_outcome"],
        warnings: [],
        tool_decision: {},
        no_tool_reason: "",
      }));
    });
  });

  const created = await api.post("/api/sessions", {
    data: { workspace_id: workspaceId, title: "unknown outcome e2e" },
  });
  expect(created.ok()).toBeTruthy();
  const sessionId = String((await created.json()).session.session_id);

  await page.goto("/workbench");
  await selectWorkspace(page, workspaceId);
  await page.getByTestId(`sess-btn-${sessionId}`).click();
  const input = page.getByTestId("chat-input");
  await expect(input).toBeEnabled();
  await input.fill("执行一个可能产生未知结果的写操作");
  await page.getByTestId("btn-send").click();

  const alert = page.getByTestId("unknown-outcome-alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("执行结果尚未确定");
  await expect(alert).toContainText("workspace.file");
  await expect(alert).toContainText("call-write-e2e");
  await expect(alert).toContainText("TOOL_TIMEOUT_UNCERTAIN");
  await expect(page.getByRole("button", { name: "重试原任务" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "换方案继续" })).toHaveCount(0);
  await expect(page.getByTestId("retry-btn")).toHaveCount(0);
});

/**
 * Test 8 — empty state
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewCenter } from "../pages/ReviewCenter/ReviewCenter";
import { enqueue, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore } from "../stores/session";

describe("Empty state", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.setState({ currentWorkspaceId: "ws-1" });
  });

  it("renders empty state when backend returns []", async () => {
    enqueue("/workspaces/ws-1/review-items", { status: 200, data: { items: [] } });
    render(<ReviewCenter />);
    const empty = await screen.findByTestId("review-empty-state");
    expect(empty.textContent).toContain("当前没有待处理复核");
    expect(empty.textContent).toContain("流程运行失败时也会自动进入这里");
  });
});

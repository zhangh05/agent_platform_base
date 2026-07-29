/**
 * Test 5 — review item 状态
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewCenter } from "../pages/ReviewCenter/ReviewCenter";
import { enqueue, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore } from "../stores/session";
import type { ReviewItem } from "../types";

const sampleItem: ReviewItem = {
  item_id: "rev-1",
  workspace_id: "ws-1",
  artifact_id: "art-1",
  severity: "warning",
  category: "trans_residue",
  line_no: 12,
  reason: "sensitive excerpt detected",
  requires_human_review: true,
  status: "pending",
  user_note: "",
  created_at: "2026-06-11T10:00:00Z",
  updated_at: "2026-06-11T10:00:00Z",
};

describe("ReviewCenter — review item status", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.setState({ currentWorkspaceId: "ws-1" });
  });

  it("renders a review item with pending badge", async () => {
    enqueue("/workspaces/ws-1/review-items", { status: 200, data: { items: [sampleItem] } });
    render(<ReviewCenter />);
    const row = await screen.findByTestId("review-rev-1");
    expect(row).toBeInTheDocument();
    // UI 在 v1.0.1 UI 重设计后中文化：pending → 待处理
    expect(row.textContent).toContain("待处理");
    expect(row.textContent).toContain("trans_residue");
  });

  it("switches to accepted when user saves", async () => {
    enqueue("/workspaces/ws-1/review-items", { status: 200, data: { items: [{ ...sampleItem, status: "accepted", user_note: "ok" }] } });
    enqueue("/review-items/rev-1", { status: 200, data: { item: { ...sampleItem, status: "accepted" } } });
    render(<ReviewCenter />);
    const row = await screen.findByTestId("review-rev-1");
    // accepted → 已接受
    expect(row.textContent).toContain("已接受");
  });

  it("explains the review workflow when pending items are empty", async () => {
    enqueue("/workspaces/ws-1/review-items", { status: 200, data: { items: [] } });
    render(<ReviewCenter />);

    expect(await screen.findByTestId("review-empty-state")).toHaveTextContent("当前没有待处理评审");
    expect(screen.getByText("2. 有风险的制品会自动进入这里")).toBeInTheDocument();
  });
});

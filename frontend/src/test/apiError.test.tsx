/**
 * Test 7 — API error 状态
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { KnowledgeLibrary } from "../pages/KnowledgeLibrary/KnowledgeLibrary";
import { enqueue, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore } from "../stores/session";

describe("API Error state", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.setState({ currentWorkspaceId: "ws-1" });
  });

  it("renders the error state with retry", async () => {
    enqueue("/knowledge/sources", {
      status: 503,
      data: { message: "service unavailable" },
    });
    enqueue("/knowledge/sources", {
      status: 200,
      data: { sources: [] },
    });
    render(<KnowledgeLibrary />);
    const err = await screen.findByTestId("error-state");
    expect(err.textContent).toContain("service unavailable");
    expect(err.textContent).toContain("http_5xx");
    // The retry button should re-fire the request.
    const btn = err.querySelector("button");
    expect(btn).toBeInTheDocument();
  });
});

import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KnowledgeLibrary } from "../pages/KnowledgeLibrary/KnowledgeLibrary";
import { enqueue, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore } from "../stores/session";

describe("KnowledgeLibrary upload", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.setState({ currentWorkspaceId: "default" });
    enqueue("/knowledge/sources", {
      status: 200,
      data: { ok: true, sources: [], counts: {} },
    });
    enqueue("/knowledge/search", {
      status: 200,
      data: { ok: true, query: "", results: [], count: 0 },
    });
    enqueue("/workspaces/default/artifacts", {
      status: 200,
      data: { artifacts: [] },
    });
  });

  it("shows local upload controls", async () => {
    render(<KnowledgeLibrary />);

    expect(await screen.findByTestId("knowledge-upload-card")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-upload-file")).toBeInTheDocument();
    expect(screen.getByText("选择本地文档")).toBeInTheDocument();
    expect(screen.getByTestId("btn-knowledge-upload")).toBeDisabled();
  });

  it("uploads a selected file and refreshes sources", async () => {
    enqueue("/knowledge/upload", {
      status: 200,
      data: {
        ok: true,
        source: {
          source_id: "ksrc_1",
          workspace_id: "default",
          title: "OSPF",
          tags: [],
          enabled: true,
          chunk_count: 1,
          created_at: "",
          status: "indexed",
        },
      },
    });
    enqueue("/knowledge/sources", {
      status: 200,
      data: {
        ok: true,
        sources: [{
          source_id: "ksrc_1",
          workspace_id: "default",
          title: "OSPF",
          tags: [],
          enabled: true,
          chunk_count: 1,
          created_at: "",
          status: "indexed",
        }],
        counts: {},
      },
    });

    render(<KnowledgeLibrary />);
    const file = new File(["# OSPF"], "ospf.md", { type: "text/markdown" });

    await userEvent.upload(await screen.findByTestId("knowledge-upload-file"), file);
    await userEvent.click(screen.getByTestId("btn-knowledge-upload"));

    await waitFor(() => expect(screen.getByText("OSPF")).toBeInTheDocument());
  });
});

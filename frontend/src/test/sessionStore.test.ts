import { beforeEach, describe, expect, it } from "vitest";
import { isInternalSessionId, useSessionStore } from "../stores/session";

describe("session store boundaries", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({ currentWorkspaceId: "default", currentSessionId: null });
  });

  it("rejects internal subagent sessions as the active UI session", () => {
    expect(isInternalSessionId("sub-a0206b29")).toBe(true);

    useSessionStore.getState().setCurrentSession("sub-a0206b29");

    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });
});

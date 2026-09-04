import { createRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThinkingBlock } from "../pages/AgentWorkbench/components/ThinkingBlock";
import { WorkbenchComposer } from "../pages/AgentWorkbench/components/WorkbenchComposer";

describe("ThinkingBlock interaction semantics", () => {
  it("keeps disclosure and copy as independent buttons", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<ThinkingBlock content="evidence" />);

    const disclosure = screen.getByRole("button", { name: /思考与推理过程/ });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("evidence"));
    expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
  });

  it("reports a rejected clipboard write instead of claiming success", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });

    render(<ThinkingBlock content="evidence" defaultOpen />);
    fireEvent.click(screen.getByRole("button", { name: "复制" }));

    expect(await screen.findByRole("button", { name: "复制失败" })).toBeInTheDocument();
  });
});

describe("WorkbenchComposer send contract", () => {
  const baseProps = {
    currentSessionId: "session-1",
    turnRunning: false,
    input: "",
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    attachments: [],
    onRemoveAttachment: vi.fn(),
    onPickFile: vi.fn(),
    fileInputRef: createRef<HTMLInputElement>(),
    onFileInputChange: vi.fn(),
    inputRef: createRef<HTMLTextAreaElement>(),
    workbenchSkills: [],
    selectedSkillKey: "",
    onSelectSkillKey: vi.fn(),
    selectedSkill: undefined,
    selectedResourceIds: [],
    onSelectResourceIds: vi.fn(),
    onDragOver: vi.fn(),
    onDrop: vi.fn(),
  };

  it("does not dispatch an empty Enter submission", () => {
    const onSend = vi.fn();
    render(<WorkbenchComposer {...baseProps} onSend={onSend} />);

    fireEvent.keyDown(screen.getByTestId("chat-input"), { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByTestId("btn-send")).toBeDisabled();
  });

  it("dispatches a non-empty Enter submission once", () => {
    const onSend = vi.fn();
    render(<WorkbenchComposer {...baseProps} input="检查设备" onSend={onSend} />);

    fireEvent.keyDown(screen.getByTestId("chat-input"), { key: "Enter" });

    expect(onSend).toHaveBeenCalledTimes(1);
  });
});

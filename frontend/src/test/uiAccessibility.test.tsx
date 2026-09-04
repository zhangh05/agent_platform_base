import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button, DataTable, ModalShell } from "../components/ui";

describe("shared UI accessibility contracts", () => {
  it("lets keyboard users activate an interactive data row", () => {
    type Row = { id: string; name: string };
    const onRowClick = vi.fn();
    render(
      <DataTable<Row>
        columns={[{ key: "name", header: "名称", render: (row) => row.name }]}
        rows={[{ id: "1", name: "设备 A" }]}
        keyExtractor={(row) => row.id}
        onRowClick={onRowClick}
      />,
    );

    const row = screen.getByRole("button", { name: "设备 A" });
    expect(row).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.keyDown(row, { key: " " });
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it("traps focus in a modal, closes with Escape, and restores focus", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <>
        <button type="button">打开设置</button>
        <ModalShell open={false} onClose={onClose} title="模型设置"><Button>保存</Button></ModalShell>
      </>,
    );
    const trigger = screen.getByRole("button", { name: "打开设置" });
    trigger.focus();

    rerender(
      <>
        <button type="button">打开设置</button>
        <ModalShell open onClose={onClose} title="模型设置"><Button>保存</Button></ModalShell>
      </>,
    );
    expect(screen.getByRole("dialog", { name: "模型设置" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();

    rerender(
      <>
        <button type="button">打开设置</button>
        <ModalShell open={false} onClose={onClose} title="模型设置"><Button>保存</Button></ModalShell>
      </>,
    );
    expect(screen.getByRole("button", { name: "打开设置" })).toHaveFocus();
  });
});

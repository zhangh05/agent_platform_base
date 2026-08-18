import { describe, expect, it, vi } from "vitest";
import { createStreamRenderCoordinator } from "../utils/streamCoordinator";

function createFrameHarness() {
  let nextId = 1;
  const queued = new Map<number, FrameRequestCallback>();
  return {
    requestAnimationFrame: vi.fn((callback: FrameRequestCallback) => {
      const id = nextId++;
      queued.set(id, callback);
      return id;
    }),
    cancelAnimationFrame: vi.fn((id: number) => {
      queued.delete(id);
    }),
    runOne: (now = 16) => {
      const first = queued.entries().next().value as [number, FrameRequestCallback] | undefined;
      if (!first) throw new Error("no frame queued");
      const [id, callback] = first;
      queued.delete(id);
      callback(now);
    },
    pending: () => queued.size,
  };
}

describe("stream render coordinator", () => {
  it("coalesces token text and elapsed time into one browser-frame commit", () => {
    const frames = createFrameHarness();
    const commit = vi.fn();
    const coordinator = createStreamRenderCoordinator({ commit, ...frames });

    coordinator.markText("第一段");
    coordinator.setElapsed(1_000, 250);
    coordinator.markText("第一段第二段");
    coordinator.request();

    expect(frames.pending()).toBe(1);
    expect(commit).not.toHaveBeenCalled();
    frames.runOne();
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenLastCalledWith({
      text: "第一段第二段",
      progressElapsedMs: 1_000,
      stageElapsedMs: 250,
    });
  });

  it("owns successive token-reveal frames while preserving one commit per frame", () => {
    const frames = createFrameHarness();
    const commit = vi.fn();
    let part = 0;
    const coordinator = createStreamRenderCoordinator({
      commit,
      ...frames,
      onFrame: () => {
        part += 1;
        coordinator.markText(`分段 ${part}`);
        return part < 2;
      },
    });

    coordinator.request();
    frames.runOne();
    expect(commit).toHaveBeenLastCalledWith({ text: "分段 1" });
    expect(frames.pending()).toBe(1);
    frames.runOne();
    expect(commit).toHaveBeenLastCalledWith({ text: "分段 2" });
    expect(commit).toHaveBeenCalledTimes(2);
  });

  it("still paints elapsed time during a quiet thinking phase", () => {
    const frames = createFrameHarness();
    const commit = vi.fn();
    const coordinator = createStreamRenderCoordinator({ commit, ...frames });

    coordinator.setElapsed(2_000, 500);
    expect(frames.pending()).toBe(1);
    frames.runOne();

    expect(commit).toHaveBeenLastCalledWith({ progressElapsedMs: 2_000, stageElapsedMs: 500 });
  });

  it("force-flushes semantic boundaries without waiting for the next frame", () => {
    const frames = createFrameHarness();
    const commit = vi.fn();
    const coordinator = createStreamRenderCoordinator({ commit, ...frames });

    coordinator.markText("不能丢失的尾部");
    coordinator.setElapsed(3_000, 750);
    coordinator.flush();

    expect(frames.cancelAnimationFrame).toHaveBeenCalledTimes(1);
    expect(frames.pending()).toBe(0);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenLastCalledWith({
      text: "不能丢失的尾部",
      progressElapsedMs: 3_000,
      stageElapsedMs: 750,
    });
  });

  it("drops ordinary queued visual work after cancellation", () => {
    const frames = createFrameHarness();
    const commit = vi.fn();
    const coordinator = createStreamRenderCoordinator({ commit, ...frames });

    coordinator.markText("已取消的显示更新");
    coordinator.request();
    coordinator.cancel();

    expect(frames.pending()).toBe(0);
    expect(commit).not.toHaveBeenCalled();
  });
});

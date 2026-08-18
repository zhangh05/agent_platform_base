export type StreamRenderPatch = {
  text?: string;
  progressElapsedMs?: number;
  stageElapsedMs?: number;
};

type AnimationFrameScheduler = (callback: FrameRequestCallback) => number;
type CancelAnimationFrameScheduler = (frameId: number) => void;

export type StreamRenderCoordinatorOptions = {
  /**
   * The only ordinary-path visible-message write. The caller retains
   * transport and Zustand ownership; this coordinator only coalesces visual
   * state to the browser paint clock.
   */
  commit: (patch: StreamRenderPatch) => void;
  /**
   * Runs inside the coordinator-owned animation frame before one merged
   * visible-state commit. Return true when token reveal needs another frame.
   */
  onFrame?: (frameNow: number) => boolean;
  requestAnimationFrame?: AnimationFrameScheduler;
  cancelAnimationFrame?: CancelAnimationFrameScheduler;
};

export type StreamRenderCoordinator = {
  /** Mark text dirty without opening a competing browser frame. */
  markText: (text: string) => void;
  /** Mark elapsed time dirty and request the shared browser frame. */
  setElapsed: (progressElapsedMs: number, stageElapsedMs: number) => void;
  /** Request the shared frame when token input has arrived. */
  request: () => void;
  /** Immediately commit buffered visual state at a semantic boundary. */
  flush: () => void;
  /** Cancel any ordinary visual work after a terminal frame. */
  cancel: () => void;
};

/**
 * Own the one ordinary visual frame for an active response.
 *
 * WebSocket bursts and elapsed-time ticks mark state dirty. The coordinator
 * invokes token reveal and commits the latest text + clock patch once per
 * browser animation frame. Terminal, tool, error and cancellation boundaries
 * call flush(), so correctness never waits for normal rendering cadence.
 */
export function createStreamRenderCoordinator({
  commit,
  onFrame,
  requestAnimationFrame: browserRequestFrame = window.requestAnimationFrame.bind(window),
  cancelAnimationFrame: browserCancelFrame = window.cancelAnimationFrame.bind(window),
}: StreamRenderCoordinatorOptions): StreamRenderCoordinator {
  let frameId: number | null = null;
  let stopped = false;
  let latestText = "";
  let elapsed: Pick<StreamRenderPatch, "progressElapsedMs" | "stageElapsedMs"> = {};
  let textDirty = false;
  let elapsedDirty = false;

  const takePatch = (): StreamRenderPatch | null => {
    if (!textDirty && !elapsedDirty) return null;
    const next: StreamRenderPatch = {};
    if (textDirty) next.text = latestText;
    if (elapsedDirty) {
      next.progressElapsedMs = elapsed.progressElapsedMs;
      next.stageElapsedMs = elapsed.stageElapsedMs;
    }
    textDirty = false;
    elapsedDirty = false;
    elapsed = {};
    return next;
  };

  const commitPending = () => {
    const next = takePatch();
    if (next) commit(next);
  };

  const schedule = () => {
    if (stopped || frameId !== null) return;
    frameId = browserRequestFrame((frameNow) => {
      frameId = null;
      if (stopped) return;
      const needsAnotherFrame = onFrame?.(frameNow) ?? false;
      commitPending();
      if (needsAnotherFrame || textDirty || elapsedDirty) schedule();
    });
  };

  return {
    markText: (text) => {
      if (stopped) return;
      latestText = text;
      textDirty = true;
    },
    setElapsed: (progressElapsedMs, stageElapsedMs) => {
      if (stopped) return;
      elapsed = {
        progressElapsedMs: Math.max(0, progressElapsedMs),
        stageElapsedMs: Math.max(0, stageElapsedMs),
      };
      elapsedDirty = true;
      schedule();
    },
    request: schedule,
    flush: () => {
      if (stopped) return;
      if (frameId !== null) {
        browserCancelFrame(frameId);
        frameId = null;
      }
      commitPending();
    },
    cancel: () => {
      stopped = true;
      if (frameId !== null) browserCancelFrame(frameId);
      frameId = null;
      textDirty = false;
      elapsedDirty = false;
      elapsed = {};
    },
  };
}

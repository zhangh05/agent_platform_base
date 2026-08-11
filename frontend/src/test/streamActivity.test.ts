import { afterEach, describe, expect, it, vi } from "vitest";
import { createStreamActivityWatchdog } from "../utils/streamActivity";

describe("stream activity watchdog", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reports elapsed time while server work is quiet", () => {
    vi.useFakeTimers();
    const onTick = vi.fn();
    const watchdog = createStreamActivityWatchdog({
      onTick,
      onTimeout: vi.fn(),
      idleTimeoutMs: 30_000,
      tickMs: 1_000,
    });

    vi.advanceTimersByTime(3_000);

    expect(onTick).toHaveBeenLastCalledWith(3_000);
    watchdog.stop();
  });

  it("resets inactivity on every heartbeat and eventually times out", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const watchdog = createStreamActivityWatchdog({
      onTick: vi.fn(),
      onTimeout,
      idleTimeoutMs: 5_000,
      tickMs: 1_000,
    });

    vi.advanceTimersByTime(4_000);
    watchdog.touch();
    vi.advanceTimersByTime(4_000);
    expect(onTimeout).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1_000);
    expect(onTimeout).toHaveBeenCalledOnce();
  });

  it("cancels all callbacks after stop", () => {
    vi.useFakeTimers();
    const onTick = vi.fn();
    const onTimeout = vi.fn();
    const watchdog = createStreamActivityWatchdog({
      onTick,
      onTimeout,
      idleTimeoutMs: 5_000,
      tickMs: 1_000,
    });

    watchdog.stop();
    vi.advanceTimersByTime(10_000);

    expect(onTick).not.toHaveBeenCalled();
    expect(onTimeout).not.toHaveBeenCalled();
  });
});

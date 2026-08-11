export const STREAM_IDLE_TIMEOUT_MS = 30_000;
export const STREAM_ELAPSED_TICK_MS = 1_000;

type StreamActivityWatchdogOptions = {
  onTick: (elapsedMs: number) => void;
  onTimeout: () => void;
  idleTimeoutMs?: number;
  tickMs?: number;
  now?: () => number;
};

export type StreamActivityWatchdog = {
  touch: () => void;
  stop: () => void;
};

/**
 * Track both overall turn duration and transport inactivity. A heartbeat or
 * any normal WebSocket frame resets only the inactivity deadline; elapsed
 * time remains monotonic for honest UI feedback.
 */
export function createStreamActivityWatchdog({
  onTick,
  onTimeout,
  idleTimeoutMs = STREAM_IDLE_TIMEOUT_MS,
  tickMs = STREAM_ELAPSED_TICK_MS,
  now = Date.now,
}: StreamActivityWatchdogOptions): StreamActivityWatchdog {
  const startedAt = now();
  let stopped = false;
  let idleTimer: ReturnType<typeof setTimeout>;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(idleTimer);
    clearInterval(tickTimer);
  };

  const expire = () => {
    if (stopped) return;
    stop();
    onTimeout();
  };

  const scheduleIdleTimeout = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(expire, idleTimeoutMs);
  };

  const tickTimer = setInterval(() => {
    if (!stopped) onTick(Math.max(0, now() - startedAt));
  }, tickMs);

  scheduleIdleTimeout();
  return {
    touch: () => {
      if (!stopped) scheduleIdleTimeout();
    },
    stop,
  };
}

/**
 * Human-facing stream durations are owned by one client monotonic clock.
 * They deliberately do not reuse backend elapsed fields because those fields
 * may originate in a different process and clock domain.
 */
export function formatStreamElapsedSeconds(elapsedMs: number): string {
  const safeElapsed = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0;
  return `${Math.floor(safeElapsed / 1000)}s`;
}

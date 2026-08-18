/**
 * Convert irregular provider chunks into bounded visual work. Transport keeps
 * every token immediately; this function only controls how much pending text a
 * single browser frame commits to React.
 */
export const STREAM_REVEAL_TARGET_MS = 180;

export function nextStreamRevealLength(
  pendingLength: number,
  frameMs: number,
  pendingAgeMs: number,
  { force = false, reducedMotion = false }: { force?: boolean; reducedMotion?: boolean } = {},
): number {
  if (pendingLength <= 0) return 0;
  if (force || reducedMotion) return pendingLength;
  const safeFrameMs = Math.max(1, frameMs);
  const remainingMs = Math.max(0, STREAM_REVEAL_TARGET_MS - pendingAgeMs);
  const remainingFrames = Math.max(1, Math.ceil(remainingMs / safeFrameMs));
  return Math.min(pendingLength, Math.max(1, Math.ceil(pendingLength / remainingFrames)));
}

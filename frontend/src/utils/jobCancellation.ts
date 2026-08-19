/**
 * Correlate one durable cancellation intent with one accepted client turn.
 *
 * The backend persists cancellation before projecting it to the in-process
 * event, so repeated browser frames must not manufacture repeated API calls.
 * A retry is still permitted after an actual request failure by releasing the
 * claim through `releaseJobCancellation`.
 */
export function jobCancellationKey(
  workspaceId: string,
  jobId: string,
  clientRequestId: string,
): string {
  return `${workspaceId}:${jobId}:${clientRequestId}`;
}

export function claimJobCancellation(
  claimed: Set<string>,
  workspaceId: string,
  jobId: string,
  clientRequestId: string,
): string | null {
  if (!workspaceId || !jobId) return null;
  const key = jobCancellationKey(workspaceId, jobId, clientRequestId);
  if (claimed.has(key)) return null;
  claimed.add(key);
  return key;
}

export function releaseJobCancellation(claimed: Set<string>, key: string): void {
  claimed.delete(key);
}

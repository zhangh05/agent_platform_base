export type AgentStreamState = {
  draft: string;
};

/**
 * HTTP fallback is safe only before a turn frame has been submitted to the
 * WebSocket. After submission, transport failure has an ambiguous outcome:
 * the server may already be executing the same client_request_id.
 */
export function canFallbackToHttp(wsTurnSubmitted: boolean): boolean {
  return !wsTurnSubmitted;
}

export function beginModelStep(_previous: string = ""): AgentStreamState {
  return { draft: "" };
}

export function discardToolCallDraft(state: AgentStreamState): void {
  state.draft = "";
}

/** The terminal response is authoritative, including short answers and corrections.
 * Only a missing final response falls back to the uncommitted stream draft. */
export function finalizeStreamText(streamedText: string, finalResponse: string): string {
  return finalResponse.trim() || streamedText.trim();
}

/** A completed terminal frame owns the final response; late socket close/error
 * callbacks may flush only an uncommitted, interrupted stream draft. */
export function shouldFlushUncommittedStreamDraft(
  terminalFrameReceived: boolean,
  pendingText: string,
  draft: string,
  committedText: string,
): boolean {
  return !terminalFrameReceived && (!!pendingText || draft !== committedText);
}

/** Return the durable job id only for a server-declared in-progress duplicate.
 * This is correlation state, not a second execution result. */
export function runningIdempotentRedirectJobId(metadata: unknown): string {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return "";
  const record = metadata as Record<string, unknown>;
  const redirect = record.idempotent_redirect;
  if (record.idempotent !== true || !redirect || typeof redirect !== "object" || Array.isArray(redirect)) return "";
  const details = redirect as Record<string, unknown>;
  return (details.status === "running" || details.status === "conflict") && typeof details.job_id === "string"
    ? details.job_id
    : "";
}

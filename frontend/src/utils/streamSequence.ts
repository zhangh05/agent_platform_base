export type StreamEnvelope = {
  type?: unknown;
  seq?: unknown;
  stream_seq?: unknown;
};

export type StreamSequenceDecision = {
  accept: boolean;
  nextSequence: number;
};

function readSequence(value: unknown): number | undefined {
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isSafeInteger(numeric) && numeric >= 0 ? numeric : undefined;
}

/**
 * Preserve server emission order for live frames without requiring a new
 * transport protocol. Live frames carry `seq`; the final done frame mirrors
 * the last live value as `stream_seq`, so equality is valid only for terminal
 * frames. Unsequenced frames remain backward-compatible.
 */
export function decideStreamFrame(
  frame: StreamEnvelope,
  lastSequence: number,
  terminalReceived: boolean,
): StreamSequenceDecision {
  if (terminalReceived) return { accept: false, nextSequence: lastSequence };

  const sequence = readSequence(frame.seq ?? frame.stream_seq);
  if (sequence === undefined) return { accept: true, nextSequence: lastSequence };

  const terminal = frame.type === "done" || frame.type === "error";
  if (terminal) {
    return {
      accept: sequence >= lastSequence,
      nextSequence: Math.max(lastSequence, sequence),
    };
  }
  return {
    accept: sequence > lastSequence,
    nextSequence: Math.max(lastSequence, sequence),
  };
}

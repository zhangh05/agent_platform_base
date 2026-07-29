import type { ThinkFilterState } from "./displayText";

export type AgentStreamState = {
  draft: string;
};

/** Known placeholder responses that signal the backend fell back
 *  to a stub instead of producing a real answer.  When the final
 *  response matches one of these AND is shorter than the streamed
 *  text, we keep the streamed version so the user never loses
 * content they already saw during real-time streaming. */
const PLACEHOLDER_PATTERNS = [
  "收到",
  "已完成",
  "工具执行成功",
  "No tools were executed",
  "readartifact completed",
  "readartifact succeeded",
];

function _isPlaceholder(text: string): boolean {
  const t = text.trim();
  if (t.length < 20) return true;           // extremely short → placeholder
  return PLACEHOLDER_PATTERNS.some((p) => t.includes(p));
}

export function beginModelStep(_previous: string = ""): AgentStreamState {
  return { draft: "" };
}

export function discardToolCallDraft(state: AgentStreamState): void {
  state.draft = "";
}

/** Choose the best final text between the streaming draft and the
 *  backend's official ``final_response``.
 *
 *  v3.16 fix: the old logic ``finalResponse || streamedText``
 *  silently dropped all streamed tokens whenever the backend returned
 *  a non-empty fallback string such as ``"收到。"``.  The user would
 *  see a full answer stream in and then watch it vanish, replaced by
 *  the two-character stub.
 *
 *  New behaviour:
 *  • If ``finalResponse`` looks like a meaningful answer (longer,
 *    not a known placeholder) → use it – it may be a corrected /
 *    post-validation version.
 *  • If ``finalResponse`` is empty / whitespace → fall back to streamed.
 *  • If ``finalResponse`` is a short placeholder but ``streamedText``
 *    contains substantive content that the user already saw → keep the
 *    streamed version to avoid content loss.                                        */
export function finalizeStreamText(streamedText: string, finalResponse: string): string {
  const s = streamedText.trim();
  const f = finalResponse.trim();

  // No backend response → always trust what was streamed
  if (!f) return s;

  // No streamed content (e.g. pure fast-path or error) → use backend
  if (!s) return f;

  // Backend returned a known placeholder but we already streamed
  // real content → preserve what the user saw.
  if (_isPlaceholder(f) && s.length > f.length * 1.5) {
    return s;
  }

  // Default: backend wins (it may include post-retry corrections)
  return f;
}

/** Create a think filter state ref for use in streaming callbacks */
export function createThinkFilter(): { mode: ThinkFilterState } {
  return { mode: "idle" };
}

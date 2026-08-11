import { scopedLocalStorageKey } from "./userScope";

const STORAGE_KEY = "chat_stream_inflight";

export type InflightChatStream = {
  streamId: string;
  workspaceId: string;
  sessionId: string;
  scratchSessionId: string;
  messageId: string;
  startedAt: string;
  lastSeq: number;
};

function storageKey(): string {
  return scopedLocalStorageKey(STORAGE_KEY);
}

export function newChatStreamId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function readInflightChatStream(): InflightChatStream | null {
  try {
    const raw = localStorage.getItem(storageKey());
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<InflightChatStream>;
    if (
      typeof value.streamId !== "string"
      || typeof value.workspaceId !== "string"
      || typeof value.sessionId !== "string"
      || typeof value.scratchSessionId !== "string"
      || typeof value.messageId !== "string"
      || typeof value.startedAt !== "string"
      || typeof value.lastSeq !== "number"
    ) return null;
    return value as InflightChatStream;
  } catch {
    return null;
  }
}

export function writeInflightChatStream(value: InflightChatStream): void {
  try { localStorage.setItem(storageKey(), JSON.stringify(value)); } catch { /* storage unavailable */ }
}

export function updateInflightChatStream(streamId: string, patch: Partial<InflightChatStream>): void {
  const current = readInflightChatStream();
  if (!current || current.streamId !== streamId) return;
  writeInflightChatStream({ ...current, ...patch });
}

export function clearInflightChatStream(streamId?: string): void {
  try {
    if (streamId) {
      const current = readInflightChatStream();
      if (!current || current.streamId !== streamId) return;
    }
    localStorage.removeItem(storageKey());
  } catch { /* storage unavailable */ }
}

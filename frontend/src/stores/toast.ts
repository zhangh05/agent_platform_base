/**
 * Toast store — for global error/success messages. Pages dispatch
 * `useToastStore.getState().show(...)` and the ToastHost renders them.
 */

import { create } from "zustand";

export interface ToastMessage {
  id: string;
  kind: "info" | "success" | "warning" | "error";
  title: string;
  body?: string;
  request_id?: string;
}

interface ToastState {
  messages: ToastMessage[];
  show: (msg: Omit<ToastMessage, "id">) => void;
  dismiss: (id: string) => void;
}

/** Hard upper bound on concurrent toasts. When a new push would exceed this,
 *  the oldest toast is dropped first (FIFO). Keeps the toast stack from
 *  piling up when a burst of network errors happens. */
export const MAX_TOASTS = 5;

/** Auto-dismiss delay (ms). Old toasts are removed individually by their
 *  own setTimeout, not by a global sweeper, so client-side hibernation
 *  does not break the lifetime invariant. */
const TOAST_TTL_MS = 6000;

let toastSeq = 0;
const _timeouts = new Map<string, ReturnType<typeof setTimeout>>();

function clearTimerFor(id: string) {
  const timer = _timeouts.get(id);
  if (timer) { clearTimeout(timer); _timeouts.delete(id); }
}

export const useToastStore = create<ToastState>((set) => ({
  messages: [],
  show: (msg) => {
    toastSeq += 1;
    const id = `toast-${Date.now()}-${toastSeq}`;
    set((prev) => {
      const next = [...prev.messages, { ...msg, id }];
      // Enforce FIFO cap: drop oldest entries (and their timers) over the cap.
      if (next.length > MAX_TOASTS) {
        const overflow = next.length - MAX_TOASTS;
        const dropped = next.splice(0, overflow);
        for (const t of dropped) clearTimerFor(t.id);
      }
      return { messages: next };
    });
    const timer = setTimeout(() => {
      _timeouts.delete(id);
      set((prev) => ({ messages: prev.messages.filter((m) => m.id !== id) }));
    }, TOAST_TTL_MS);
    _timeouts.set(id, timer);
  },
  dismiss: (id) => {
    clearTimerFor(id);
    set((prev) => ({ messages: prev.messages.filter((m) => m.id !== id) }));
  },
}));

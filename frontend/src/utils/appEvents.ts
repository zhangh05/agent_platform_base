export const APP_EVENTS = {
  RUN_COMPLETED: "agent-platform-base:run-completed",
} as const;

export function notifyRunCompleted(): void {
  window.dispatchEvent(new CustomEvent(APP_EVENTS.RUN_COMPLETED));
}

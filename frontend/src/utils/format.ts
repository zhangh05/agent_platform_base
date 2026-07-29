/**
 * Shared formatting utilities — single source of truth for date / file-size display.
 * Backend timestamps are UTC ISO strings; frontend renders them in the product
 * timezone so every page shows the same wall-clock value.
 */

const DISPLAY_LOCALE = "zh-CN";
const DISPLAY_TIME_ZONE = "Asia/Shanghai";

/** Human-readable file size. */
export function formatFileSize(bytes: number): string {
  if (bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/** Date/time formatting presets. "compact" = full datetime, "short" = date-only, "time" = HH:mm. */
export function formatDate(
  value: string | undefined | null,
  style: "compact" | "short" | "time" = "compact",
): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;

  switch (style) {
    case "short":
      return d.toLocaleDateString(DISPLAY_LOCALE, { timeZone: DISPLAY_TIME_ZONE });
    case "time":
      return d.toLocaleTimeString(DISPLAY_LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: DISPLAY_TIME_ZONE });
    case "compact":
    default:
      return d.toLocaleString(DISPLAY_LOCALE, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: DISPLAY_TIME_ZONE,
      });
  }
}

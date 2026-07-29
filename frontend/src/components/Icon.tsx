import type { SVGProps } from "react";

/**
 * Inline SVG icon set — zero deps, themeable via currentColor.
 *
 * The icons are deliberately drawn at 16×16 with a 1.5–1.75 stroke for
 * a refined feel that matches the editorial design system. Each
 * component spreads the rest of the SVG props so callers can override
 * size, color, aria-label, etc.
 */

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base(size = 16, rest: SVGProps<SVGSVGElement>) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...rest,
  };
}

export function IconChat(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M2.5 4.5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H6.5l-3 2.5V11.5H4.5a2 2 0 0 1-2-2v-5Z" />
      <path d="M5.5 6.5h5M5.5 8.5h3.5" opacity="0.5" />
    </svg>
  );
}

export function IconBook(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M2.5 3.5a1 1 0 0 1 1-1h9v11h-9a1 1 0 0 1-1-1V3.5Z" />
      <path d="M12.5 2.5h-9a1 1 0 0 0-1 1V13" />
      <path d="M5 5.5h4.5M5 7.5h3" opacity="0.5" />
    </svg>
  );
}

export function IconBrain(props: IconProps) {
  // Brain-style icon for memory / knowledge retention.
  return (
    <svg {...base(props.size, props)}>
      <path d="M8 2c-3.5 0-5.5 2.5-5.5 6 0 3 2 5.5 5.5 5.5s5.5-2.5 5.5-5.5C13.5 4.5 11.5 2 8 2Z" />
      <path d="M8 2v11.5" />
      <path d="M4 6c1-.5 3-.5 4 0" />
      <path d="M4 10c1 .5 3 .5 4 0" />
      <path d="M10 6c1-.5 2.5 0 3 1" />
      <path d="M10 10c1 .5 2.5 0 3-1" />
    </svg>
  );
}

export function IconServer(props: IconProps) {
  // Server rack icon for infrastructure/system pages.
  return (
    <svg {...base(props.size, props)}>
      <rect x="3" y="2.5" width="10" height="11" rx="1.5" />
      <path d="M3 6h10" />
      <path d="M3 10h10" />
      <circle cx="5" cy="4" r="0.6" fill="currentColor" />
      <circle cx="7.5" cy="4" r="0.6" fill="currentColor" opacity="0.5" />
      <circle cx="10" cy="4" r="0.6" fill="currentColor" opacity="0.3" />
    </svg>
  );
}

export function IconBox(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M8 1.5 2 4.5v7l6 3 6-3v-7L8 1.5Z" />
      <path d="M2 4.5 8 7.5l6-3" />
      <path d="M8 7.5v7" />
    </svg>
  );
}

export function IconCheck(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="m5 8 2 2 4-4" />
    </svg>
  );
}

export function IconBolt(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M8.5 1.5 3 9h3.5L7 14.5l5.5-7.5H9L8.5 1.5Z" />
    </svg>
  );
}

export function IconShield(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M8 1.5 2.5 3.5v4c0 3 2 5.5 5.5 7 3.5-1.5 5.5-4 5.5-7v-4L8 1.5Z" />
    </svg>
  );
}

export function IconProbe(props: IconProps) {
  // Stethoscope-style icon — represents health diagnostics.
  return (
    <svg {...base(props.size, props)}>
      <path d="M3 2v5a3.5 3.5 0 0 0 7 0V2" />
      <path d="M3 2h7" opacity="0.4" />
      <circle cx="12.5" cy="11" r="1.5" />
      <path d="M6.5 10.5a3.5 3.5 0 0 1 3.5 3.5c0 1.5 1 2.5 2.5 2.5" />
    </svg>
  );
}

export function IconClock(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M8 4.5V8l2.5 1.5" />
    </svg>
  );
}

export function IconSettings(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1.5v2M8 12.5v2M14.5 8h-2M3.5 8h-2M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4M12.6 12.6l-1.4-1.4M4.8 4.8 3.4 3.4" />
    </svg>
  );
}

export function IconLayers(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="m2 5.5 6 3 6-3-6-3-6 3Z" />
      <path d="m2 8.5 6 3 6-3" opacity="0.6" />
      <path d="m2 11.5 6 3 6-3" opacity="0.3" />
    </svg>
  );
}

export function IconSend(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M14 2 1 8l5 2 2 5 6-13Z" />
      <path d="m6 10 4-4" />
    </svg>
  );
}

export function IconPlus(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

export function IconClose(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M3.5 3.5 12.5 12.5M12.5 3.5 3.5 12.5" />
    </svg>
  );
}

export function IconChevronLeft(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="m10 3-5 5 5 5" />
    </svg>
  );
}

export function IconChevronRight(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="m6 3 5 5-5 5" />
    </svg>
  );
}

export function IconChevronDown(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="m3 6 5 5 5-5" />
    </svg>
  );
}

export function IconSearch(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3 3" />
    </svg>
  );
}

export function IconSun(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v2M8 12.5v2M14.5 8h-2M3.5 8h-2M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4M12.6 12.6l-1.4-1.4M4.8 4.8 3.4 3.4" />
    </svg>
  );
}

export function IconMenu(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />
    </svg>
  );
}

export function IconMoon(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M13 9.5A5.5 5.5 0 1 1 6.5 3a4.5 4.5 0 0 0 6.5 6.5Z" />
    </svg>
  );
}

export function IconWorkspace(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="M2 6.5h12" />
      <path d="M5 9.5h3" opacity="0.5" />
    </svg>
  );
}

export function IconHistory(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9" />
      <path d="M2 2.5v3.5h3.5" />
      <path d="M8 5.5V8l1.5 1.5" />
    </svg>
  );
}

export function IconAlert(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M8 1.5 1 13.5h14L8 1.5Z" />
      <path d="M8 6v3.5" />
      <circle cx="8" cy="11.5" r="0.4" fill="currentColor" />
    </svg>
  );
}

export function IconSparkle(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M8 1.5 9 6l4.5 1-4.5 1L8 14.5 7 8 2.5 7l4.5-1L8 1.5Z" />
    </svg>
  );
}

export function IconRefresh(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M2.5 8a5.5 5.5 0 0 1 9.4-3.9M13.5 8a5.5 5.5 0 0 1-9.4 3.9" />
      <path d="M12 2v3.5h-3.5M4 14v-3.5h3.5" />
    </svg>
  );
}

export function IconExternal(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M9 2.5h4.5V7" />
      <path d="m13 3-7 7" />
      <path d="M12.5 9.5v3.5a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1H7" />
    </svg>
  );
}

export function IconLink(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M6 9.5 9.5 6a2.5 2.5 0 0 1 3.5 3.5L11 11.5" />
      <path d="m10 6.5-3.5 3.5a2.5 2.5 0 0 1-3.5-3.5L5 4.5" />
    </svg>
  );
}

export function IconDocument(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M3.5 1.5h6L13 5v9a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V2.5a1 1 0 0 1 1-1Z" />
      <path d="M9 1.5V5h4" />
      <path d="M5 8h6M5 10.5h4" opacity="0.5" />
    </svg>
  );
}

export function IconKey(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <circle cx="5" cy="11" r="2.5" />
      <path d="m7 9 6-6" />
      <path d="m11 5 2 2M10 6l2 2" />
    </svg>
  );
}

export function IconArchive(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <rect x="2" y="3" width="12" height="3" rx="0.5" />
      <path d="M3 6v6.5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V6" />
      <path d="M6.5 9.5h3" />
    </svg>
  );
}

export function IconEdit(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M11.5 2.5a1.414 1.414 0 0 1 2 2L5 13l-3 1 1-3 8.5-8.5z" />
    </svg>
  );
}

export function IconTrash(props: IconProps) {
  return (
    <svg {...base(props.size, props)}>
      <path d="M2.5 4.5h11" />
      <path d="M5.5 4.5V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5" />
      <path d="M3.5 4.5l.8 9.2a1 1 0 0 0 1 .8h5.4a1 1 0 0 0 1-.8l.8-9.2" />
    </svg>
  );
}

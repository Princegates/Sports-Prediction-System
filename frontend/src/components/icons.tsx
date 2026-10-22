/**
 * Inline SVG icon set, replacing @carbon/icons-react now that nothing in
 * the app depends on Carbon. One consistent stroke style (round caps/joins,
 * 1.75px stroke, 24x24 viewBox) instead of pulling in a second icon
 * library -- this is the whole icon vocabulary the app actually uses, not
 * a general-purpose set.
 */
import type { SVGProps } from "react";

interface IconProps extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  size?: number;
}

function base({ size = 20, ...props }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };
}

export function Search(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}

export function UserAvatar(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7" />
    </svg>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 13.5a7.8 7.8 0 000-3l1.9-1.3-2-3.4-2.2.7a7.6 7.6 0 00-2.6-1.5L14 2.5h-4l-.5 2.5a7.6 7.6 0 00-2.6 1.5l-2.2-.7-2 3.4 1.9 1.3a7.8 7.8 0 000 3l-1.9 1.3 2 3.4 2.2-.7c.76.66 1.64 1.17 2.6 1.5l.5 2.5h4l.5-2.5a7.6 7.6 0 002.6-1.5l2.2.7 2-3.4-1.9-1.3z" />
    </svg>
  );
}

export function Locked(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </svg>
  );
}

export function UserFollow(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="9" cy="8" r="4" />
      <path d="M2 20c0-4.4 3.1-7 7-7s7 2.6 7 7" />
      <path d="M18 8h4M20 6v4" />
    </svg>
  );
}

export function Pin(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 2l1.8 5.6L19 9l-4.6 3.2L15 18l-3-3.6L9 18l.6-5.8L5 9l5.2-1.4z" />
      <path d="M12 14v8" />
    </svg>
  );
}

export function PinFilled(props: IconProps) {
  return (
    <svg {...base(props)} fill="currentColor">
      <path d="M12 2l1.8 5.6L19 9l-4.6 3.2L15 18l-3-3.6L9 18l.6-5.8L5 9l5.2-1.4z" />
      <path d="M12 14v8" stroke="currentColor" />
    </svg>
  );
}

export function ChevronRight(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

export function Login(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M11 4h7a2 2 0 012 2v12a2 2 0 01-2 2h-7" />
      <path d="M3 12h13M12 7l5 5-5 5" />
    </svg>
  );
}

export function Menu(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}

export function Close(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function Home(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 11.5L12 4l8 7.5" />
      <path d="M6 10v9a1 1 0 001 1h3v-6h4v6h3a1 1 0 001-1v-9" />
    </svg>
  );
}

export function ChartLine(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 19h16" />
      <path d="M4 15l4.5-5 3.5 3 5.5-7" />
    </svg>
  );
}

export function ShoppingCatalog(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="4" y="7" width="16" height="13" rx="2" />
      <path d="M8 7V5a4 4 0 018 0v2" />
    </svg>
  );
}

export function AiGenerate(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3l1.6 4.2L18 9l-4.4 1.8L12 15l-1.6-4.2L6 9l4.4-1.8z" />
      <path d="M19 15l.8 2.1L22 18l-2.2.9L19 21l-.8-2.1L16 18l2.2-.9z" />
    </svg>
  );
}

export function Live(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="2.5" />
      <path d="M8.5 8.5a5 5 0 000 7M15.5 8.5a5 5 0 010 7" />
      <path d="M5.3 5.3a9.5 9.5 0 000 13.4M18.7 5.3a9.5 9.5 0 010 13.4" />
    </svg>
  );
}

export function Trophy(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M8 4h8v5a4 4 0 01-8 0V4z" />
      <path d="M16 6h3v1a3 3 0 01-3 3M8 6H5v1a3 3 0 003 3" />
      <path d="M12 13v4M8.5 20h7M10 17h4" />
    </svg>
  );
}

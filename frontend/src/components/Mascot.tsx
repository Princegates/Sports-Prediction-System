type Pose = "idle" | "thinking" | "celebrating" | "sad";
type Variant = "compact" | "hero";

interface Props {
  pose?: Pose;
  size?: number;
  className?: string;
  /** "hero" adds a continuous 3D turn (perspective + rotateY) and an
   * animated juggling ball, for a large standalone placement (the dashboard
   * introduction). "compact" (default) is the original flat, still figure
   * used in loading/empty states, where constant motion would be noise. */
  variant?: Variant;
}

/**
 * A small line-art analyst mascot used in empty/loading states and the goal
 * celebration overlay. Deliberately not a casino/betting mascot -- a friendly
 * "AI analyst" reading data, never holding a trophy, dice, or cash.
 */
export function Mascot({ pose = "idle", size = 96, className, variant = "compact" }: Props) {
  const svg = (
    <svg
      className={`mascot mascot-${pose}${variant === "hero" ? " mascot-hero" : ""}${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      viewBox="0 0 96 96"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* body */}
      <rect x="28" y="38" width="40" height="34" rx="14" fill="var(--bg-surface-3)" stroke="var(--accent)" strokeWidth="2.5" />
      {/* head */}
      <g className="mascot-head">
        <rect x="30" y="14" width="36" height="28" rx="12" fill="var(--bg-surface-2)" stroke="var(--accent)" strokeWidth="2.5" />
        <circle className="mascot-eye left" cx="41" cy="28" r="3.4" fill="var(--accent)" />
        <circle className="mascot-eye right" cx="55" cy="28" r="3.4" fill="var(--accent)" />
        {pose === "celebrating" ? (
          <path d="M40 34 Q48 40 56 34" stroke="var(--good)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        ) : pose === "sad" ? (
          <path d="M40 35 Q48 30 56 35" stroke="var(--text-muted)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        ) : (
          <path d="M41 34 Q48 37 55 34" stroke="var(--text-secondary)" strokeWidth="2.2" strokeLinecap="round" fill="none" />
        )}
        {/* antenna */}
        <line x1="48" y1="14" x2="48" y2="6" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="48" cy="5" r="3" fill="var(--ai-violet)" className="mascot-antenna-dot" />
      </g>

      {/* arms */}
      {pose === "celebrating" ? (
        <>
          <path d="M30 46 L14 28" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" className="mascot-arm left" />
          <path d="M66 46 L82 28" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" className="mascot-arm right" />
        </>
      ) : pose === "thinking" ? (
        <>
          <path d="M30 50 L20 62" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" />
          <path d="M66 48 Q76 40 74 30" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" fill="none" className="mascot-arm right" />
        </>
      ) : (
        <>
          <path d="M28 50 L16 62" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" />
          <path d="M68 50 L80 62" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" />
        </>
      )}

      {/* football, cradled -- idle/sad only, and only when not juggling below */}
      {(pose === "idle" || pose === "sad") && variant !== "hero" && (
        <g transform="translate(38, 58)">
          <circle r="9" fill="var(--bg-surface)" stroke="var(--text-muted)" strokeWidth="2" />
          <path d="M0 -9 L0 9 M-9 0 L9 0 M-6.5 -6.5 L6.5 6.5 M-6.5 6.5 L6.5 -6.5" stroke="var(--text-muted)" strokeWidth="1" opacity="0.5" />
        </g>
      )}

      {/* football, juggling near the feet -- the hero variant's signature motion */}
      {variant === "hero" && (
        <g className="mascot-hero-ball" transform="translate(48, 82)">
          <circle r="10" fill="var(--bg-surface)" stroke="var(--accent)" strokeWidth="2" />
          <path d="M0 -10 L0 10 M-10 0 L10 0 M-7 -7 L7 7 M-7 7 L7 -7" stroke="var(--accent)" strokeWidth="1" opacity="0.6" />
        </g>
      )}

      {pose === "thinking" && (
        <g className="mascot-sparks">
          <circle cx="78" cy="18" r="2" fill="var(--warning)" />
          <circle cx="84" cy="26" r="1.4" fill="var(--warning)" />
        </g>
      )}

      {pose === "celebrating" && (
        <g className="mascot-sparks">
          <circle cx="16" cy="20" r="2" fill="var(--good)" />
          <circle cx="80" cy="18" r="2.2" fill="var(--accent)" />
          <circle cx="10" cy="34" r="1.6" fill="var(--ai-violet)" />
          <circle cx="86" cy="34" r="1.6" fill="var(--warning)" />
        </g>
      )}
    </svg>
  );

  if (variant !== "hero") return svg;

  // The stage carries the perspective; the SVG inside it does the turning.
  // Splitting them is what makes the rotation read as depth instead of a
  // flat shape stretching -- the same pairing tilt-card uses for its hover
  // tilt, just animated continuously instead of pointer-driven.
  return <div className="mascot-hero-stage">{svg}</div>;
}

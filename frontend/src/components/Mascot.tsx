import { useId } from "react";

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
 * A friendly little AI-robot mascot used in empty/loading states, the goal
 * celebration overlay, and the dashboard hero. Rounded and glossy rather
 * than flat line art -- gradients and a specular highlight on the visor do
 * the "3D" work, not an actual 3D render or a static image, so it stays a
 * few KB of inline SVG and keeps recoloring correctly across every accent
 * theme and both light/dark modes, the way a baked PNG never could.
 * Deliberately not a casino/betting mascot -- a friendly "AI analyst"
 * reading data, never holding a trophy, dice, or cash.
 */
export function Mascot({ pose = "idle", size = 96, className, variant = "compact" }: Props) {
  // Unique per instance so two Mascots on the same page (e.g. a hero plus a
  // loading skeleton) never collide on the same gradient id.
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const headGradId = `mascotHead-${uid}`;
  const bodyGradId = `mascotBody-${uid}`;
  const eyeGradId = `mascotEye-${uid}`;

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
      <defs>
        <linearGradient id={headGradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--bg-surface-2)" />
          <stop offset="100%" stopColor="var(--bg-surface-3)" />
        </linearGradient>
        <linearGradient id={bodyGradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--bg-surface-3)" />
          <stop offset="100%" stopColor="var(--bg-surface-2)" />
        </linearGradient>
        <radialGradient id={eyeGradId} cx="38%" cy="32%" r="70%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="45%" stopColor="var(--accent-strong)" />
          <stop offset="100%" stopColor="var(--accent)" />
        </radialGradient>
      </defs>

      {/* grounding shadow -- reads as depth without an actual 3D render */}
      <ellipse cx="48" cy="90" rx="21" ry="3.5" fill="var(--text-muted)" opacity="0.22" />

      {/* feet */}
      <g className="mascot-feet">
        <rect x="33" y="74" width="9" height="13" rx="4.5" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
        <rect x="54" y="74" width="9" height="13" rx="4.5" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
        <ellipse cx="37.5" cy="88" rx="7.5" ry="3.6" fill="var(--bg-surface-3)" stroke="var(--accent)" strokeWidth="2" />
        <ellipse cx="58.5" cy="88" rx="7.5" ry="3.6" fill="var(--bg-surface-3)" stroke="var(--accent)" strokeWidth="2" />
      </g>

      {/* body: a chunky rounded capsule, not a boxy rectangle */}
      <rect x="23" y="40" width="50" height="38" rx="19" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2.5" />
      {/* chest status light */}
      <circle cx="48" cy="58" r="5.5" fill="none" stroke="var(--ai-violet)" strokeWidth="2" opacity="0.8" />
      <circle cx="48" cy="58" r="2" fill="var(--ai-violet)" className="mascot-antenna-dot" />

      {/* arms -- thicker, with a rounded "mitten" hand at the tip so they
          read as chunky robot limbs rather than thin stick-figure lines */}
      {pose === "celebrating" ? (
        <>
          <g className="mascot-arm left">
            <path d="M28 48 L12 30" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round" fill="none" />
            <circle cx="11" cy="27" r="6.5" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
            <rect x="7" y="16" width="4.5" height="8" rx="2.2" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="1.8" />
          </g>
          <g className="mascot-arm right">
            <path d="M68 48 L84 30" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round" fill="none" />
            <circle cx="85" cy="27" r="6.5" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
            <rect x="83.5" y="16" width="4.5" height="8" rx="2.2" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="1.8" />
          </g>
        </>
      ) : pose === "thinking" ? (
        <>
          <path d="M28 52 L18 64" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round" />
          <circle cx="17" cy="66" r="6" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
          <g className="mascot-arm right">
            <path d="M68 50 Q78 42 75 30" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round" fill="none" />
            <circle cx="75" cy="27" r="6" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
          </g>
        </>
      ) : (
        <>
          <path d="M26 52 L14 64" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round" />
          <circle cx="13" cy="66" r="6" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
          <path d="M70 52 L82 64" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round" />
          <circle cx="83" cy="66" r="6" fill={`url(#${bodyGradId})`} stroke="var(--accent)" strokeWidth="2" />
        </>
      )}

      {/* head: rounded, glossy, with ear pods, a visor and glowing eyes */}
      <g className="mascot-head">
        {/* ear pods */}
        <circle cx="22" cy="30" r="5.5" fill={`url(#${headGradId})`} stroke="var(--accent)" strokeWidth="2" />
        <circle cx="74" cy="30" r="5.5" fill={`url(#${headGradId})`} stroke="var(--accent)" strokeWidth="2" />
        <circle cx="22" cy="30" r="1.8" fill="var(--accent-soft)" />
        <circle cx="74" cy="30" r="1.8" fill="var(--accent-soft)" />

        {/* head shell */}
        <rect x="25" y="9" width="46" height="35" rx="17.5" fill={`url(#${headGradId})`} stroke="var(--accent)" strokeWidth="2.5" />
        {/* top gloss highlight */}
        <path d="M31 16 Q48 8 65 16" stroke="#ffffff" strokeOpacity="0.25" strokeWidth="3" strokeLinecap="round" fill="none" />

        {/* visor */}
        <rect x="32" y="19" width="32" height="17" rx="8.5" fill="var(--bg-surface)" />

        {/* eyes */}
        <circle className="mascot-eye left" cx="41" cy="27.5" r="5.4" fill={`url(#${eyeGradId})`} />
        <circle className="mascot-eye right" cx="55" cy="27.5" r="5.4" fill={`url(#${eyeGradId})`} />
        <circle cx="39" cy="25.5" r="1.5" fill="#ffffff" />
        <circle cx="53" cy="25.5" r="1.5" fill="#ffffff" />

        {pose === "celebrating" ? (
          <path d="M40 40 Q48 45 56 40" stroke="var(--good)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        ) : pose === "sad" ? (
          <path d="M40 41 Q48 36 56 41" stroke="var(--text-muted)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        ) : (
          <path d="M41 40 Q48 43 55 40" stroke="var(--text-secondary)" strokeWidth="2.2" strokeLinecap="round" fill="none" />
        )}

        {/* antenna */}
        <line x1="48" y1="9" x2="48" y2="2" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="48" cy="1.6" r="3.2" fill="var(--ai-violet)" className="mascot-antenna-dot" />
      </g>

      {/* football, cradled -- idle/sad only, and only when not juggling below */}
      {(pose === "idle" || pose === "sad") && variant !== "hero" && (
        <g transform="translate(48, 68)">
          <circle r="8" fill="var(--bg-surface)" stroke="var(--text-muted)" strokeWidth="2" />
          <path d="M0 -8 L0 8 M-8 0 L8 0 M-5.8 -5.8 L5.8 5.8 M-5.8 5.8 L5.8 -5.8" stroke="var(--text-muted)" strokeWidth="1" opacity="0.5" />
        </g>
      )}

      {/* football, juggling between the feet -- the hero variant's signature motion */}
      {variant === "hero" && (
        <g className="mascot-hero-ball" transform="translate(48, 84)">
          <circle r="9" fill="var(--bg-surface)" stroke="var(--accent)" strokeWidth="2" />
          <path d="M0 -9 L0 9 M-9 0 L9 0 M-6.4 -6.4 L6.4 6.4 M-6.4 6.4 L6.4 -6.4" stroke="var(--accent)" strokeWidth="1" opacity="0.6" />
        </g>
      )}

      {pose === "thinking" && (
        <g className="mascot-sparks">
          <circle cx="80" cy="16" r="2" fill="var(--warning)" />
          <circle cx="86" cy="24" r="1.4" fill="var(--warning)" />
        </g>
      )}

      {pose === "celebrating" && (
        <g className="mascot-sparks">
          <circle cx="14" cy="16" r="2" fill="var(--good)" />
          <circle cx="82" cy="14" r="2.2" fill="var(--accent)" />
          <circle cx="8" cy="32" r="1.6" fill="var(--ai-violet)" />
          <circle cx="88" cy="32" r="1.6" fill="var(--warning)" />
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

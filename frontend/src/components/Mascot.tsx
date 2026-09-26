import type { CSSProperties } from "react";

type Pose = "idle" | "thinking" | "celebrating" | "sad";
type Variant = "compact" | "hero";

interface Props {
  pose?: Pose;
  size?: number;
  className?: string;
  /** "hero" shows the full-body render in a card with a continuous 3D turn,
   * for a large standalone placement (the dashboard introduction). "compact"
   * (default) frames a tight head-and-shoulders crop in a circular badge,
   * for icon-sized placements everywhere else. */
  variant?: Variant;
}

const HERO_ASPECT = "550 / 619";

/**
 * Guda: an animated 3D-rendered AI-robot photo used as the site's mascot --
 * the chat launcher, empty/loading states, the goal celebration overlay, the
 * dashboard hero, and the auth pages. Pose is expressed as CSS motion
 * (float / tilt / bounce / droop) plus a couple of decorative sparkle
 * overlays, since a single photographic render can't be re-posed frame by
 * frame the way the old inline-SVG mascot could.
 */
export function Mascot({ pose = "idle", size = 96, className, variant = "compact" }: Props) {
  const src = variant === "hero" ? "/mascot/guda-full.webp" : "/mascot/guda-face.webp";
  const style: CSSProperties =
    variant === "hero" ? { width: size, aspectRatio: HERO_ASPECT } : { width: size, height: size };

  const mascot = (
    <div
      className={`mascot mascot-${pose} mascot-${variant}${className ? ` ${className}` : ""}`}
      style={style}
      aria-hidden="true"
    >
      <div className="mascot-frame">
        <img src={src} alt="" draggable={false} />
      </div>

      {pose === "thinking" && (
        <span className="mascot-sparks">
          <i />
          <i />
        </span>
      )}

      {pose === "celebrating" && (
        <span className="mascot-sparks mascot-sparks-burst">
          <i />
          <i />
          <i />
          <i />
        </span>
      )}
    </div>
  );

  if (variant !== "hero") return mascot;

  // The stage carries the perspective; the mascot inside it does the
  // turning -- the same pairing tilt-card uses for its hover tilt, just
  // animated continuously instead of pointer-driven.
  return <div className="mascot-hero-stage">{mascot}</div>;
}

import { useEffect, useState, type CSSProperties } from "react";
import { Mascot } from "./Mascot";

const PARTICLE_COLORS = ["var(--accent)", "var(--ai-violet)", "var(--good)", "var(--warning)"];
const PARTICLES = Array.from({ length: 14 }, (_, i) => i);

interface Props {
  /** Bump this (e.g. with a counter or timestamp) each time a goal should celebrate. */
  triggerKey: number;
  team: string;
}

/** A brief, tasteful celebration for a goal event -- confetti + mascot, not a
 * casino win animation. Auto-dismisses; respects prefers-reduced-motion by
 * skipping straight to a quiet toast instead of the particle burst. */
export function GoalCelebration({ triggerKey, team }: Props) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (triggerKey === 0) return;
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), 2200);
    return () => clearTimeout(timer);
  }, [triggerKey]);

  if (!visible) return null;

  return (
    <div className="goal-celebration" role="status" aria-live="polite">
      <div className="confetti-burst">
        {PARTICLES.map((i) => {
          const style = {
            "--angle": `${(i / PARTICLES.length) * 360}deg`,
            "--delay": `${(i % 5) * 0.04}s`,
            background: PARTICLE_COLORS[i % PARTICLE_COLORS.length],
          } as CSSProperties;
          return <span key={i} className="confetti-piece" style={style} />;
        })}
      </div>
      <div className="goal-celebration-card">
        <Mascot pose="celebrating" size={56} />
        <div>
          <strong>Goal!</strong>
          <div style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>{team} -- recalculating the outcome...</div>
        </div>
      </div>
    </div>
  );
}

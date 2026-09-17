type Pose = "idle" | "thinking" | "celebrating" | "sad";

interface Props {
  pose?: Pose;
  size?: number;
  className?: string;
}

/**
 * A small line-art analyst mascot used in empty/loading states and the goal
 * celebration overlay. Deliberately not a casino/betting mascot -- a friendly
 * "AI analyst" reading data, never holding a trophy, dice, or cash.
 */
export function Mascot({ pose = "idle", size = 96, className }: Props) {
  return (
    <svg
      className={`mascot mascot-${pose}${className ? ` ${className}` : ""}`}
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

      {/* football, cradled -- idle/sad only */}
      {(pose === "idle" || pose === "sad") && (
        <g transform="translate(38, 58)">
          <circle r="9" fill="var(--bg-surface)" stroke="var(--text-muted)" strokeWidth="2" />
          <path d="M0 -9 L0 9 M-9 0 L9 0 M-6.5 -6.5 L6.5 6.5 M-6.5 6.5 L6.5 -6.5" stroke="var(--text-muted)" strokeWidth="1" opacity="0.5" />
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
}

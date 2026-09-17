interface Props {
  label: string;
  probability: number;
  variant?: "home" | "draw" | "away";
}

export function ProbabilityBar({ label, probability, variant }: Props) {
  const pct = Math.round(probability * 100);
  const variantClass = variant && variant !== "home" ? ` ${variant}` : "";
  return (
    <div className="prob-row">
      <span className="label">{label}</span>
      <div className="prob-bar-track">
        <div className={`prob-bar-fill${variantClass}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="value tabular-nums">{pct}%</span>
    </div>
  );
}

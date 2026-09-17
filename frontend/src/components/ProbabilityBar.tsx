interface Props {
  label: string;
  probability: number;
}

export function ProbabilityBar({ label, probability }: Props) {
  const pct = Math.round(probability * 100);
  return (
    <div className="prob-row">
      <span className="label">{label}</span>
      <div className="prob-bar-track">
        <div className="prob-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="value">{pct}%</span>
    </div>
  );
}

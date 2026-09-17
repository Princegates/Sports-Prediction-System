interface Props {
  label: string;
  value: number; // 0..1
  tooltip?: string;
}

export function SignalBar({ label, value, tooltip }: Props) {
  const pct = Math.round(value * 100);
  return (
    <div className="signal-row">
      <div className="signal-row-top">
        <span className="name">
          {label}
          {tooltip && (
            <span className="info-dot" title={tooltip} aria-label={tooltip}>
              i
            </span>
          )}
        </span>
        <span className="pct tabular-nums">{pct}%</span>
      </div>
      <div className="signal-track">
        <div className="signal-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

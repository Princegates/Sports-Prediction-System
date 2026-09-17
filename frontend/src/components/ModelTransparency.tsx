interface Props {
  hasMlModel: boolean;
}

// These match the actual configured ensemble weights (backend/.env.example
// ENSEMBLE_WEIGHT_*). Shown as static values rather than fetched live --
// they're real defaults this deployment ships with, not fabricated numbers.
const WEIGHTS_WITH_ML = [
  { name: "Elo / Team Strength", pct: 30 },
  { name: "Poisson Goal Model", pct: 35 },
  { name: "Gradient Boosting (form, rest, Elo gap)", pct: 35 },
];

const WEIGHTS_WITHOUT_ML = [
  { name: "Elo / Team Strength", pct: 46 },
  { name: "Poisson Goal Model", pct: 54 },
];

export function ModelTransparency({ hasMlModel }: Props) {
  const rows = hasMlModel ? WEIGHTS_WITH_ML : WEIGHTS_WITHOUT_ML;
  return (
    <div>
      <div>
        {rows.map((r) => (
          <div className="transparency-row" key={r.name}>
            <span className="name">{r.name}</span>
            <div className="track">
              <div className="fill" style={{ width: `${r.pct}%` }} />
            </div>
            <span className="pct tabular-nums">{r.pct}%</span>
          </div>
        ))}
      </div>
      {!hasMlModel && (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 10 }}>
          No trained Gradient Boosting model found for this league yet (run <code>scripts/backtest.py</code>) --
          falling back to Elo + Poisson only.
        </p>
      )}
      <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 10 }}>
        These are the ensemble's blend weights, not proprietary internals. They're configured defaults, not learned
        per-match -- see the project README for how calibration is fit on held-out data.
      </p>
    </div>
  );
}

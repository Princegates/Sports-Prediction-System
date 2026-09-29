interface Props {
  hasMlModel: boolean;
}

// These match the ensemble weights the backend ships with (backend/.env.example
// ENSEMBLE_WEIGHT_*), which were fitted on every league's validation matches
// pooled. Shown as static values rather than fetched live -- they're the real
// defaults, not fabricated numbers; a retrain refits them and lands close by.
const WEIGHTS_WITH_ML = [
  { name: "Elo / Team Strength", pct: 40 },
  { name: "Poisson Goal Model", pct: 40 },
  { name: "Gradient Boosting (form, rest, Elo gap)", pct: 20 },
];

const WEIGHTS_WITHOUT_ML = [
  { name: "Elo / Team Strength", pct: 50 },
  { name: "Poisson Goal Model", pct: 50 },
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
          No trained Gradient Boosting model for this league yet -- falling back to Elo + Poisson only.
        </p>
      )}
      <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 10 }}>
        These are the ensemble's blend weights, not proprietary internals. They're fitted once across every league on
        held-out matches, not learned per match -- see How It Works for how they were chosen.
      </p>
    </div>
  );
}

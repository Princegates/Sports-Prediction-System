import type { BlendWeights } from "../types";

interface Props {
  hasMlModel: boolean;
  // The weights this prediction was actually blended with, as stored in its
  // model_breakdown. Predictions made before that was recorded don't carry
  // it, so they show the shipped defaults below instead.
  weights?: BlendWeights;
}

// The ensemble weights the backend ships with (backend/.env.example
// ENSEMBLE_WEIGHT_*), fitted on every league's validation matches pooled.
// Only a fallback for older predictions -- see ``weights`` above.
const DEFAULT_WITH_ML: BlendWeights = { elo: 0.4, poisson: 0.4, ml: 0.2 };
const DEFAULT_WITHOUT_ML: BlendWeights = { elo: 0.5, poisson: 0.5, ml: 0 };

const LABELS: { key: keyof BlendWeights; name: string }[] = [
  { key: "elo", name: "Elo / Team Strength" },
  { key: "poisson", name: "Poisson Goal Model" },
  { key: "ml", name: "Gradient Boosting (form, rest, Elo gap)" },
];

export function ModelTransparency({ hasMlModel, weights }: Props) {
  const applied = weights ?? (hasMlModel ? DEFAULT_WITH_ML : DEFAULT_WITHOUT_ML);
  const rows = LABELS.filter((l) => l.key !== "ml" || hasMlModel).map((l) => ({
    name: l.name,
    pct: Math.round(applied[l.key] * 100),
  }));
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
        These are the weights this prediction was blended with, not proprietary internals. They're set once for every
        league, not learned per match -- see How It Works for how they were chosen.
      </p>
    </div>
  );
}

import type { TeamForm } from "../types";

interface Metric {
  label: string;
  home: number;
  away: number;
  format: (v: number) => string;
  invert?: boolean; // lower is better (e.g. goals conceded)
}

function Row({ metric }: { metric: Metric }) {
  const { home, away, invert } = metric;
  const homeShare = invert ? away + 0.001 : home + 0.001;
  const awayShare = invert ? home + 0.001 : away + 0.001;
  const total = homeShare + awayShare;
  const leftPct = (homeShare / total) * 100;

  return (
    <div>
      <div className="compare-row">
        <span className="compare-value">{metric.format(metric.home)}</span>
        <span className="stat-label" style={{ textAlign: "center" }}>
          {metric.label}
        </span>
        <span className="compare-value">{metric.format(metric.away)}</span>
      </div>
      <div className="compare-bars" style={{ marginBottom: 14 }}>
        <div className="compare-bar-left" style={{ width: `${leftPct}%` }} />
        <div className="compare-bar-right" style={{ width: `${100 - leftPct}%` }} />
      </div>
    </div>
  );
}

export function TeamComparison({ homeForm, awayForm }: { homeForm: TeamForm; awayForm: TeamForm }) {
  const metrics: Metric[] = [
    { label: "Form (pts/game)", home: homeForm.points_per_game, away: awayForm.points_per_game, format: (v) => v.toFixed(2) },
    { label: "Goals scored/game", home: homeForm.goals_scored_avg, away: awayForm.goals_scored_avg, format: (v) => v.toFixed(2) },
    {
      label: "Goals conceded/game",
      home: homeForm.goals_conceded_avg,
      away: awayForm.goals_conceded_avg,
      format: (v) => v.toFixed(2),
      invert: true,
    },
    { label: "Clean sheet rate", home: homeForm.clean_sheet_rate, away: awayForm.clean_sheet_rate, format: (v) => `${(v * 100).toFixed(0)}%` },
    { label: "Rest days", home: homeForm.rest_days, away: awayForm.rest_days, format: (v) => v.toFixed(0) },
  ];

  return (
    <div>
      {metrics.map((m) => (
        <Row key={m.label} metric={m} />
      ))}
    </div>
  );
}

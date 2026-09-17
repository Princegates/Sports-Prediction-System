import { SignalBar } from "./SignalBar";
import type { ModelBreakdown, TeamForm } from "../types";

interface Props {
  breakdown: ModelBreakdown;
  modelAgreement: number;
  dataQuality: number;
  homeForm?: TeamForm;
  awayForm?: TeamForm;
}

export function PredictionBreakdown({ breakdown, modelAgreement, dataQuality, homeForm, awayForm }: Props) {
  const signals: { label: string; value: number; tooltip: string }[] = [];

  if (breakdown.elo) {
    signals.push({
      label: "Team Strength (Elo)",
      value: breakdown.elo.home_win,
      tooltip: "Home win probability from the Elo rating model alone, including home advantage.",
    });
  }

  if (breakdown.poisson) {
    const { lambda_home, lambda_away } = breakdown.poisson;
    signals.push({
      label: "Expected Goals Edge",
      value: lambda_home / (lambda_home + lambda_away || 1),
      tooltip: `Home team's share of total expected goals (Poisson model): ${lambda_home.toFixed(2)} vs ${lambda_away.toFixed(2)}.`,
    });
  }

  if (homeForm && awayForm) {
    const total = homeForm.points_per_game + awayForm.points_per_game || 1;
    signals.push({
      label: "Recent Form Edge",
      value: homeForm.points_per_game / total,
      tooltip: `Home team's share of combined recent points-per-game: ${homeForm.points_per_game.toFixed(2)} vs ${awayForm.points_per_game.toFixed(2)}.`,
    });
  }

  signals.push({
    label: "Model Agreement",
    value: modelAgreement,
    tooltip: "How closely the Elo, Poisson and Gradient Boosting models agree with each other. Low agreement should reduce trust in the pick.",
  });
  signals.push({
    label: "Data Quality",
    value: dataQuality,
    tooltip: "How much reliable match history is available for both teams. Low quality means the prediction is based on thin data.",
  });

  return (
    <div>
      {signals.map((s) => (
        <SignalBar key={s.label} label={s.label} value={s.value} tooltip={s.tooltip} />
      ))}
    </div>
  );
}

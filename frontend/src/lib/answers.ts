import type { LivePrediction, Prediction, TeamForm } from "../types";

export interface MatchContext {
  homeTeam: string;
  awayTeam: string;
  prediction: Prediction;
  homeForm?: TeamForm;
  awayForm?: TeamForm;
  latestLive?: LivePrediction | null;
}

/**
 * Deterministic, rule-based answer generator -- NOT a live LLM call. Every
 * sentence it produces is assembled from numbers the backend already
 * computed for this match, so it can never fabricate a statistic. This
 * keeps the "Ask about this match" feature genuinely free to run and
 * genuinely grounded, at the cost of being less flexible than a real
 * language model would be.
 */
export function answerQuestion(question: string, ctx: MatchContext): string {
  const q = question.toLowerCase();
  const { prediction: p, homeTeam, awayTeam, homeForm, awayForm, latestLive } = ctx;

  const favored = p.home_win >= p.away_win ? homeTeam : awayTeam;
  const favoredPct = Math.round(Math.max(p.home_win, p.away_win) * 100);

  if (q.includes("favor") || q.includes("why")) {
    const factors = p.explanation.positive.slice(0, 3);
    if (factors.length === 0) {
      return `The model gives ${favored} a ${favoredPct}% win probability, close to a coin flip -- it didn't find a strong standout factor either way for this one.`;
    }
    return `The model favors ${favored} (${favoredPct}% win probability) mainly because: ${factors.join("; ")}.`;
  }

  if (q.includes("risk")) {
    if (p.explanation.negative.length === 0) {
      return `No significant risk factors were flagged against the favored side. Model agreement is ${(p.model_agreement_score * 100).toFixed(0)}%, and data quality is ${(p.data_quality_score * 100).toFixed(0)}%.`;
    }
    return `Biggest risk factors to the favored outcome: ${p.explanation.negative.join("; ")}.`;
  }

  if (q.includes("compare")) {
    if (!homeForm || !awayForm) {
      return "Team comparison data isn't loaded yet -- try again once the page finishes loading.";
    }
    return (
      `${homeTeam}: ${homeForm.points_per_game.toFixed(2)} pts/game, ${homeForm.goals_scored_avg.toFixed(2)} scored / ${homeForm.goals_conceded_avg.toFixed(2)} conceded per game recently. ` +
      `${awayTeam}: ${awayForm.points_per_game.toFixed(2)} pts/game, ${awayForm.goals_scored_avg.toFixed(2)} scored / ${awayForm.goals_conceded_avg.toFixed(2)} conceded per game recently.`
    );
  }

  if (q.includes("score")) {
    return `The single most likely final score is ${p.most_likely_score}, at ${(p.most_likely_score_probability * 100).toFixed(1)}% -- that's the highest individual scoreline in the model's distribution, not a majority likelihood on its own.`;
  }

  if (q.includes("chang")) {
    if (!latestLive) {
      return "This match hasn't kicked off yet (or no live events have been recorded), so there's no in-play probability change to report.";
    }
    return `As of minute ${latestLive.minute} (score ${latestLive.score_home}-${latestLive.score_away}), the live model has home win at ${(latestLive.home_win * 100).toFixed(0)}%, versus ${(p.home_win * 100).toFixed(0)}% pre-match.`;
  }

  return (
    `AI Assessment: ${p.global_outcome.selection} (${p.global_outcome.market}) is the single highest-probability outcome at ` +
    `${(p.global_outcome.probability * 100).toFixed(0)}%, with ${p.confidence.toLowerCase()} confidence. This is a model probability, not a guarantee -- ask "why is this team favored?" or "what are the biggest risks?" for the reasoning behind it.`
  );
}

export const SUGGESTED_QUESTIONS = [
  "Why is this team favored?",
  "What are the biggest risks?",
  "Compare both teams",
  "Explain the predicted score",
  "What changed during the match?",
];

"""Explainable AI (spec section 36): turn real feature deltas into a
plain-language positive/negative factor list, instead of a canned template.
"""

from __future__ import annotations

from app.features.team_stats import TeamForm


def generate_explanation(
    home_team: str,
    away_team: str,
    home_form: TeamForm,
    away_form: TeamForm,
    elo_diff: float,
    home_advantage: float,
) -> dict[str, list[str]]:
    positive: list[str] = []
    negative: list[str] = []

    def add(condition: bool, text: str, positive_for_home: bool) -> None:
        if not condition:
            return
        (positive if positive_for_home else negative).append(text)

    net_elo = elo_diff - home_advantage  # strip out the fixed home-advantage constant
    if abs(net_elo) >= 40:
        if net_elo > 0:
            positive.append(f"{home_team} rated stronger than {away_team} (Elo edge: +{net_elo:.0f})")
        else:
            negative.append(f"{away_team} rated stronger than {home_team} (Elo edge: {net_elo:.0f})")

    positive.append(f"Home advantage (+{home_advantage:.0f} Elo-equivalent)")

    ppg_diff = home_form.points_per_game - away_form.points_per_game
    if abs(ppg_diff) >= 0.4:
        add(ppg_diff > 0, f"Better recent form: {home_team} averaging {home_form.points_per_game:.2f} pts/game vs {away_form.points_per_game:.2f}", True)
        add(ppg_diff < 0, f"Better recent form: {away_team} averaging {away_form.points_per_game:.2f} pts/game vs {home_form.points_per_game:.2f}", False)

    attack_diff = home_form.goals_scored_avg - away_form.goals_conceded_avg
    if home_form.goals_scored_avg >= 1.8:
        positive.append(f"{home_team} scoring {home_form.goals_scored_avg:.2f} goals/game recently")
    if away_form.goals_conceded_avg >= 1.8:
        positive.append(f"{away_team} conceding {away_form.goals_conceded_avg:.2f} goals/game recently")
    if away_form.goals_scored_avg >= 1.8:
        negative.append(f"{away_team} scoring {away_form.goals_scored_avg:.2f} goals/game recently")
    if home_form.goals_conceded_avg >= 1.8:
        negative.append(f"{home_team} conceding {home_form.goals_conceded_avg:.2f} goals/game recently")

    rest_diff = home_form.rest_days - away_form.rest_days
    if abs(rest_diff) >= 2:
        add(rest_diff > 0, f"{home_team} had more rest ({home_form.rest_days:.0f} vs {away_form.rest_days:.0f} days)", True)
        add(rest_diff < 0, f"{away_team} had more rest ({away_form.rest_days:.0f} vs {home_form.rest_days:.0f} days)", False)

    if home_form.clean_sheet_rate >= 0.4:
        positive.append(f"{home_team} kept a clean sheet in {home_form.clean_sheet_rate:.0%} of recent matches")
    if away_form.clean_sheet_rate >= 0.4:
        negative.append(f"{away_team} kept a clean sheet in {away_form.clean_sheet_rate:.0%} of recent matches")

    if home_form.matches_played < 5 or away_form.matches_played < 5:
        negative.append("Limited recent match history for one or both teams -- treat this prediction with extra caution")

    return {"positive": positive, "negative": negative}

"""Turns retrieved database rows into the assistant's prose.

Every sentence produced here interpolates a value that was read from the
database by ``retrieval``. There is no branch that invents a probability, a
scoreline or a fixture, which is the property that makes this assistant safe
to point at a user who may act on what it says.

Responses carry structured ``sources`` alongside the text so the UI can link
to the match, team or methodology page the answer came from -- a claim the
user can click through to verify is worth more than a confident tone.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.assistant import retrieval
from app.assistant.nlu import Intent, ParsedQuery
from app.assistant.retrieval import MatchCard
from app.betcode.selection import DEFAULT_MIN_PROBABILITY, SlipCriteria, select_legs
from app.db.models import Prediction

# Never phrase a probability as a certainty. This line is appended to any
# answer that quotes a forward-looking number.
PROBABILITY_CAVEAT = (
    "These are model probabilities from historical validation, not guarantees -- "
    "even a 90% outcome misses roughly one time in ten."
)


@dataclass
class Source:
    kind: str  # "match" | "team" | "page"
    label: str
    ref: str | int | None = None


@dataclass
class PickRef:
    """A (match, market, selection) an answer named specifically enough to
    act on -- lets the client offer "price these for real" via AI
    Generation without re-parsing the prose reply. Probability-only here,
    same as everywhere else a best-picks-style answer is built: this reads
    from stored predictions, never a bookmaker quote."""

    match_id: int
    market: str
    selection: str


@dataclass
class Answer:
    text: str
    intent: Intent
    sources: list[Source] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    # Set when the answer quotes a forward-looking probability, so the client
    # can render the caveat as a distinct visual element rather than prose
    # the user skims past.
    includes_probability: bool = False
    picks: list[PickRef] = field(default_factory=list)


def _pct(value: float, places: int = 0) -> str:
    return f"{value * 100:.{places}f}%"


def _kickoff(when: dt.datetime) -> str:
    return when.strftime("%a %d %b, %H:%M UTC")


def _one_x_two_line(card: MatchCard, p: Prediction) -> str:
    return (
        f"{card.home_team} win {_pct(p.home_win)} / draw {_pct(p.draw)} / "
        f"{card.away_team} win {_pct(p.away_win)}"
    )


def _no_prediction_answer(card: MatchCard, intent: Intent) -> Answer:
    return Answer(
        text=(
            f"I found the fixture -- {card.home_team} vs {card.away_team}, {_kickoff(card.kickoff)} "
            f"({card.league}) -- but no prediction has been generated for it yet. Open the match page "
            f"and the ensemble will run on demand; I only report numbers the models have actually "
            f"produced rather than estimating one here."
        ),
        intent=intent,
        sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
        suggestions=["What are today's best picks?", "How accurate is the model?"],
    )


# --- Intent handlers ------------------------------------------------------


def _match_prediction(db: Session, q: ParsedQuery, card: MatchCard) -> Answer:
    p = card.prediction
    if p is None:
        return _no_prediction_answer(card, q.intent)

    played = card.home_score is not None and card.away_score is not None
    header = (
        f"{card.home_team} {card.home_score}-{card.away_score} {card.away_team} (played {_kickoff(card.kickoff)})"
        if played
        else f"{card.home_team} vs {card.away_team} -- {_kickoff(card.kickoff)}, {card.league}"
    )

    lines = [header, ""]
    lines.append(
        f"**Most likely outcome: {p.global_outcome_selection}** ({p.global_outcome_market}) at "
        f"{_pct(p.global_outcome_probability, 1)}, {p.confidence.lower()} confidence."
    )
    lines.append("")
    lines.append(f"- Match result: {_one_x_two_line(card, p)}")

    over25 = p.over_probabilities.get("2.5")
    if over25 is not None:
        lines.append(f"- Over 2.5 goals: {_pct(over25)} (under {_pct(1 - over25)})")
    lines.append(f"- Both teams to score: {_pct(p.btts_yes)} yes / {_pct(p.btts_no)} no")
    lines.append(
        f"- Most likely exact score: {p.most_likely_score} at {_pct(p.most_likely_score_probability, 1)}"
    )
    lines.append("")
    lines.append(
        f"Data quality {_pct(p.data_quality_score)}, model agreement {_pct(p.model_agreement_score)}."
    )

    if played:
        suggestions = [f"Head to head {card.home_team} vs {card.away_team}", "How accurate is the model?"]
    else:
        suggestions = [
            f"Why is this favored?",
            f"What are the risks?",
            f"Compare {card.home_team} and {card.away_team}",
        ]

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
        suggestions=suggestions,
        includes_probability=not played,
    )


def _explain(db: Session, q: ParsedQuery, card: MatchCard) -> Answer:
    p = card.prediction
    if p is None:
        return _no_prediction_answer(card, q.intent)

    explanation = p.explanation or {}
    positive = explanation.get("positive", [])
    negative = explanation.get("negative", [])
    want_risks = q.intent == Intent.RISKS
    factors = negative if want_risks else positive
    label = "Risk factors against the favored side" if want_risks else "Factors behind this call"

    lines = [
        f"**{p.global_outcome_selection}** ({p.global_outcome_market}) at "
        f"{_pct(p.global_outcome_probability, 1)} for {card.home_team} vs {card.away_team}.",
        "",
    ]

    if factors:
        lines.append(f"{label}:")
        lines.extend(f"- {factor}" for factor in factors)
    else:
        lines.append(
            f"The model didn't flag any standout {'risk' if want_risks else 'supporting'} factor for this "
            f"one -- it reads as a genuinely close matchup rather than a strong signal."
        )

    lines.append("")
    breakdown = p.model_breakdown or {}
    elo = breakdown.get("elo") or {}
    poisson = breakdown.get("poisson") or {}
    ml = breakdown.get("ml")
    lines.append("How each model voted on the match result:")
    if elo:
        lines.append(
            f"- Elo: home {_pct(elo.get('home_win', 0))} (rating gap {elo.get('elo_diff', 0):+.0f})"
        )
    if poisson:
        lines.append(
            f"- Poisson: home {_pct(poisson.get('home_win', 0))} "
            f"(expected goals {poisson.get('lambda_home', 0):.2f} - {poisson.get('lambda_away', 0):.2f})"
        )
    if ml:
        lines.append(f"- Gradient boosting: home {_pct(ml.get('H', 0))}")
    else:
        lines.append("- Gradient boosting: not trained for this league yet, so it was left out of the blend.")

    lines.append("")
    lines.append(
        f"They agree {_pct(p.model_agreement_score)} of the way; the data behind it scores "
        f"{_pct(p.data_quality_score)} for completeness."
    )

    other_side = "What are the risks?" if not want_risks else "Why is this favored?"
    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
        suggestions=[other_side, "How accurate is the model?", "Explain the predicted score"],
        includes_probability=True,
    )


def _correct_score(db: Session, q: ParsedQuery, card: MatchCard) -> Answer:
    p = card.prediction
    if p is None:
        return _no_prediction_answer(card, q.intent)

    scores = sorted((p.correct_score_probabilities or {}).items(), key=lambda kv: kv[1], reverse=True)[:5]
    lines = [
        f"Most likely scorelines for {card.home_team} vs {card.away_team}:",
        "",
    ]
    lines.extend(f"- {score}: {_pct(prob, 1)}" for score, prob in scores)

    if scores:
        top_score, top_prob = scores[0]
        lines.append("")
        lines.append(
            f"{top_score} is the single highest scoreline at {_pct(top_prob, 1)} -- worth keeping in "
            f"perspective, since that still means roughly a {_pct(1 - top_prob)} chance the match ends "
            f"some other way. Correct score is the hardest market there is."
        )

    over25 = (p.over_probabilities or {}).get("2.5")
    if over25 is not None:
        lines.append("")
        lines.append(
            f"If you care about goals rather than the exact score: over 2.5 sits at {_pct(over25)}."
        )

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
        suggestions=["Why is this favored?", "What are the risks?"],
        includes_probability=True,
    )


def _team_form(db: Session, q: ParsedQuery) -> Answer:
    team = q.teams[0]
    form = retrieval.team_form(db, team)

    if form.matches_played == 0:
        return Answer(
            text=(
                f"I have {team.name} in the database ({team.league}) but no completed matches for them "
                f"yet, so there's no form to report. Importing more historical seasons for this league "
                f"would fix that."
            ),
            intent=q.intent,
            sources=[Source("team", team.name, team.id)],
            suggestions=["What are today's best picks?", "How does the model work?"],
        )

    results = "".join(form.recent_results[-5:]) or "n/a"
    lines = [
        f"**{team.name}** ({team.league}) -- last {form.matches_played} matches:",
        "",
        f"- Recent results: {results} (oldest to newest)",
        f"- Points per game: {form.points_per_game:.2f}",
        f"- Goals: {form.goals_scored_avg:.2f} scored / {form.goals_conceded_avg:.2f} conceded per game",
        f"- At home: {form.home_goals_scored_avg:.2f} scored / {form.home_goals_conceded_avg:.2f} conceded",
        f"- Away: {form.away_goals_scored_avg:.2f} scored / {form.away_goals_conceded_avg:.2f} conceded",
        f"- Clean sheets: {_pct(form.clean_sheet_rate)} of matches",
        f"- Days since last match: {form.rest_days:.0f}",
    ]

    sources = [Source("team", team.name, team.id)]
    suggestions = [f"How accurate is the model?"]

    nxt = retrieval.next_fixture_for_team(db, team)
    if nxt is not None:
        opponent = nxt.away_team if nxt.home_team == team.name else nxt.home_team
        lines.append("")
        lines.append(f"Next up: {nxt.home_team} vs {nxt.away_team}, {_kickoff(nxt.kickoff)}.")
        if nxt.prediction is not None:
            lines.append(
                f"The model's call on it: {nxt.prediction.global_outcome_selection} "
                f"({nxt.prediction.global_outcome_market}) at "
                f"{_pct(nxt.prediction.global_outcome_probability, 1)}."
            )
        sources.append(Source("match", f"{nxt.home_team} vs {nxt.away_team}", nxt.match_id))
        suggestions.insert(0, f"{team.name} vs {opponent} prediction")

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=sources,
        suggestions=suggestions,
        includes_probability=nxt is not None and nxt.prediction is not None,
    )


def _head_to_head(db: Session, q: ParsedQuery) -> Answer:
    team_a, team_b = q.teams[0], q.teams[1]
    meetings = retrieval.head_to_head(db, team_a, team_b)

    if not meetings:
        return Answer(
            text=(
                f"No completed meetings between {team_a.name} and {team_b.name} are in the database. "
                f"They may not have played inside the seasons that have been imported."
            ),
            intent=q.intent,
            sources=[Source("team", team_a.name, team_a.id), Source("team", team_b.name, team_b.id)],
            suggestions=[f"{team_a.name} form", f"{team_b.name} form"],
        )

    a_wins = b_wins = draws = 0
    a_goals = b_goals = 0
    for m in meetings:
        home_is_a = m.home_team == team_a.name
        a_score = m.home_score if home_is_a else m.away_score
        b_score = m.away_score if home_is_a else m.home_score
        a_goals += a_score or 0
        b_goals += b_score or 0
        if (a_score or 0) > (b_score or 0):
            a_wins += 1
        elif (a_score or 0) < (b_score or 0):
            b_wins += 1
        else:
            draws += 1

    lines = [
        f"**{team_a.name} vs {team_b.name}** -- last {len(meetings)} meetings in the database:",
        "",
        f"- {team_a.name}: {a_wins} wins",
        f"- {team_b.name}: {b_wins} wins",
        f"- Draws: {draws}",
        f"- Goals: {a_goals} - {b_goals}",
        "",
        "Most recent:",
    ]
    for m in meetings[:5]:
        lines.append(
            f"- {m.kickoff.date().isoformat()}: {m.home_team} {m.home_score}-{m.away_score} {m.away_team}"
        )

    lines.append("")
    lines.append(
        "Worth noting: head-to-head history is *not* a direct input to the ensemble -- it uses Elo, "
        "recent form and goal-rate features instead, because squads turn over enough that a result "
        "from three years ago says little about the current teams. Treat this as context, not signal."
    )

    sources = [Source("team", team_a.name, team_a.id), Source("team", team_b.name, team_b.id)]
    upcoming = retrieval.find_match_for_teams(db, team_a, team_b)
    suggestions = []
    if upcoming is not None:
        sources.append(Source("match", f"{upcoming.home_team} vs {upcoming.away_team}", upcoming.match_id))
        suggestions.append(f"{team_a.name} vs {team_b.name} prediction")
    suggestions.append("How does the model work?")

    return Answer(text="\n".join(lines), intent=q.intent, sources=sources, suggestions=suggestions)


def _compare_teams(db: Session, q: ParsedQuery) -> Answer:
    """Each side's own recent record, side by side -- distinct from H2H,
    which is about their past meetings with each other. A team missing an
    away game or a home game entirely (small sample) still compares fine on
    every other row; the recent-form line is the one place that just goes
    thin rather than wrong.
    """

    team_a, team_b = q.teams[0], q.teams[1]
    form_a = retrieval.team_form(db, team_a)
    form_b = retrieval.team_form(db, team_b)

    if form_a.matches_played == 0 or form_b.matches_played == 0:
        empty = team_a.name if form_a.matches_played == 0 else team_b.name
        return Answer(
            text=(
                f"{empty} has no completed matches in the database yet, so there isn't enough to "
                f"compare it against the other side."
            ),
            intent=q.intent,
            sources=[Source("team", team_a.name, team_a.id), Source("team", team_b.name, team_b.id)],
            suggestions=[f"{team_a.name} form", f"{team_b.name} form"],
        )

    def row(label: str, a: float, b: float, *, lower_is_better: bool = False, as_pct: bool = False) -> str:
        fmt = _pct if as_pct else (lambda v: f"{v:.2f}")
        if a == b:
            edge = "even"
        else:
            edge = team_a.name if (a < b if lower_is_better else a > b) else team_b.name
        return f"- {label}: {team_a.name} {fmt(a)} vs {team_b.name} {fmt(b)} -- edge {edge}"

    lines = [
        f"**{team_a.name}** vs **{team_b.name}** -- each side's own recent record, not their head-to-head:",
        "",
        row("Points per game", form_a.points_per_game, form_b.points_per_game),
        row("Goals scored per game", form_a.goals_scored_avg, form_b.goals_scored_avg),
        row("Goals conceded per game", form_a.goals_conceded_avg, form_b.goals_conceded_avg, lower_is_better=True),
        row("Clean sheet rate", form_a.clean_sheet_rate, form_b.clean_sheet_rate, as_pct=True),
        row("Home goals scored per game", form_a.home_goals_scored_avg, form_b.home_goals_scored_avg),
        row("Away goals scored per game", form_a.away_goals_scored_avg, form_b.away_goals_scored_avg),
        "",
        f"- Recent form: {team_a.name} {''.join(form_a.recent_results[-5:]) or 'n/a'} vs "
        f"{team_b.name} {''.join(form_b.recent_results[-5:]) or 'n/a'}",
    ]

    sources = [Source("team", team_a.name, team_a.id), Source("team", team_b.name, team_b.id)]
    suggestions = []

    if retrieval.head_to_head(db, team_a, team_b, limit=1):
        suggestions.append(f"Head to head {team_a.name} vs {team_b.name}")

    upcoming = retrieval.find_match_for_teams(db, team_a, team_b)
    if upcoming is not None:
        sources.append(Source("match", f"{upcoming.home_team} vs {upcoming.away_team}", upcoming.match_id))
        suggestions.append(f"{team_a.name} vs {team_b.name} prediction")

    return Answer(text="\n".join(lines), intent=q.intent, sources=sources, suggestions=suggestions)


def _todays_card(db: Session, q: ParsedQuery, now: dt.datetime) -> Answer:
    today = dt.datetime.combine(now.date(), dt.time.min)
    date_from = q.date_from or today
    date_to = q.date_to or today + dt.timedelta(days=1)
    cards = retrieval.fixtures_between(db, date_from, date_to, q.league)

    window = "today"
    if q.date_from and (q.date_from - today).days == 1:
        window = "tomorrow"
    elif q.date_to and (q.date_to - date_from).days > 1:
        window = f"{date_from.date().isoformat()} to {(date_to - dt.timedelta(days=1)).date().isoformat()}"

    if not cards:
        return Answer(
            text=(
                f"No fixtures {window} in the imported data"
                f"{f' for {q.league}' if q.league else ''}. Either there genuinely are none, or that "
                f"league/season hasn't been imported yet -- the data scripts in `backend/scripts` pull "
                f"more in."
            ),
            intent=q.intent,
            sources=[Source("page", "Fixtures", "/predictions")],
            suggestions=["What are the best picks this week?", "How accurate is the model?"],
        )

    lines = [f"**{len(cards)} fixture{'s' if len(cards) != 1 else ''} {window}:**", ""]
    quoted_probability = False
    for c in cards:
        line = f"- {c.kickoff.strftime('%H:%M')} {c.home_team} vs {c.away_team}"
        if c.prediction is not None:
            line += (
                f" -- {c.prediction.global_outcome_selection} "
                f"({_pct(c.prediction.global_outcome_probability)}, {c.prediction.confidence.lower()})"
            )
            quoted_probability = True
        else:
            line += " -- no prediction generated yet"
        lines.append(line)

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("page", "Fixtures", "/predictions")]
        + [Source("match", f"{c.home_team} vs {c.away_team}", c.match_id) for c in cards[:5]],
        suggestions=["What are the best picks?", "Show me high-confidence calls only"],
        includes_probability=quoted_probability,
    )


_MARKET_LABELS = {
    "btts": "both teams to score",
    "over_under": "over/under 2.5 goals",
    "double_chance": "double chance",
    "correct_score": "correct score",
    "1x2": "match result",
}


def _best_picks_for_market(db: Session, q: ParsedQuery, now: dt.datetime) -> Answer:
    """"Best BTTS picks", "top over/under picks" -- q.market filters this to
    one market family instead of _best_picks' global most-likely outcome,
    which could be Match Result on one match and Correct Score on the next."""

    date_from = q.date_from or now
    date_to = q.date_to or now + dt.timedelta(days=3)
    hits = retrieval.best_outcomes(db, q.market, date_from, date_to, q.league, limit=5)
    label = _MARKET_LABELS.get(q.market, q.market)

    if not hits:
        return Answer(
            text=(
                f"No stored predictions in the next few days price a {label} outcome I can rank -- "
                f"either there's nothing scheduled, or the fixtures haven't had predictions generated "
                f"for them yet."
            ),
            intent=q.intent,
            sources=[Source("page", "Markets", "/app/markets")],
            suggestions=["What are today's best picks?", "What fixtures are on today?"],
        )

    lines = [f"**Highest-probability {label} calls in the next few days:**", ""]
    for i, h in enumerate(hits, start=1):
        lines.append(f"{i}. **{h.selection}** ({h.market}) -- {_pct(h.probability, 1)}")
        lines.append(f"   {h.home_team} vs {h.away_team}, {_kickoff(h.kickoff)} · {h.confidence.lower()} confidence")

    lines.append("")
    lines.append(
        "Ranked purely by model probability, same caveat as any best-picks list: a high probability "
        "and a good price are different things -- this doesn't look at bookmaker odds at all."
    )

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("match", f"{h.home_team} vs {h.away_team}", h.match_id) for h in hits],
        picks=[PickRef(h.match_id, h.market, h.selection) for h in hits],
        suggestions=["What are today's best picks?", "What fixtures are on today?"],
        includes_probability=True,
    )


def _best_picks(db: Session, q: ParsedQuery, now: dt.datetime) -> Answer:
    if q.market:
        return _best_picks_for_market(db, q, now)

    date_from = q.date_from or now
    date_to = q.date_to or now + dt.timedelta(days=3)
    cards = retrieval.ranked_predictions(db, date_from, date_to, q.league, limit=5)

    if not cards:
        return Answer(
            text=(
                "No stored predictions for fixtures in the next few days, so I have nothing to rank. "
                "Predictions are generated per match (open a match page, or run "
                "`scripts/generate_predictions.py`) -- I deliberately don't spin up the models for a "
                "whole fixture list inside a chat message."
            ),
            intent=q.intent,
            sources=[Source("page", "Predictions", "/predictions")],
            suggestions=["What fixtures are on today?", "How accurate is the model?"],
        )

    lines = ["**Highest-probability calls in the next few days:**", ""]
    for i, c in enumerate(cards, start=1):
        p = c.prediction
        assert p is not None  # ranked_predictions only returns cards with one
        lines.append(
            f"{i}. **{p.global_outcome_selection}** ({p.global_outcome_market}) -- "
            f"{_pct(p.global_outcome_probability, 1)}"
        )
        lines.append(
            f"   {c.home_team} vs {c.away_team}, {_kickoff(c.kickoff)} · {p.confidence.lower()} confidence, "
            f"data quality {_pct(p.data_quality_score)}"
        )

    lines.append("")
    lines.append(
        "Ranked purely by model probability. Note that the highest-probability pick is often a "
        "low-information one (a heavy favorite, or an over 0.5 goals line) -- high probability and "
        "high *value* are different things, and this system doesn't look at bookmaker odds at all, so "
        "it can't tell you which of these is mispriced."
    )

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("match", f"{c.home_team} vs {c.away_team}", c.match_id) for c in cards],
        picks=[PickRef(c.match_id, c.prediction.global_outcome_market, c.prediction.global_outcome_selection) for c in cards],
        suggestions=[
            f"Why is {cards[0].prediction.global_outcome_selection} favored?",
            "What fixtures are on today?",
            "How accurate is the model?",
        ],
        includes_probability=True,
    )


def _generate_selections(db: Session, q: ParsedQuery) -> Answer:
    """"Give me 20 selections with at least 50% chance" and its variants --
    the chat entry point to the same engine AI Generation's page uses
    (app.betcode.selection.select_legs), so a chat answer and a page preview
    can never disagree about what counts as a real, priced pick.

    Target odds is set effectively unreachable rather than left to a
    default: a count and a floor were what the user actually asked for, and
    a target price stopping the search early would silently hand back fewer
    legs than requested without saying why.
    """

    count = q.selection_count or 10
    floor = q.probability_floor if q.probability_floor is not None else DEFAULT_MIN_PROBABILITY

    criteria = SlipCriteria(
        bookmaker="any",
        target_odds=1_000_000.0,
        min_probability=floor,
        max_legs=count,
        league=q.league,
        days_ahead=7,
    )
    result = select_legs(db, criteria)

    if not result.legs:
        return Answer(
            text=(
                f"No scheduled match in the next 7 days{f' in {q.league}' if q.league else ''} both clears "
                f"{_pct(floor)} model probability and has a real, stored bookmaker price -- so there's "
                f"nothing I can put together honestly. Try a lower floor, a different league, or capture "
                f"more odds first (Settings -> Data sources)."
            ),
            intent=q.intent,
            sources=[Source("page", "AI Generation", "/app/betcodes")],
            suggestions=["What are today's best picks?", "How accurate is the model?"],
        )

    lines = [
        f"**{len(result.legs)} selection{'s' if len(result.legs) != 1 else ''}**, each priced from a real, "
        f"stored bookmaker quote, {_pct(floor)}+ model probability:",
        "",
    ]
    for i, leg in enumerate(result.legs, start=1):
        lines.append(
            f"{i}. **{leg.selection}** ({leg.market}) -- {leg.home_team} vs {leg.away_team}, "
            f"{_kickoff(leg.kickoff)} -- {_pct(leg.model_probability)} at {leg.decimal_odds:.2f} "
            f"({leg.priced_by})"
        )

    lines.append("")
    if len(result.legs) < count:
        lines.append(
            f"That's every match in the next 7 days{f' in {q.league}' if q.league else ''} that clears "
            f"{_pct(floor)} with a real price -- short of the {count} you asked for. A lower floor or a "
            f"wider league would surface more."
        )
        lines.append("")
    lines.append(
        f"Combined: {result.combined_odds:.2f} odds, {_pct(result.combined_probability)} probability -- "
        f"stacking {len(result.legs)} legs multiplies the risk, it doesn't add the confidence."
    )

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("page", "AI Generation", "/app/betcodes")]
        + [Source("match", f"{leg.home_team} vs {leg.away_team}", leg.match_id) for leg in result.legs[:5]],
        suggestions=[
            "What are today's best picks?",
            f"Give me {count} selections with at least {min(95, int((floor + 0.15) * 100))}% chance",
        ],
        includes_probability=True,
    )


def _accuracy(db: Session, q: ParsedQuery) -> Answer:
    snapshot = retrieval.accuracy_snapshot(db)

    if not snapshot.has_data:
        return Answer(
            text=(
                "No backtest has been recorded in this database yet, so I can't quote a hit rate -- and "
                "I'm not going to invent one. Running `python scripts/backtest.py --league-name \"...\"` "
                "does a date-split (never shuffled) train/validation/test evaluation and stores accuracy, "
                "log loss, Brier score and a calibration table, which is what I'd report here."
            ),
            intent=q.intent,
            sources=[Source("page", "Methodology", "/how-it-works")],
            suggestions=["How does the model work?", "What fixtures are on today?"],
        )

    # The held-out test split is the only honest headline number; train-split
    # accuracy is what the model already saw.
    preferred = snapshot.splits.get("test") or snapshot.splits.get("validation") or next(iter(snapshot.splits.values()))
    split_name = "test" if "test" in snapshot.splits else ("validation" if "validation" in snapshot.splits else "recorded")

    lines = [
        f"Measured on the **held-out {split_name} split** of a temporal backtest "
        f"(model `{snapshot.model_version}`"
        + (f", run {snapshot.computed_at.date().isoformat()}" if snapshot.computed_at else "")
        + "):",
        "",
    ]

    # Explicit labels rather than derived ones: title-casing the stored keys
    # turns "over_2_5_log_loss" into "Over 2 5 Log Loss", and the goal-line
    # metrics are exactly the ones a user is most likely to ask about.
    label_map = {
        "n": ("Matches evaluated", "{:.0f}"),
        "matches": ("Matches evaluated", "{:.0f}"),
        "accuracy": ("1X2 accuracy", "{:.1%}"),
        "log_loss": ("1X2 log loss", "{:.4f}"),
        "brier_score": ("1X2 Brier score", "{:.4f}"),
        "brier": ("1X2 Brier score", "{:.4f}"),
        "calibration_error": ("Calibration error (ECE)", "{:.4f}"),
        "over_2_5_log_loss": ("Over 2.5 log loss", "{:.4f}"),
        "over_2_5_brier": ("Over 2.5 Brier score", "{:.4f}"),
        "btts_log_loss": ("Both-teams-to-score log loss", "{:.4f}"),
        "btts_brier": ("Both-teams-to-score Brier score", "{:.4f}"),
    }
    # Report in a deliberate order (headline first) rather than dict order.
    ordering = list(label_map)
    for key in sorted(preferred, key=lambda k: (ordering.index(k) if k in ordering else len(ordering), k)):
        value = preferred[key]
        label, fmt = label_map.get(key, (key.replace("_", " ").capitalize(), "{:.4f}"))
        lines.append(f"- {label}: {fmt.format(value)}")

    if snapshot.leagues:
        lines.append("")
        lines.append(f"Leagues covered: {', '.join(snapshot.leagues)}.")

    lines.append("")
    lines.append(
        "The split is by date, never shuffled, so the model is always scored on matches that happened "
        "strictly after everything it trained on -- no lookahead. For reference, football 1X2 is a "
        "genuinely hard problem: bookmakers land around 53-55% accuracy, so treat anything claiming "
        "much more than that (here or anywhere) with suspicion."
    )

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("page", "Model accuracy", "/how-it-works")],
        suggestions=["How does the model work?", "What are today's best picks?"],
    )


def _methodology(db: Session, q: ParsedQuery) -> Answer:
    counts = retrieval.coverage_counts(db)
    text = f"""**How the predictions are made**

Four stages, all running locally on open-source libraries:

1. **Elo ratings** -- a match-by-match rating for every team, with a home-advantage
   offset and a margin-of-victory multiplier, mapped to 1X2 probabilities through a
   fitted logistic curve.
2. **Dixon-Coles Poisson** -- attack and defense strength per team, split home/away,
   with the low-score correction. This produces the full score matrix, which is where
   over/under lines, both-teams-to-score and correct-score numbers come from.
3. **Gradient boosting** -- scikit-learn, trained on engineered features: recent form,
   goal differentials, rest days, Elo gap.
4. **Blend + calibration** -- the three are combined on configured weights, then passed
   through isotonic regression fitted on a held-out validation split, so a stated 70%
   means the outcome actually happened about 70% of the time historically.

The **Global Most-Likely Outcome** you see on each match is then `argmax` across every
market in the outcome registry -- but only over outcomes that clear a data-quality gate,
so a team with three matches of history can't produce a confident-looking call.

**What's in the database right now:** {counts['matches_analyzed']:,} completed matches,
{counts['teams']:,} teams, {counts['leagues']} leagues, {counts['predictions_generated']:,} predictions
generated.

Two deliberate design choices worth knowing: the system **never looks at bookmaker odds**,
so its probabilities are independent rather than a re-derivation of the market; and every
feature is computed as-of the fixture date, so backtests can't see the future."""

    return Answer(
        text=text,
        intent=q.intent,
        sources=[Source("page", "How it works", "/how-it-works")],
        suggestions=["How accurate is the model?", "What are today's best picks?"],
    )


def _live_status(db: Session, q: ParsedQuery) -> Answer:
    if q.teams and len(q.teams) >= 2:
        card = retrieval.find_match_for_teams(db, q.teams[0], q.teams[1])
        cards = [card] if card is not None else []
    else:
        cards = retrieval.live_matches(db)

    if not cards:
        return Answer(
            text=(
                "Nothing is marked live right now. In-play probabilities update when match events are "
                "pushed to the live endpoint -- there's no free second-by-second feed wired up, so live "
                "data arrives either from the manual event controls on a match page or from a provider "
                "you connect later."
            ),
            intent=q.intent,
            sources=[Source("page", "Live", "/live")],
            suggestions=["What fixtures are on today?", "What are the best picks?"],
        )

    lines = ["**Live now:**", ""]
    for c in cards:
        snapshots = retrieval.live_snapshots(db, c.match_id)
        if snapshots:
            s = snapshots[0]
            lines.append(
                f"- {c.home_team} {s.score_home}-{s.score_away} {c.away_team} ({s.minute}') -- "
                f"{s.global_outcome_selection} at {_pct(s.global_outcome_probability)} "
                f"(last update triggered by: {s.trigger_event.replace('_', ' ')})"
            )
        else:
            lines.append(f"- {c.home_team} vs {c.away_team} -- live, no in-play snapshot recorded yet")

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("page", "Live", "/live")]
        + [Source("match", f"{c.home_team} vs {c.away_team}", c.match_id) for c in cards[:5]],
        suggestions=["What fixtures are on today?", "How does the live model work?"],
        includes_probability=bool(cards),
    )


def _what_changed(db: Session, q: ParsedQuery, card: MatchCard) -> Answer:
    """Explains a live probability swing by comparing the two most recent
    ``LivePrediction`` snapshots, rather than just restating the current
    number the way live_status does.

    The leading selection itself can flip between snapshots (a different
    outcome becomes the model's top call, not just a probability shift on
    the same one) -- reporting a plain before/after delta in that case would
    silently compare two different bets, so that gets its own sentence
    instead.
    """

    snapshots = retrieval.live_snapshots(db, card.match_id, limit=2)

    if not snapshots:
        return Answer(
            text=(
                f"No live events have been recorded yet for {card.home_team} vs {card.away_team}, "
                f"so there's nothing to explain a change in. Ask again once it kicks off."
            ),
            intent=q.intent,
            sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
            suggestions=[f"{card.home_team} vs {card.away_team} prediction"],
        )

    latest = snapshots[0]
    if len(snapshots) == 1:
        lines = [
            f"**{card.home_team} {latest.score_home}-{latest.score_away} {card.away_team}** "
            f"({latest.minute}') -- this is the first live update recorded, triggered by "
            f"{latest.trigger_event.replace('_', ' ')}. There's no earlier snapshot to compare it "
            f"against yet.",
            "",
            f"Right now the model favors {latest.global_outcome_selection} at "
            f"{_pct(latest.global_outcome_probability)}.",
        ]
        return Answer(
            text="\n".join(lines),
            intent=q.intent,
            sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
            suggestions=["What's live right now?"],
            includes_probability=True,
        )

    previous = snapshots[1]
    trigger = latest.trigger_event.replace("_", " ")
    same_selection = latest.global_outcome_selection == previous.global_outcome_selection
    score_changed = (latest.score_home, latest.score_away) != (previous.score_home, previous.score_away)

    lines = [
        f"**{card.home_team} {latest.score_home}-{latest.score_away} {card.away_team}** ({latest.minute}'):",
        "",
    ]
    if same_selection:
        delta = latest.global_outcome_probability - previous.global_outcome_probability
        direction = "moved up" if delta > 0 else "moved down" if delta < 0 else "held steady"
        lines.append(
            f"{latest.global_outcome_selection} {direction} from {_pct(previous.global_outcome_probability)} "
            f"to {_pct(latest.global_outcome_probability)} ({abs(delta) * 100:.0f} point"
            f"{'s' if abs(round(delta * 100)) != 1 else ''}), driven by: {trigger}."
        )
    else:
        lines.append(
            f"The model's leading call flipped from {previous.global_outcome_selection} "
            f"({_pct(previous.global_outcome_probability)}) to {latest.global_outcome_selection} "
            f"({_pct(latest.global_outcome_probability)}), driven by: {trigger}."
        )
    if score_changed:
        lines.append(
            f"Score moved from {previous.score_home}-{previous.score_away} to "
            f"{latest.score_home}-{latest.score_away} at minute {latest.minute}."
        )

    return Answer(
        text="\n".join(lines),
        intent=q.intent,
        sources=[Source("match", f"{card.home_team} vs {card.away_team}", card.match_id)],
        suggestions=[f"{card.home_team} vs {card.away_team} prediction", "What's live right now?"],
        includes_probability=True,
    )


def _responsible_use(q: ParsedQuery) -> Answer:
    return Answer(
        text=(
            "I want to be straight with you about this rather than give you a number.\n\n"
            "This system outputs **probabilities**, not outcomes. There is no such thing as a guaranteed "
            "or fixed result here, and anyone selling you one is lying. A 90% call still loses about one "
            "time in ten, and those losses arrive in clusters, not politely spaced out.\n\n"
            "So: never stake money you need for anything else -- rent, food, debt, someone else's "
            "expenses. Never increase a stake to recover a loss; that's the single most reliable way "
            "people turn a bad week into a serious problem. And if the honest answer to \"can I stop?\" "
            "is no, that's worth taking seriously on its own terms.\n\n"
            # Ghana's own number, because this platform's members are in Ghana and a
            # US 1-800 line -- which this used to give -- cannot be dialled from here.
            # Someone reaching for help would have got a dead line.
            "Free, confidential help exists and works. In Ghana, the **Mental Health Authority** "
            "runs a toll-free line on **0800 678 678**, any hour, from any network. The "
            "**Gaming Commission of Ghana** (gamingcommission.gov.gh) handles self-exclusion if you "
            "want to be blocked from betting sites. Online and free from anywhere: **Gambling Therapy** "
            "(gamblingtherapy.org) for one-to-one support by text, and **Gamblers Anonymous** "
            "(gamblersanonymous.org) for peer meetings.\n\n"
            "I'm happy to keep talking about what the model thinks and why -- I just won't dress a "
            "probability up as a certainty."
        ),
        intent=Intent.RESPONSIBLE_USE,
        sources=[Source("page", "Responsible use", "/responsible")],
        suggestions=["How accurate is the model, really?", "How does the model work?"],
    )


def _help(db: Session, q: ParsedQuery, greeting: bool) -> Answer:
    counts = retrieval.coverage_counts(db)
    opener = (
        "Hey. I'm the match intelligence assistant."
        if greeting
        else "Here's what I can actually answer:"
    )
    text = f"""{opener}

Everything I say comes from this system's own database -- {counts['matches_analyzed']:,} completed
matches across {counts['leagues']} leagues, {counts['predictions_generated']:,} generated predictions.
I read those rows and report them; I don't improvise numbers.

**Try asking:**
- "Arsenal vs Chelsea" -- full prediction for a fixture
- "What's on today?" / "fixtures this weekend"
- "What are the best picks?" -- ranked by model probability
- "Best BTTS picks" / "top double chance picks" -- ranked within one market
- "Give me 10 selections with at least 60% chance" -- a combo priced from real bookmaker odds
- "Why is this favored?" / "what are the risks?" -- the reasoning behind a call
- "Liverpool form" -- recent results and goal rates
- "Head to head Arsenal vs Spurs"
- "Compare Arsenal and Chelsea" -- each side's own form, side by side
- "What changed?" on a live match -- what moved the probability, and why
- "How accurate are you?" -- real backtest numbers, held-out split
- "How does the model work?" -- the actual methodology

If I don't recognize a question I'll say so rather than guess at it."""

    return Answer(
        text=text,
        intent=q.intent,
        sources=[Source("page", "How it works", "/how-it-works")],
        suggestions=["What are today's best picks?", "How accurate is the model?", "How does the model work?"],
    )


def _unknown(db: Session, q: ParsedQuery) -> Answer:
    return Answer(
        text=(
            "I couldn't match that to something I can answer from the database, and I'd rather say so "
            "than guess.\n\n"
            "I handle fixtures and predictions (\"Arsenal vs Chelsea\", \"what's on today\"), reasoning "
            "(\"why is this favored\", \"what are the risks\"), team form and head-to-head, best picks, "
            "and questions about the model's own accuracy and methodology.\n\n"
            "If you named a team and I missed it, it may not be in the imported leagues yet -- try the "
            "team search, or ask me \"what leagues do you cover?\""
        ),
        intent=Intent.UNKNOWN,
        sources=[],
        suggestions=["What can you do?", "What are today's best picks?", "How does the model work?"],
    )


# --- Entry point ----------------------------------------------------------


def _resolve_card(db: Session, q: ParsedQuery) -> MatchCard | None:
    """The match a match-scoped intent should answer about: the fixture
    between two named teams, else whatever the user has open in the UI."""

    if len(q.teams) >= 2:
        card = retrieval.find_match_for_teams(db, q.teams[0], q.teams[1])
        if card is not None:
            return card
    if q.context_match_id is not None:
        return retrieval.get_match_card(db, q.context_match_id)
    return None


def respond(db: Session, q: ParsedQuery, now: dt.datetime | None = None) -> Answer:
    now = now or dt.datetime.utcnow()

    if q.intent == Intent.RESPONSIBLE_USE:
        return _responsible_use(q)
    if q.intent in (Intent.HELP, Intent.GREETING):
        return _help(db, q, greeting=q.intent == Intent.GREETING)
    if q.intent == Intent.ACCURACY:
        return _accuracy(db, q)
    if q.intent == Intent.METHODOLOGY:
        return _methodology(db, q)
    if q.intent == Intent.LIVE_STATUS:
        return _live_status(db, q)
    if q.intent == Intent.TODAYS_CARD:
        return _todays_card(db, q, now)
    if q.intent == Intent.BEST_PICKS:
        return _best_picks(db, q, now)
    if q.intent == Intent.GENERATE_SELECTIONS:
        return _generate_selections(db, q)
    if q.intent == Intent.HEAD_TO_HEAD and len(q.teams) >= 2:
        return _head_to_head(db, q)
    if q.intent == Intent.COMPARE_TEAMS and len(q.teams) >= 2:
        return _compare_teams(db, q)
    if q.intent == Intent.TEAM_FORM and q.teams:
        return _team_form(db, q)

    if q.intent in (
        Intent.MATCH_PREDICTION,
        Intent.EXPLAIN_REASONING,
        Intent.RISKS,
        Intent.CORRECT_SCORE,
        Intent.WHAT_CHANGED,
    ):
        card = _resolve_card(db, q)
        if card is None:
            if len(q.teams) >= 2:
                return Answer(
                    text=(
                        f"I have both {q.teams[0].name} and {q.teams[1].name} in the database, but no "
                        f"fixture between them -- played or scheduled -- inside the imported seasons. "
                        f"They may be in different leagues, or that season isn't loaded."
                    ),
                    intent=q.intent,
                    sources=[
                        Source("team", q.teams[0].name, q.teams[0].id),
                        Source("team", q.teams[1].name, q.teams[1].id),
                    ],
                    suggestions=[f"{q.teams[0].name} form", f"{q.teams[1].name} form"],
                )
            return _unknown(db, q)

        if q.intent == Intent.CORRECT_SCORE:
            return _correct_score(db, q, card)
        if q.intent in (Intent.EXPLAIN_REASONING, Intent.RISKS):
            return _explain(db, q, card)
        if q.intent == Intent.WHAT_CHANGED:
            return _what_changed(db, q, card)
        return _match_prediction(db, q, card)

    return _unknown(db, q)

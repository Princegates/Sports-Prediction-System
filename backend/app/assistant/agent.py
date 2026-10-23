"""Smart mode: Claude works out what is being asked and looks the facts up.

The built-in pipeline (nlu -> retrieval -> responder) decides what a message
means with keyword patterns, so anything it doesn't have a pattern for -- a
compound question, an unusual phrasing, "which of these two is the safer
second-half goals bet?" -- fell through to a menu. Here the model does that
part instead: it reads the question (and the last few turns), decides which
lookups it needs, calls them, reasons over what comes back, and writes the
answer.

What does *not* change is where facts come from. Every number the model can
state arrives through a tool that reads this system's own database -- the same
functions the built-in pipeline uses (retrieval, the outcome registry, the slip
builder, team news) -- and the system prompt forbids any other source for
platform facts. Two further guards keep that honest rather than merely
requested:

* after the model answers, every percentage in its text is checked against the
  numbers the tools actually returned; one that matches nothing gets one
  correction round, and if it still doesn't match the answer carries a visible
  caution instead of passing as grounded;
* the sources and picks shown beside the answer are built from the tool results,
  never from what the model says it used.

It is opt-in and fails safe. Not enabled, no key, over a daily limit, a slow or
failed call, a refusal, or a run that exhausts its steps all return ``None``, and
the caller carries on with the built-in pipeline -- so the assistant can only
ever get better with this on, never go silent. Cost is bounded three ways: a
per-user and a whole-site daily count (read from the chat history, no new
table), a cap on tool-calling steps, and a wall-clock deadline.

Calls go through the official ``anthropic`` SDK. Opus-class models think
adaptively on their own, so no ``thinking`` setting is sent, and no sampling
parameters are (they are rejected); ``effort`` is the control.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app_settings, news_feed, team_news
from app.assistant import nlu, retrieval
from app.assistant.responder import Answer, PickRef, Source
from app.assistant.understanding import understand
from app.betcode.selection import select_legs_by_confidence
from app.db.models import ChatMessage, Match, Team
from app.outcomes.registry import outcomes_from_prediction
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

MAX_STEPS = 8  # model calls per question, the last one forbidden from using tools
DEADLINE_SECONDS = 80.0  # whole question; the hosting proxy gives up at ~100
MAX_TOKENS = 4096
HISTORY_WINDOW = dt.timedelta(minutes=60)
HISTORY_MESSAGES = 8
TOOL_RESULT_MAX_CHARS = 14_000
MAX_SLIP_LEGS = 30

# Models that accept ``output_config.effort`` (it errors on older ones).
_EFFORT_PREFIXES = (
    "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-4-6",
    "claude-sonnet-5", "claude-haiku-5", "claude-fable", "claude-mythos",
)
# Models for which a refused request can be re-run server-side on a fallback
# model in the same call (opt-in; see the module docstring of the claude-api
# reference). Dropped for the process if the API rejects it.
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_fallbacks_rejected = False

SYSTEM_PROMPT = """You are Guda, the football analyst inside Socca Intelligence, a platform that publishes AI match predictions and builds slips from them. You answer members' questions by looking things up with your tools and reasoning over what comes back.

How you work
- Your tools are the only source of platform facts: fixtures, predictions and market probabilities, team form, head-to-head, team news and lineups, the model's measured accuracy, and slips. Call as many as the question needs, in parallel when they are independent, and follow up when a result raises a new question.
- Every number, percentage, probability, score, date and team in your answer must come from a tool result in this conversation. Never state a platform fact from memory -- current form, injuries, fixtures, predictions -- and never estimate one. General football knowledge (rules, history, how a market works) is fine; say it is general.
- If a tool reports no data or no prediction, say so plainly. Do not fill the gap.
- The platform has no bookmaker odds. Slips are priced by the betting site when they are booked, so never quote or imply a price or a return.

Honesty about probabilities
- These are model estimates, not guarantees. Never call a pick safe, sure, certain or a banker, and give no staking advice. Say what the number is and, when it matters, what weakens it (thin data, low model agreement, a key player out).
- If someone describes losing money they can't afford, chasing losses, or a gambling problem, respond with care, do not encourage betting, and point them to responsible-gambling support.

Understanding the question
- People write in plain English with typos, slang and nicknames ("spurs", "man utd", "tmrw", "2nd half goals"). Work out what they mean. Use find_team when unsure of a name, and list_markets when unsure which market they mean.
- Resolve relative dates ("today", "tomorrow", "this weekend") from the date given in the message. Kickoff times are UTC.
- A follow-up ("and the second half?", "why?") refers to the conversation so far.
- If a question is truly ambiguous in a way that changes the answer, ask one short question. Otherwise make the sensible assumption and say which you made.

Answering well
- Answer the question that was asked, then stop; do not list everything you looked up. Reason: compare options, weigh a high probability against low data quality, notice when two markets disagree, and say which you would lean on and why.
- Be concise and conversational. Markdown is fine: bold the key pick, short lists. For a slip, list each leg (match, market and selection, probability), then the combined probability and the plain point that stacking legs multiplies the risk.
- Finish with exactly one line in this form, and nothing after it: `Follow-ups: <question> | <question> | <question>` (up to three short questions the member might ask next)."""


class ToolError(Exception):
    """A tool couldn't do what was asked; the message goes back to the model."""


# --- what one run learns, for the answer's sources and the numbers check -------


@dataclass
class Trace:
    db: Session
    now: dt.datetime
    teams: list[Team] = field(default_factory=list)
    matches: dict[int, str] = field(default_factory=dict)  # match id -> "Home vs Away"
    picks: list[PickRef] = field(default_factory=list)
    used_slip_builder: bool = False
    evidence: list[float] = field(default_factory=list)  # every number any tool returned
    tool_calls: int = 0

    def note_match(self, match_id: int, home: str, away: str) -> None:
        self.matches.setdefault(match_id, f"{home} vs {away}")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 4)
    return value


def _numbers(value: Any, out: list[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, out)
    elif isinstance(value, list):
        for v in value:
            _numbers(v, out)
    elif isinstance(value, str):
        # Numbers inside strings ("2-1", "Over 2.5 at 57%") are evidence too.
        out.extend(float(m) for m in re.findall(r"\d+(?:\.\d+)?", value))


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x * 100, 1)


def _day(raw: str | None, default: dt.date, field_name: str) -> dt.date:
    if not raw:
        return default
    try:
        return dt.date.fromisoformat(raw.strip()[:10])
    except ValueError as exc:
        raise ToolError(f"{field_name} must be a date like 2026-10-24, got {raw!r}") from exc


def _window(args: dict, trace: Trace, default_days: int = 7) -> tuple[dt.date, dt.date]:
    start = _day(args.get("date_from"), trace.now.date(), "date_from")
    end = _day(args.get("date_to"), start + dt.timedelta(days=default_days - 1), "date_to")
    if end < start:
        raise ToolError("date_to is before date_from")
    return start, end


def _bounds(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return dt.datetime.combine(start, dt.time.min), dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min)


def _all_teams(trace: Trace) -> list[Team]:
    if not trace.teams:
        trace.teams = list(trace.db.execute(select(Team)).scalars())
    return trace.teams


def _team(trace: Trace, text: str) -> Team:
    """The one team a name refers to: exact words first, then typos and
    nicknames repaired the way the built-in parser repairs them, then the
    closest spelling. A name that matches nothing is an error the model can read,
    never a guess."""

    text = (text or "").strip()
    if not text:
        raise ToolError("a team name is required")
    teams = _all_teams(trace)
    lowered = text.lower()
    repaired = understand(lowered, set(), nlu._name_tokens(teams)).text
    for candidate in dict.fromkeys((lowered, repaired)):
        found = nlu.resolve_teams(trace.db, candidate, limit=1, teams=teams)
        if found:
            return found[0]
    spellings = {t.name.lower(): t for t in teams}
    for t in teams:
        for alias in t.aliases or []:
            spellings.setdefault(alias.lower(), t)
    close = difflib.get_close_matches(repaired, list(spellings), n=1, cutoff=0.78)
    if close:
        return spellings[close[0]]
    raise ToolError(f"No team in the database matches {text!r}. Try find_team, or list_leagues to see what is covered.")


def _card_summary(trace: Trace, card: retrieval.MatchCard, *, detail: bool) -> dict:
    trace.note_match(card.match_id, card.home_team, card.away_team)
    out: dict[str, Any] = {
        "match_id": card.match_id, "league": card.league, "kickoff_utc": card.kickoff, "status": card.status,
        "home_team": card.home_team, "away_team": card.away_team,
    }
    if card.home_score is not None and card.away_score is not None:
        out["final_score"] = f"{card.home_score}-{card.away_score}"
    p = card.prediction
    out["has_prediction"] = p is not None
    if p is None:
        return out
    pred: dict[str, Any] = {
        "home_win_pct": _pct(p.home_win), "draw_pct": _pct(p.draw), "away_win_pct": _pct(p.away_win),
        "most_likely_outcome": f"{p.global_outcome_selection} ({p.global_outcome_market})",
        "most_likely_outcome_pct": _pct(p.global_outcome_probability),
        "confidence": p.confidence,
    }
    if detail:
        pred.update({
            "over_pct_by_goal_line": {line: _pct(v) for line, v in (p.over_probabilities or {}).items()},
            "btts_yes_pct": _pct(p.btts_yes), "btts_no_pct": _pct(p.btts_no),
            "most_likely_score": p.most_likely_score, "most_likely_score_pct": _pct(p.most_likely_score_probability),
            "data_quality_pct": _pct(p.data_quality_score), "model_agreement_pct": _pct(p.model_agreement_score),
            "reasons_for": (p.explanation or {}).get("positive", [])[:6],
            "reasons_against": (p.explanation or {}).get("negative", [])[:6],
        })
    out["prediction"] = pred
    return out


# --- tools -----------------------------------------------------------------------


def _t_find_team(args: dict, trace: Trace) -> Any:
    team = _team(trace, str(args.get("name", "")))
    return {"name": team.name, "league": team.league}


def _t_list_leagues(args: dict, trace: Trace) -> Any:
    return {"leagues": sorted({t.league for t in _all_teams(trace) if t.league})}


def _t_get_match(args: dict, trace: Trace) -> Any:
    if args.get("match_id") is not None:
        card = retrieval.get_match_card(trace.db, int(args["match_id"]))
        if card is None:
            raise ToolError(f"No match with id {args['match_id']}")
        return _card_summary(trace, card, detail=True)
    first = args.get("team") or args.get("home_team")
    second = args.get("away_team") or args.get("opponent")
    if not first:
        raise ToolError("give match_id, or a team (and optionally the opponent)")
    team_a = _team(trace, str(first))
    if second:
        team_b = _team(trace, str(second))
        card = retrieval.find_match_for_teams(trace.db, team_a, team_b, now=trace.now)
        if card is None:
            raise ToolError(f"No fixture between {team_a.name} and {team_b.name} in the imported schedule.")
    else:
        card = retrieval.next_fixture_for_team(trace.db, team_a, now=trace.now)
        if card is None:
            raise ToolError(f"No upcoming fixture for {team_a.name} in the imported schedule.")
    return _card_summary(trace, card, detail=True)


def _t_get_market_probabilities(args: dict, trace: Trace) -> Any:
    card = retrieval.get_match_card(trace.db, int(args["match_id"]))
    if card is None:
        raise ToolError(f"No match with id {args['match_id']}")
    trace.note_match(card.match_id, card.home_team, card.away_team)
    if card.prediction is None:
        raise ToolError("That match has no stored prediction yet, so no market is priced.")
    wanted = [str(m).strip().lower() for m in (args.get("markets") or []) if str(m).strip()]
    outcomes = outcomes_from_prediction(card.prediction)
    if not wanted:
        names = sorted({o.market for o in outcomes})
        return {"markets_available": names, "note": "Call again with `markets` set to the names you want."}
    # An exact market name wins over a substring: asking for "2H Total Goals 1.5"
    # must not also bring back "HT & 2H Total Goals 1.5", a different market.
    names = {o.market.lower() for o in outcomes}
    exact = {w for w in wanted if w in names}
    loose = [w for w in wanted if w not in exact]
    rows = []
    for o in outcomes:
        name = o.market.lower()
        if name in exact or any(w in name for w in loose):
            rows.append({"market": o.market, "selection": o.selection, "probability_pct": _pct(o.probability),
                         "meaning": o.definition})
    if not rows:
        raise ToolError(f"No priced market matches {args.get('markets')}. Call list_markets to see the names.")
    return {"match": f"{card.home_team} vs {card.away_team}", "outcomes": rows[:80],
            "truncated": len(rows) > 80}


def _t_list_markets(args: dict, trace: Trace) -> Any:
    from app.outcomes.registry import outcomes_from_prediction as expand  # same source of names as the Markets page
    from types import SimpleNamespace

    synthetic = SimpleNamespace(
        home_win=0.45, draw=0.28, away_win=0.27,
        over_probabilities={"0.5": 0.95, "1.5": 0.78, "2.5": 0.52, "3.5": 0.28, "4.5": 0.12},
        btts_yes=0.55, btts_no=0.45, correct_score_probabilities={"1-0": 0.11, "1-1": 0.09},
        data_quality_score=1.0,
        model_breakdown={"poisson": {"lambda_home": 1.6, "lambda_away": 1.1, "lambda_home_ht": 0.7,
                                    "lambda_away_ht": 0.5, "lambda_home_corners": 6.0, "lambda_away_corners": 4.5}},
    )
    names = sorted({o.market for o in expand(synthetic)})
    query = str(args.get("query", "")).strip().lower()
    if query:
        names = [n for n in names if query in n.lower()]
    return {"markets": names, "note": "Half-time markets are 'HT ...' (1st half) and '2H ...' (2nd half)."}


def _t_list_fixtures(args: dict, trace: Trace) -> Any:
    start, end = _window(args, trace, default_days=3)
    lo, hi = _bounds(start, end)
    limit = max(1, min(int(args.get("limit") or 15), 30))
    league = args.get("league")
    if league:
        league = _league(trace, str(league))
    cards = retrieval.fixtures_between(trace.db, lo, hi, league, limit=limit)
    if args.get("team"):
        team = _team(trace, str(args["team"]))
        cards = [c for c in cards if team.name in (c.home_team, c.away_team)]
    return {"window": f"{start} to {end}", "fixtures": [_card_summary(trace, c, detail=False) for c in cards]}


def _league(trace: Trace, text: str) -> str:
    wanted = nlu.resolve_league(trace.db, text.lower())
    if wanted:
        return wanted
    leagues = sorted({t.league for t in _all_teams(trace) if t.league})
    close = difflib.get_close_matches(text, leagues, n=1, cutoff=0.6)
    if close:
        return close[0]
    raise ToolError(f"Unknown league {text!r}. Covered: {', '.join(leagues[:12])}...")


def _t_best_picks(args: dict, trace: Trace) -> Any:
    start, end = _window(args, trace, default_days=3)
    lo, hi = _bounds(start, end)
    limit = max(1, min(int(args.get("limit") or 8), 15))
    league = _league(trace, str(args["league"])) if args.get("league") else None
    markets = tuple(str(m) for m in (args.get("markets") or []))
    side = args.get("side")
    if side not in (None, "Over", "Under"):
        raise ToolError("side must be 'Over' or 'Under'")
    if markets:
        hits = retrieval.best_outcomes(trace.db, "", lo, hi, league, limit=limit, names=markets, side=side)
        rows = [{"match_id": h.match_id, "match": f"{h.home_team} vs {h.away_team}", "league": h.league,
                 "kickoff_utc": h.kickoff, "market": h.market, "selection": h.selection,
                 "probability_pct": _pct(h.probability), "confidence": h.confidence} for h in hits]
        for h in hits:
            trace.note_match(h.match_id, h.home_team, h.away_team)
            trace.picks.append(PickRef(h.match_id, h.market, h.selection))
    else:
        cards = retrieval.ranked_predictions(trace.db, lo, hi, league, limit=limit)
        rows = []
        for c in cards:
            p = c.prediction
            if p is None:
                continue
            trace.note_match(c.match_id, c.home_team, c.away_team)
            trace.picks.append(PickRef(c.match_id, p.global_outcome_market, p.global_outcome_selection))
            rows.append({"match_id": c.match_id, "match": f"{c.home_team} vs {c.away_team}", "league": c.league,
                         "kickoff_utc": c.kickoff, "market": p.global_outcome_market,
                         "selection": p.global_outcome_selection, "probability_pct": _pct(p.global_outcome_probability),
                         "confidence": p.confidence, "data_quality_pct": _pct(p.data_quality_score)})
    return {"window": f"{start} to {end}", "picks": rows,
            "note": "Ranked by model probability alone; no bookmaker price is involved."}


def _t_build_slip(args: dict, trace: Trace) -> Any:
    legs = max(1, min(int(args.get("legs") or 5), MAX_SLIP_LEGS))
    floor = args.get("min_probability_pct")
    floor = 0.6 if floor is None else max(0.0, min(float(floor) / 100.0, 0.99))
    start = _day(args.get("date_from"), trace.now.date(), "date_from") if args.get("date_from") else None
    end = _day(args.get("date_to"), start, "date_to") if args.get("date_to") and start else None
    if start is None and args.get("date_to"):
        raise ToolError("date_to needs date_from")
    markets = tuple(str(m) for m in (args.get("markets") or []))
    league = _league(trace, str(args["league"])) if args.get("league") else None
    pick = select_legs_by_confidence(
        trace.db, min_probability=floor, max_legs=legs, markets=markets, league=league, days_ahead=7,
        date_from=start, date_to=end,
    )
    trace.used_slip_builder = True
    rows = []
    for leg in pick.legs:
        trace.note_match(leg.match_id, leg.home_team, leg.away_team)
        trace.picks.append(PickRef(leg.match_id, leg.market, leg.selection))
        rows.append({"match_id": leg.match_id, "match": f"{leg.home_team} vs {leg.away_team}", "league": leg.league,
                     "kickoff_utc": leg.kickoff, "market": leg.market, "selection": leg.selection,
                     "probability_pct": _pct(leg.model_probability)})
    return {"legs_requested": legs, "legs_found": len(rows), "min_probability_pct": _pct(floor), "legs": rows,
            "combined_probability_pct": _pct(pick.combined_probability), "warnings": pick.warnings,
            "note": "Not priced: the betting site prices the slip when it is booked."}


def _t_team_form(args: dict, trace: Trace) -> Any:
    team = _team(trace, str(args.get("team", "")))
    f = retrieval.team_form(trace.db, team, trace.now)
    return {"team": team.name, "league": team.league, "matches_played": f.matches_played,
            "points_per_game": round(f.points_per_game, 2), "goals_scored_avg": round(f.goals_scored_avg, 2),
            "goals_conceded_avg": round(f.goals_conceded_avg, 2), "clean_sheet_rate_pct": _pct(f.clean_sheet_rate),
            "home_goals_scored_avg": round(f.home_goals_scored_avg, 2), "away_goals_scored_avg": round(f.away_goals_scored_avg, 2),
            "recent_results_oldest_to_newest": f.recent_results, "rest_days": round(f.rest_days, 1)}


def _t_head_to_head(args: dict, trace: Trace) -> Any:
    a, b = _team(trace, str(args.get("team_a", ""))), _team(trace, str(args.get("team_b", "")))
    cards = retrieval.head_to_head(trace.db, a, b, limit=int(args.get("limit") or 8))
    meetings = []
    for c in cards:
        if c.home_score is None:
            continue
        meetings.append({"date": c.kickoff.date(), "home_team": c.home_team, "away_team": c.away_team,
                         "score": f"{c.home_score}-{c.away_score}", "league": c.league})
    return {"team_a": a.name, "team_b": b.name, "meetings_newest_first": meetings}


def _t_team_news(args: dict, trace: Trace) -> Any:
    match = trace.db.get(Match, int(args["match_id"]))
    if match is None:
        raise ToolError(f"No match with id {args['match_id']}")
    trace.note_match(match.id, match.home_team.name, match.away_team.name)
    return {"match": f"{match.home_team.name} vs {match.away_team.name}",
            "injuries_and_suspensions": team_news.injuries_for(trace.db, match, trace.now),
            "confirmed_lineups": team_news.lineups_for(trace.db, match, trace.now)}


def _t_model_accuracy(args: dict, trace: Trace) -> Any:
    snap = retrieval.accuracy_snapshot(trace.db)
    track = retrieval.track_record(trace.db)
    out: dict[str, Any] = {"backtest_recorded": snap.has_data}
    if snap.has_data:
        pref = snap.splits.get("test") or snap.splits.get("validation") or next(iter(snap.splits.values()))
        out["backtest"] = {"split": "test" if "test" in snap.splits else "validation", "model_version": snap.model_version,
                           "leagues": snap.leagues, "metrics": {k: (_pct(v) if k in ("accuracy",) else round(v, 4))
                                                                  for k, v in pref.items()},
                           "note": "accuracy is the 1X2 hit rate on a held-out, date-split test; brier/log_loss are lower-is-better"}
    out["live_track_record"] = {
        "graded_predictions": track.graded_predictions, "hits": track.hits,
        "by_confidence": [{"confidence": b.confidence, "graded": b.graded, "hit_rate_pct": _pct(b.hit_rate)} for b in track.by_confidence],
    } if track.has_data else None
    return out


def _t_football_headlines(args: dict, trace: Trace) -> Any:
    query = str(args.get("query", "")).strip().lower()
    items = news_feed.headlines(30)
    if query:
        items = [h for h in items if query in h.title.lower()]
    limit = max(1, min(int(args.get("limit") or 6), 12))
    return {"headlines": [{"title": h.title, "source": h.source, "published_utc": h.published_at, "link": h.link}
                          for h in items[:limit]],
            "note": "Headlines only; the article is at the link."}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


_DATE_NOTE = "ISO date, e.g. 2026-10-24 (inclusive)."
TOOLS: list[tuple[dict, Callable[[dict, Trace], Any]]] = [
    ({"name": "find_team", "description": "Resolve a team name, nickname or misspelling to the team as stored. Use when unsure how a team is named.",
      "input_schema": _schema({"name": {"type": "string"}}, ["name"])}, _t_find_team),
    ({"name": "list_leagues", "description": "The leagues this platform covers.", "input_schema": _schema({})}, _t_list_leagues),
    ({"name": "get_match", "description": "A fixture and the model's prediction for it: 1X2, goal lines, BTTS, likely score, confidence, data quality and the reasons for and against. Give match_id, or a team (its next fixture), or two teams (the fixture between them). Returns the match_id for the other tools.",
      "input_schema": _schema({"match_id": {"type": "integer"}, "team": {"type": "string"}, "opponent": {"type": "string"}})}, _t_get_match),
    ({"name": "get_market_probabilities", "description": "The model's probability for specific markets in one match, any of ~100: Total Goals lines, HT (1st half) and 2H (2nd half) markets, corners, handicaps, clean sheets, Odd/Even, combos. Call without `markets` to see which names exist for that match. `markets` matches names exactly or as a substring (e.g. '2H Total Goals', 'Corners').",
      "input_schema": _schema({"match_id": {"type": "integer"}, "markets": {"type": "array", "items": {"type": "string"}}}, ["match_id"])}, _t_get_market_probabilities),
    ({"name": "list_markets", "description": "Names of every market the platform prices, optionally filtered by a word.",
      "input_schema": _schema({"query": {"type": "string"}})}, _t_list_markets),
    ({"name": "list_fixtures", "description": "Upcoming fixtures in a date window (default today and the next 2 days), optionally for one league or team, each with its headline prediction.",
      "input_schema": _schema({"date_from": {"type": "string", "description": _DATE_NOTE}, "date_to": {"type": "string", "description": _DATE_NOTE}, "league": {"type": "string"}, "team": {"type": "string"}, "limit": {"type": "integer"}})}, _t_list_fixtures),
    ({"name": "best_picks", "description": "The highest-probability picks in a window (default the next 3 days). Without `markets`: each match's single most likely outcome across all markets. With `markets` (exact names, e.g. ['2H Total Goals 1.5']): the best selection in those markets per match; set `side` to Over or Under to restrict a goals line.",
      "input_schema": _schema({"markets": {"type": "array", "items": {"type": "string"}}, "side": {"type": "string", "enum": ["Over", "Under"]}, "league": {"type": "string"}, "date_from": {"type": "string", "description": _DATE_NOTE}, "date_to": {"type": "string", "description": _DATE_NOTE}, "limit": {"type": "integer"}})}, _t_best_picks),
    ({"name": "build_slip", "description": "Build a slip with the same engine as the AI Generation page: up to 30 legs, one per match, safest first, each at or above a minimum probability, optionally limited to markets, a league and dates (default the next 7 days). Returns the legs and their combined probability. Not priced.",
      "input_schema": _schema({"legs": {"type": "integer", "description": "1 to 30"}, "min_probability_pct": {"type": "number", "description": "e.g. 65 for 65%"}, "markets": {"type": "array", "items": {"type": "string"}}, "league": {"type": "string"}, "date_from": {"type": "string", "description": _DATE_NOTE}, "date_to": {"type": "string", "description": _DATE_NOTE}})}, _t_build_slip),
    ({"name": "team_form", "description": "A team's recent form: results, points per game, goals for and against, clean sheets.",
      "input_schema": _schema({"team": {"type": "string"}}, ["team"])}, _t_team_form),
    ({"name": "head_to_head", "description": "Past meetings between two teams, newest first, with scores.",
      "input_schema": _schema({"team_a": {"type": "string"}, "team_b": {"type": "string"}, "limit": {"type": "integer"}}, ["team_a", "team_b"])}, _t_head_to_head),
    ({"name": "team_news", "description": "Injuries, suspensions and (about an hour before kickoff) confirmed lineups for a match.",
      "input_schema": _schema({"match_id": {"type": "integer"}}, ["match_id"])}, _t_team_news),
    ({"name": "model_accuracy", "description": "How accurate the model has measurably been: the held-out backtest and the live track record of graded pre-match calls.",
      "input_schema": _schema({})}, _t_model_accuracy),
    ({"name": "football_headlines", "description": "Latest football news headlines from public feeds, optionally containing a word (a team name). Headlines only, with links.",
      "input_schema": _schema({"query": {"type": "string"}, "limit": {"type": "integer"}})}, _t_football_headlines),
]
TOOL_DEFS = [definition for definition, _ in sorted(TOOLS, key=lambda t: t[0]["name"])]  # stable order keeps the prompt cacheable
TOOL_FUNCS = {definition["name"]: fn for definition, fn in TOOLS}


def _fit(payload: Any) -> str:
    """The tool result as compact JSON, shortened (longest list first) if it
    would swamp the context."""

    text = json.dumps(payload, separators=(",", ":"), default=str)
    for _ in range(12):
        if len(text) <= TOOL_RESULT_MAX_CHARS:
            return text
        longest: list | None = None

        def walk(node: Any) -> None:
            nonlocal longest
            if isinstance(node, list):
                if len(node) > 3 and (longest is None or len(node) > len(longest)):
                    longest = node
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                for item in node.values():
                    walk(item)

        walk(payload)
        if longest is None:
            break
        del longest[max(3, len(longest) // 2):]
        if isinstance(payload, dict):
            payload["truncated"] = True
        text = json.dumps(payload, separators=(",", ":"), default=str)
    return text[:TOOL_RESULT_MAX_CHARS]


def _run_tool(name: str, args: dict, trace: Trace) -> tuple[str, bool]:
    """``(content, is_error)`` for one tool call; never raises."""

    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return f"Unknown tool {name!r}.", True
    trace.tool_calls += 1
    try:
        result = _jsonable(fn(args or {}, trace))
    except ToolError as exc:
        return str(exc), True
    except (KeyError, TypeError, ValueError) as exc:
        return f"Bad arguments for {name}: {exc}", True
    except Exception:  # noqa: BLE001 -- a broken tool must degrade to an error the model can read
        logger.exception("agent tool %s failed", name)
        return f"{name} failed unexpectedly; answer without it or try a different tool.", True
    _numbers(result, trace.evidence)
    return _fit(result), False


# --- the numbers check ---------------------------------------------------------------

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def unverified_percentages(text: str, evidence: list[float], extra: str = "") -> list[str]:
    """Percentages in ``text`` that match no number a tool returned (within
    rounding) and aren't in the user's own message. 0 and 100 are always fine,
    and so is the complement of a returned figure ("43% under" beside a 57% over)."""

    known = set(evidence) | {0.0, 100.0}
    known |= {100.0 - e for e in evidence if 0.0 <= e <= 100.0}
    known |= {float(m) for m in re.findall(r"\d+(?:\.\d+)?", extra)}
    bad = []
    for m in _PERCENT.finditer(text):
        value = float(m.group(1))
        if not any(abs(value - k) <= 0.51 for k in known):
            bad.append(m.group(0).replace(" ", ""))
    return list(dict.fromkeys(bad))


# --- the run -------------------------------------------------------------------------


def _settings(db: Session) -> dict:
    return app_settings.all_values(db)


def is_enabled(db: Session) -> bool:
    v = _settings(db)
    return bool(v.get("assistant_agent_enabled") and v.get("assistant_agent_api_key"))


def within_limits(db: Session, user_id: int, now: dt.datetime | None = None) -> bool:
    """Whether this user, and the site as a whole, still have smart-mode
    answers left today. Counted from the chat history itself (smart-mode answers
    are stored with intent "agent"), so there is nothing extra to keep in step."""

    v = _settings(db)
    since = (now or utcnow()) - dt.timedelta(hours=24)
    base = select(func.count(ChatMessage.id)).where(
        ChatMessage.role == "assistant", ChatMessage.intent == nlu.Intent.AGENT.value, ChatMessage.created_at >= since
    )
    if db.execute(base).scalar_one() >= int(v.get("assistant_agent_daily_total") or 0):
        return False
    return db.execute(base.where(ChatMessage.user_id == user_id)).scalar_one() < int(v.get("assistant_agent_daily_per_user") or 0)


def _history(db: Session, user_id: int, now: dt.datetime) -> list[dict]:
    rows = list(db.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id, ChatMessage.created_at >= now - HISTORY_WINDOW)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(HISTORY_MESSAGES)
    ).scalars())
    rows.reverse()
    turns: list[dict] = []
    for r in rows:
        text = (r.content or "").strip()
        if not text:
            continue
        # Consecutive same-role turns are merged by the API; keeping strict
        # alternation here keeps the history readable and valid.
        if turns and turns[-1]["role"] == r.role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": r.role, "content": text})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    if turns and turns[-1]["role"] == "user":
        turns.pop()  # a user turn with no answer: the new message replaces it
    return turns


def _client(values: dict) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=str(values["assistant_agent_api_key"]), timeout=float(values.get("assistant_agent_timeout") or 60.0),
        max_retries=1,
    )


def _create(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    global _fallbacks_rejected
    if str(kwargs["model"]).startswith(_FALLBACK_MODELS) and not _fallbacks_rejected:
        try:
            return client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
        except anthropic.BadRequestError as exc:
            # A deployment that doesn't accept the opt-in: drop it for good and go on without.
            logger.warning("server-side fallbacks rejected (%s); continuing without them", exc)
            _fallbacks_rejected = True
    return client.messages.create(**kwargs)


def _text_of(content: list) -> str:
    return "\n\n".join(b.text for b in content if getattr(b, "type", None) == "text" and b.text).strip()


def _split_followups(text: str) -> tuple[str, list[str]]:
    m = re.search(r"\n?\s*Follow-ups?:\s*(.+?)\s*$", text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return text.strip(), []
    questions = [q.strip(" -*`") for q in m.group(1).split("|")]
    return text[: m.start()].strip(), [q for q in questions if q][:3]


def run(
    db: Session,
    message: str,
    *,
    user_id: int,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
) -> Answer | None:
    """Smart mode's answer to ``message``, or ``None`` whenever the caller
    should use the built-in pipeline instead (see the module docstring)."""

    values = _settings(db)
    if not (values.get("assistant_agent_enabled") and values.get("assistant_agent_api_key")):
        return None
    now = now or utcnow()
    if not within_limits(db, user_id, now):
        return None

    model = str(values.get("assistant_agent_model") or "claude-opus-5-5")
    effort = str(values.get("assistant_agent_effort") or "medium")
    trace = Trace(db=db, now=now)
    client = _client(values)

    framing = f"[Today is {now.strftime('%A %d %B %Y')} (UTC).]"
    if context_match_id is not None:
        card = retrieval.get_match_card(db, context_match_id)
        if card is not None:
            trace.note_match(card.match_id, card.home_team, card.away_team)
            framing += (f" [The member has the match {card.home_team} vs {card.away_team} (match_id {card.match_id}) "
                        f"open on screen; 'this match' means it.]")
    messages: list[dict] = _history(db, user_id, now) + [{"role": "user", "content": f"{framing}\n\n{message}"}]

    base: dict[str, Any] = {
        "model": model, "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "tools": TOOL_DEFS,
    }
    if model.startswith(_EFFORT_PREFIXES):
        base["output_config"] = {"effort": effort}

    started = time.monotonic()
    corrected = False
    final_text = ""
    try:
        for step in range(MAX_STEPS):
            remaining = DEADLINE_SECONDS - (time.monotonic() - started)
            if remaining <= 5:
                logger.warning("smart mode ran out of time after %d steps", step)
                return None
            kwargs = {**base, "messages": messages, "timeout": remaining}
            if step == MAX_STEPS - 1:
                kwargs["tool_choice"] = {"type": "none"}  # out of steps: answer with what has been gathered
            response = _create(client, **kwargs)
            logger.info("smart mode step %d: stop=%s in=%s out=%s cache_read=%s", step + 1, response.stop_reason,
                        response.usage.input_tokens, response.usage.output_tokens,
                        getattr(response.usage, "cache_read_input_tokens", 0))

            if response.stop_reason == "refusal":
                logger.warning("smart mode declined by safeguards (%s)", getattr(response, "stop_details", None))
                return None
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                results = []
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        content, is_error = _run_tool(block.name, dict(block.input or {}), trace)
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": content,
                                        **({"is_error": True} if is_error else {})})
                messages.append({"role": "user", "content": results})  # all results in one message
                continue
            if response.stop_reason == "pause_turn":
                continue  # the model was paused mid-turn; resume it as is

            text = _text_of(response.content)
            if response.stop_reason not in ("end_turn", "max_tokens", "stop_sequence") or not text:
                return None
            bad = unverified_percentages(text, trace.evidence, message)
            if bad and not corrected and step < MAX_STEPS - 1:
                corrected = True
                messages.append({"role": "user", "content": (
                    f"Check before I show this: the figures {', '.join(bad)} don't appear in anything your tools "
                    "returned. Fix the answer using only retrieved figures -- look the number up with a tool, or "
                    "remove it -- and give the full corrected answer in the same format.")})
                continue
            if bad:
                text += "\n\n*Some figures above couldn't be matched to the data I looked up -- treat them with caution.*"
            final_text = text
            break
    except anthropic.APIError as exc:
        logger.warning("smart mode failed (%s: %s) request_id=%s", type(exc).__name__, exc,
                       getattr(exc, "request_id", None))
        return None
    except Exception:  # noqa: BLE001 -- smart mode must never take the chat down
        logger.exception("smart mode crashed")
        return None

    if not final_text:
        return None
    body, followups = _split_followups(final_text)
    sources = [Source("match", label, mid) for mid, label in list(trace.matches.items())[:5]]
    if trace.used_slip_builder:
        sources.append(Source("page", "AI Generation", "/app/betcodes"))
    return Answer(
        text=body, intent=nlu.Intent.AGENT, sources=sources, suggestions=followups,
        includes_probability=bool(_PERCENT.search(body)), picks=trace.picks[:MAX_SLIP_LEGS],
        rewritten=True,  # shown with the "AI-phrased" marker and counted against the AI quota
    )

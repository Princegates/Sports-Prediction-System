"""Intent classification and entity resolution for the chat assistant.

This is deliberately a rule-based parser rather than a model call. Two
reasons, in order of importance:

1. **Grounding.** Entity resolution here is a lookup against the ``teams``
   table, so the assistant can only ever talk about teams that exist in this
   database. A generative parser can invent "Manchester United vs Barcelona"
   as a fixture that was never played; this cannot.
2. **Cost.** It runs locally with no API call, which keeps the whole feature
   free to operate.

The trade-off is breadth: phrasings outside the patterns below fall through
to ``Intent.UNKNOWN``, which the responder turns into a capability menu
rather than a guess. That failure mode is a worse conversation but never a
wrong fact.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.team_matching import normalize_team_name
from app.db.models import Team

# Words that carry no entity signal but frequently sit adjacent to team
# names ("how will arsenal do against chelsea"), so they must not be treated
# as candidate team tokens during matching.
_STOPWORDS = {
    "a", "about", "against", "ahead", "all", "am", "an", "and", "any", "anything",
    "are", "as", "at", "back", "bad", "be", "because", "been", "bet", "bets",
    "better", "between", "bit", "both", "but", "by", "can", "chance", "chances",
    "come", "confidence", "could", "data", "day", "days", "did", "do", "does",
    "doing", "done", "dont", "down", "each", "explain", "far", "few", "find",
    "for", "form", "from", "get", "give", "go", "going", "good", "got", "great",
    "had", "happen", "has", "have", "he", "help", "her", "here", "hey", "hi",
    "him", "his", "how", "i", "if", "im", "in", "into", "is", "it", "its",
    "just", "know", "last", "less", "like", "likely", "long", "look", "lot",
    "make", "many", "match", "matches", "may", "me", "mean", "means", "might",
    "model", "models", "more", "most", "much", "my", "need", "next", "night",
    "no", "not", "now", "of", "off", "on", "one", "only", "or", "other", "our",
    "out", "over", "pick", "picks", "play", "played", "playing", "plays",
    "please", "predict", "predicted", "prediction", "predictions", "probability",
    "put", "really", "result", "results", "right", "risk", "risks", "same",
    "say", "score", "see", "should", "show", "side", "since", "so", "some",
    "something", "soon", "sure", "take", "team", "teams", "tell", "than",
    "thanks", "that", "the", "their", "them", "then", "there", "these", "they",
    "thing", "think", "this", "those", "time", "to", "today", "tomorrow", "too",
    "up", "us", "use", "very", "vs", "want", "was", "way", "we", "week",
    "weekend", "well", "what", "whats", "when", "where", "which", "who", "why",
    "will", "win", "with", "won", "work", "works", "would", "you", "your",
}

# Tokens shorter than this are ignored when matching a team name, so "as" or
# "fc" can't anchor a match on their own.
_MIN_TOKEN_LEN = 3


class Intent(str, Enum):
    GREETING = "greeting"
    HELP = "help"
    MATCH_PREDICTION = "match_prediction"
    TEAM_FORM = "team_form"
    HEAD_TO_HEAD = "head_to_head"
    COMPARE_TEAMS = "compare_teams"
    TODAYS_CARD = "todays_card"
    BEST_PICKS = "best_picks"
    ACCURACY = "accuracy"
    EXPLAIN_REASONING = "explain_reasoning"
    RISKS = "risks"
    CORRECT_SCORE = "correct_score"
    GENERATE_SELECTIONS = "generate_selections"
    LIVE_STATUS = "live_status"
    WHAT_CHANGED = "what_changed"
    METHODOLOGY = "methodology"
    RESPONSIBLE_USE = "responsible_use"
    UNKNOWN = "unknown"


@dataclass
class ParsedQuery:
    intent: Intent
    teams: list[Team] = field(default_factory=list)
    league: str | None = None
    date_from: dt.datetime | None = None
    date_to: dt.datetime | None = None
    market: str | None = None
    # The match the user is currently looking at in the UI, passed through
    # from the client so "why is this favored?" resolves without the user
    # having to re-name the teams.
    context_match_id: int | None = None
    # How many legs and what accuracy floor a GENERATE_SELECTIONS request
    # asked for ("give me 20 selections with at least 50% chance") -- None
    # when the number/threshold wasn't stated, which the responder fills
    # with the same defaults AI Generation itself uses.
    selection_count: int | None = None
    probability_floor: float | None = None
    raw: str = ""


# Ordered longest-phrase-first so "head to head" wins over "head".
_INTENT_PATTERNS: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.RESPONSIBLE_USE, ("gambling problem", "addicted", "addiction", "stop gambling",
                              "lost money", "losing money", "chasing losses", "guaranteed",
                              "sure thing", "sure bet", "fixed match", "can i get rich",
                              "life savings", "rent money", "borrow")),
    (Intent.ACCURACY, ("how accurate", "accuracy", "hit rate", "win rate", "track record",
                       "how good are you", "how reliable", "brier", "log loss", "calibrat",
                       "backtest", "how often are you right", "are you right")),
    (Intent.METHODOLOGY, ("how do you work", "how does it work", "how does the model work",
                          "how does this work", "what model", "which model", "model work",
                          "how are predictions made", "methodology", "how do you predict",
                          "what data", "where does the data", "elo", "poisson", "ensemble",
                          "algorithm", "machine learning", "how is this calculated")),
    (Intent.HEAD_TO_HEAD, ("head to head", "head-to-head", "h2h", "past meetings",
                           "previous meetings", "last time they", "when they played",
                           "history between", "record against")),
    (Intent.COMPARE_TEAMS, ("compare", "comparison", "who is better", "who's better",
                            "which team is better", "which team is stronger",
                            "who is stronger", "who's stronger", "how do they compare",
                            "better team", "side by side", "compare both teams")),
    (Intent.GENERATE_SELECTIONS, ("odds selection", "generate selections", "generate picks",
                                  "generate a slip", "build a slip", "build me a combo",
                                  "combo", "combination bet", "accumulator", "acca",
                                  "betting slip", "selections with", "selection with",
                                  "picks with", "legs with")),
    (Intent.BEST_PICKS, ("best pick", "best bet", "top pick", "top bet", "best selection",
                         "highest confidence", "high confidence", "safest", "strongest",
                         "most confident", "best value", "what should i back",
                         "banker", "best today", "best tip", "top tip")),
    (Intent.TODAYS_CARD, ("today", "tonight", "tomorrow", "this weekend", "what's on",
                          "whats on", "fixtures", "what games", "which games",
                          "upcoming", "schedule")),
    (Intent.WHAT_CHANGED, ("what changed", "what's changed", "whats changed",
                           "what just happened", "what happened live",
                           "why did it change", "why did that change",
                           "why did it move", "why did the probability change",
                           "why did the probability move")),
    (Intent.LIVE_STATUS, ("live", "in play", "in-play", "right now", "current score",
                          "what's the score", "whats the score")),
    (Intent.CORRECT_SCORE, ("correct score", "exact score", "final score", "scoreline",
                            "what will the score", "how many goals")),
    (Intent.RISKS, ("risk", "downside", "what could go wrong", "worry", "concern",
                    "danger", "upset", "against it", "why not")),
    (Intent.EXPLAIN_REASONING, ("why", "reason", "explain", "because", "what makes",
                                "justify", "how come", "factors", "rationale")),
    (Intent.TEAM_FORM, ("form", "how are they playing", "recent results", "streak",
                        "last 5", "last five", "in good shape", "playing well",
                        "how good is", "how strong is", "stats for")),
    (Intent.HELP, ("help", "what can you do", "what can you answer", "commands",
                   "capabilities", "how do i use")),
    (Intent.GREETING, ("hello", "hey there", "good morning", "good afternoon",
                       "good evening", "how are you")),
]

_GREETING_EXACT = {"hi", "hey", "yo", "hello", "sup", "hiya", "morning", "evening"}


def _needle_pattern(needle: str) -> re.Pattern[str]:
    r"""Compile a needle to a word-boundary-anchored regex.

    Plain substring matching is wrong here in a way that is easy to miss:
    ``"elo" in "what is the airspeed velocity of a swallow"`` is True, because
    "elo" sits inside "velocity" -- which routed an unanswerable question to
    the methodology handler. Short needles like "elo", "live" and "win" are
    exactly the ones that hide inside unrelated words, so every needle is
    matched on word boundaries instead.

    ``\b`` is only used where the needle's own edge is a word character, so a
    needle like "2.5" still matches.

    The trailing boundary tolerates an optional plural suffix, so a needle
    written in the singular ("best pick", "risk") still matches the way
    people actually type it ("best picks", "risks"). That keeps the pattern
    list from needing both forms of every noun, which is the kind of
    duplication that rots the moment someone adds a third.
    """

    escaped = re.escape(needle)
    prefix = r"\b" if needle[:1].isalnum() else ""
    suffix = r"(?:e?s)?\b" if needle[-1:].isalnum() else ""
    return re.compile(prefix + escaped + suffix)


_COMPILED_INTENTS: list[tuple[Intent, tuple[re.Pattern[str], ...]]] = []
_COMPILED_MARKETS: list[tuple[str, tuple[re.Pattern[str], ...]]] = []


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)

_MARKET_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("btts", ("btts", "both teams to score", "both score", "both to score")),
    ("over_under", ("over", "under", "total goals", "goal line", "2.5", "1.5", "3.5")),
    ("double_chance", ("double chance", "draw no bet", "dnb")),
    ("correct_score", ("correct score", "exact score", "scoreline")),
    ("1x2", ("who will win", "match result", "win", "draw", "1x2", "moneyline")),
]


_COMPILED_INTENTS = [
    (intent, tuple(_needle_pattern(n) for n in needles)) for intent, needles in _INTENT_PATTERNS
]
_COMPILED_MARKETS = [
    (market, tuple(_needle_pattern(n) for n in needles)) for market, needles in _MARKET_PATTERNS
]


def _detect_market(text: str) -> str | None:
    for market, patterns in _COMPILED_MARKETS:
        if _matches_any(text, patterns):
            return market
    return None


# Matches the number attached to a "%"/"percent" token -- checked first so
# it can be masked out before looking for the leg count, otherwise "20 ...
# 50% chance" would find "50" (from the percentage) as the count instead of
# "20". Capped to 3 digits; nothing meaningful here exceeds 100.
#
# The trailing \b sits only after "percent", not after "%": "%" is already a
# non-word character, so a \b right after it (with whitespace following)
# spans two non-word characters and never matches -- that would silently
# fail this pattern on the single most common phrasing, "50% chance".
_PERCENT_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b)")
# Any bare 1-3 digit integer left over once the percentage is masked out --
# the leg count, clamped below to MAX_SELECTION_COUNT so "give me 500
# selections" is read as "as many as you'll give me" rather than ignored.
# Capped at 3 digits so a 4-digit year is never mistaken for it.
_COUNT_PATTERN = re.compile(r"\b(\d{1,3})\b")

# Hard ceiling on how many legs a chat request can ask for -- generous
# enough for any real use, small enough that "give me 500 selections"
# doesn't try to walk the entire fixture list into one reply.
MAX_SELECTION_COUNT = 20


def _detect_selection_request(text: str) -> tuple[int | None, float | None]:
    """Pulls a leg count and an accuracy floor out of a selections request,
    e.g. "give me 20 odds selection with at least 50% chance" -> (20, 0.5).
    Either half is optional -- the responder falls back to AI Generation's
    own defaults when one is missing."""

    probability = None
    masked = text
    percent_match = _PERCENT_PATTERN.search(text)
    if percent_match:
        probability = max(0.0, min(1.0, float(percent_match.group(1)) / 100))
        masked = text[: percent_match.start()] + text[percent_match.end() :]

    count = None
    count_match = _COUNT_PATTERN.search(masked)
    if count_match:
        count = max(1, min(MAX_SELECTION_COUNT, int(count_match.group(1))))

    return count, probability


def _detect_dates(text: str, now: dt.datetime) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Resolve the relative date expressions people actually type. Anything
    absolute ("on March 3rd") is intentionally not parsed -- the responder
    tells the user to use the fixtures page for that, rather than guessing a
    year and quietly answering about the wrong match."""

    today = dt.datetime.combine(now.date(), dt.time.min)

    if "tomorrow" in text:
        return today + dt.timedelta(days=1), today + dt.timedelta(days=2)
    if "tonight" in text or "today" in text:
        return today, today + dt.timedelta(days=1)
    if "weekend" in text:
        # Saturday of the current football week through end of Sunday.
        days_to_saturday = (5 - today.weekday()) % 7
        saturday = today + dt.timedelta(days=days_to_saturday)
        return saturday, saturday + dt.timedelta(days=2)
    if "this week" in text or "next week" in text:
        offset = 7 if "next week" in text else 0
        return today + dt.timedelta(days=offset), today + dt.timedelta(days=offset + 7)
    return None, None


def _candidate_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if len(w) >= _MIN_TOKEN_LEN and w not in _STOPWORDS]


def resolve_teams(db: Session, text: str, limit: int = 2) -> list[Team]:
    """Find teams the user named, scored by how much of the team's own
    normalized name the message actually covers.

    Scoring rather than first-match matters for names that nest inside each
    other: "Manchester City" and "Manchester United" share a token, so a
    message saying "man city" must not resolve to United just because it was
    inserted first. A team only qualifies if every significant token of its
    name is present, which makes the shared-token case a tie that the longer,
    more specific name wins.
    """

    tokens = set(_candidate_tokens(text))
    if not tokens:
        return []

    lowered = text.lower()
    scored: list[tuple[float, int, Team]] = []

    for team in db.execute(select(Team)).scalars():
        normalized = normalize_team_name(team.name)
        name_tokens = [t for t in re.findall(r"[a-z0-9']+", normalized) if len(t) >= _MIN_TOKEN_LEN]
        if not name_tokens:
            # Nothing distinctive survived normalization (e.g. a club named
            # only "FC"); fall back to a whole-name substring check.
            if normalized and normalized in lowered:
                scored.append((1.0, len(normalized), team))
            continue

        matched = [t for t in name_tokens if t in tokens]
        if len(matched) != len(name_tokens):
            continue

        # Full phrase appearing verbatim is a stronger signal than the same
        # tokens scattered across the sentence.
        score = 1.0 + (0.5 if normalized in lowered else 0.0)
        scored.append((score, len(normalized), team))

    # Highest score first, then longest name (the more specific club).
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)

    # Drop teams whose name is a strict token-subset of an already-chosen
    # team, so "Manchester City" doesn't also drag in a hypothetical
    # "Manchester".
    chosen: list[Team] = []
    chosen_tokens: list[set[str]] = []
    for _, _, team in scored:
        team_tokens = set(re.findall(r"[a-z0-9']+", normalize_team_name(team.name)))
        if any(team_tokens < existing for existing in chosen_tokens):
            continue
        if any(team.id == c.id for c in chosen):
            continue
        chosen.append(team)
        chosen_tokens.append(team_tokens)
        if len(chosen) >= limit:
            break

    return chosen


def _classify(text: str, has_two_teams: bool, has_one_team: bool, has_context: bool) -> Intent:
    stripped = text.strip().strip("!?.").lower()
    if stripped in _GREETING_EXACT:
        return Intent.GREETING

    for intent, patterns in _COMPILED_INTENTS:
        if not _matches_any(text, patterns):
            continue

        # "why"/"risk"/"explain" are only answerable against a specific
        # match. Without one named or in context there is nothing to explain,
        # so treat it as a request for the best picks instead of answering
        # about a match the user never chose.
        if intent in (Intent.EXPLAIN_REASONING, Intent.RISKS, Intent.CORRECT_SCORE):
            if not (has_two_teams or has_context):
                return Intent.BEST_PICKS if intent != Intent.CORRECT_SCORE else Intent.TODAYS_CARD
        if intent == Intent.HEAD_TO_HEAD and not has_two_teams:
            return Intent.TEAM_FORM if has_one_team else Intent.HELP
        if intent == Intent.COMPARE_TEAMS and not has_two_teams:
            return Intent.TEAM_FORM if has_one_team else Intent.HELP
        # "What changed" without a match named or open is a question about
        # nothing in particular -- the closest real answer is just showing
        # what's live right now, same fallback LIVE_STATUS itself uses.
        if intent == Intent.WHAT_CHANGED and not (has_two_teams or has_context):
            return Intent.LIVE_STATUS
        if intent == Intent.TEAM_FORM and not has_one_team:
            return Intent.BEST_PICKS
        return intent

    # No keyword hit, but naming two teams is itself an unambiguous request
    # for that fixture's prediction -- "arsenal chelsea" is a real query.
    if has_two_teams:
        return Intent.MATCH_PREDICTION
    if has_one_team:
        return Intent.TEAM_FORM
    return Intent.UNKNOWN


def parse(
    db: Session,
    message: str,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
) -> ParsedQuery:
    now = now or dt.datetime.utcnow()
    text = message.lower().strip()

    teams = resolve_teams(db, text)
    date_from, date_to = _detect_dates(text, now)
    market = _detect_market(text)

    intent = _classify(
        text,
        has_two_teams=len(teams) >= 2,
        has_one_team=len(teams) >= 1,
        has_context=context_match_id is not None,
    )

    # Two named teams plus a market keyword is a fixture question, not a
    # generic market explainer.
    if intent in (Intent.TODAYS_CARD, Intent.LIVE_STATUS) and len(teams) >= 2:
        intent = Intent.MATCH_PREDICTION if intent == Intent.TODAYS_CARD else intent

    league = teams[0].league if teams else None

    selection_count = probability_floor = None
    if intent == Intent.GENERATE_SELECTIONS:
        selection_count, probability_floor = _detect_selection_request(text)

    return ParsedQuery(
        intent=intent,
        teams=teams,
        league=league,
        date_from=date_from,
        date_to=date_to,
        market=market,
        context_match_id=context_match_id,
        selection_count=selection_count,
        probability_floor=probability_floor,
        raw=message.strip(),
    )

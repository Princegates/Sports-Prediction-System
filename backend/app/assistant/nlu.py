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
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app.assistant.understanding import understand
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

# A named risk tier for a GENERATE_SELECTIONS request. Grouped separately
# from _INTENT_PATTERNS (rather than only living inside it) because these
# needles are also compiled into their own matcher, _detect_risk_level --
# the same phrase both selects the intent and reports which tier it named.
# Checked in this order so "higher risk" (a distinct phrase, not a suffix of
# "high risk") never gets shadowed by a broader synonym.
_RISK_LEVEL_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("higher", ("higher risk", "very high risk", "max risk", "maximum risk",
                "extreme risk", "highest risk")),
    ("high", ("high risk", "aggressive slip", "aggressive combo",
              "aggressive accumulator", "risky slip", "risky combo",
              "risky accumulator")),
    ("medium", ("medium risk", "moderate risk", "moderate slip", "balanced risk")),
    ("low", ("low risk", "safe slip", "safe combo", "safe accumulator",
             "safest slip", "conservative slip", "conservative combo")),
]
_RISK_LEVEL_NEEDLES: tuple[str, ...] = tuple(n for _, needles in _RISK_LEVEL_PATTERNS for n in needles)


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
    # A named risk tier ("give me a high risk accumulator") -- None when the
    # message didn't name one, in which case the responder's own default (or
    # the count/floor above, if those were stated instead) applies.
    risk_level: str | None = None
    # The specific registry markets the message named ("2nd half over 1.5" ->
    # "2H Total Goals 1.5"), when it named any -- see resolve_markets. These,
    # not the coarse ``market`` family above, are what the responder ranks and
    # reports on. ``selection_side`` is "Over"/"Under" when the message said so.
    market_names: tuple[str, ...] = ()
    market_label: str | None = None
    selection_side: str | None = None
    # What had to be assumed to read the message -- a typo fixed, a nickname
    # expanded (see understanding.understand). The answer shows these so a
    # wrong guess is visible rather than silently acted on.
    assumed: list[tuple[str, str]] = field(default_factory=list)
    raw: str = ""


# Ordered longest-phrase-first so "head to head" wins over "head".
_INTENT_PATTERNS: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.RESPONSIBLE_USE, ("gambling problem", "addicted", "addiction", "stop gambling",
                              "lost money", "losing money", "chasing losses", "guaranteed",
                              "sure thing", "sure bet", "fixed match", "can i get rich",
                              "life savings", "rent money", "borrow")),
    (Intent.ACCURACY, ("how accurate", "accuracy", "hit rate", "win rate", "track record",
                       "how good are you", "how reliable", "brier", "log loss", "calibrat",
                       "backtest", "how often are you right", "are you right", "can i trust",
                       "trust you", "trustworthy", "any good", "success rate", "how often do you win",
                       "how right")),
    (Intent.METHODOLOGY, ("how do you work", "how does it work", "how does the model work",
                          "how does this work", "what model", "which model", "model work",
                          "how are predictions made", "methodology", "how do you predict",
                          "what data", "where does the data", "elo", "poisson", "ensemble",
                          "algorithm", "machine learning", "how is this calculated", "how do you know",
                          "how do you decide", "how do you make", "where do you get")),
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
                                  "picks with", "legs with") + _RISK_LEVEL_NEEDLES),
    (Intent.BEST_PICKS, ("best pick", "best bet", "top pick", "top bet", "best selection",
                         "highest confidence", "high confidence", "safest", "strongest",
                         "most confident", "best value", "what should i back",
                         "banker", "best today", "best tip", "top tip", "what should i bet",
                         "who should i back", "any tips", "recommend", "recommendation", "suggest",
                         "what to bet", "what to back", "what do you like")),
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


@dataclass(frozen=True)
class MarketRequest:
    names: tuple[str, ...]  # exact outcome-registry market names
    label: str  # how the answer refers to them
    side: str | None = None  # "Over" / "Under" when the message said so


_FIRST_HALF = re.compile(r"\b(?:1st|first)\s+half\b|\bhalf[\s-]?time\b|\bhalftime\b|\bht\b|\b1h\b")
_SECOND_HALF = re.compile(r"\b(?:2nd|second)\s+half\b|\b2h\b")
_HT_FT = re.compile(r"\bht\s*/\s*ft\b|\bhtft\b|\bhalf[\s-]?time\s*(?:/|-|and)?\s*full[\s-]?time\b")
_LINE = re.compile(r"(?<![\d.])(\d{1,2}\.5)\b")

# Lines the registry actually prices, per scope -- a line outside these
# (a "7.5" first-half total) isn't a market we have, so it is dropped and
# the scope's defaults used instead of inventing a name that matches nothing.
_FULL_TIME_LINES = tuple(f"{n}.5" for n in range(10))
_HALF_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5")
_CORNER_LINES = ("7.5", "8.5", "9.5", "10.5", "11.5")
# The reference line for a bare "goals" question, and the two a half's
# goals are usually quoted on.
_DEFAULT_GOAL_LINES = {"": ("2.5",), "HT": ("0.5", "1.5"), "2H": ("0.5", "1.5")}
_HALF_NAME = {"": "", "HT": "1st half ", "2H": "2nd half "}


def resolve_markets(text: str) -> MarketRequest | None:
    """The specific markets a message asks about, as outcome-registry names:
    "2nd half over 1.5" is ``2H Total Goals 1.5`` (Over only), "first half
    btts" is ``HT Both Teams To Score``, "corners over 9.5" is
    ``Total Corners 9.5``.

    ``_detect_market``'s five coarse families can't say any of that -- they
    cannot tell a half from the full match or read a goal line, so "over 1.5"
    used to be answered with the 2.5 line. This is the finer-grained read of
    the same message; it returns ``None`` when nothing specific was named, and
    the caller falls back to the coarse family. Names are only ever produced
    from the fixed vocabulary above, so a request can't name a market the
    registry doesn't have.
    """

    if _HT_FT.search(text):
        return MarketRequest(("HT/FT",), "half-time/full-time")

    half = "HT" if _FIRST_HALF.search(text) else ("2H" if _SECOND_HALF.search(text) else "")
    prefix = f"{half} " if half else ""
    where = _HALF_NAME[half]
    names: list[str] = []
    labels: list[str] = []

    def add(market_names: tuple[str, ...], label: str) -> None:
        names.extend(n for n in market_names if n not in names)
        labels.append(label)

    if re.search(r"\bwin(?:s|ning)?\b", text) and re.search(r"\b(?:both|each) halves\b|\beither half\b", text):
        both = bool(re.search(r"\b(?:both|each) halves\b", text))
        kind = "Both Halves" if both else "Either Half"
        add((f"Home Team To Win {kind}", f"Away Team To Win {kind}"), f"to win {kind.lower()}")
    elif re.search(r"\bhighest[\s-]scoring half\b|\bhalf with (?:the )?most goals\b|\bwhich half\b.*\bgoals?\b|\bmost goals\b.*\bhalf\b", text):
        add(("Half With Most Goals",), "the half with most goals")
    elif re.search(r"\bcorners?\b", text):
        if re.search(r"\b(?:most|more) corners\b", text):
            add(("Most Corners",), "most corners")
        else:
            lines = tuple(l for l in dict.fromkeys(_LINE.findall(text)) if l in _CORNER_LINES) or ("9.5",)
            add(tuple(f"Total Corners {l}" for l in lines), f"total corners {'/'.join(lines)}")
    else:
        if re.search(r"\bclean[\s-]sheets?\b", text):
            add(("Home Clean Sheet", "Away Clean Sheet"), "clean sheet")
        if re.search(r"\bodd\s*(?:/|or|and)\s*even\b|\b(?:odd|even)\s+(?:number of\s+)?goals\b|\bgoals\s+(?:odd|even)\b", text):
            add((f"{prefix}Total Goals Odd/Even",), f"{where}odd/even goals")
        if re.search(r"\bbtts\b|\bboth teams? to score\b|\bboth (?:to )?score\b|\bgg\b|\bng\b", text):
            add((f"{prefix}Both Teams To Score",), f"{where}both teams to score")
        if re.search(r"\bdouble chance\b", text):
            add((f"{prefix}Double Chance",), f"{where}double chance")
        if re.search(r"\bdraw no bet\b|\bdnb\b", text) and not half:
            add(("Draw No Bet",), "draw no bet")
        if re.search(r"\bcorrect score\b|\bexact score\b|\bscoreline\b", text) and half:
            add((f"{half} Correct Score",), f"{where}correct score")
        if re.search(r"\bexact goals\b", text) and half:
            add((f"{half} Exact Goals",), f"{where}exact goals")
        if re.search(r"\bmulti[\s-]?goals\b", text) and half:
            add((f"{half} Multigoals",), f"{where}multigoals")

        side = "Over" if re.search(r"\bover\b", text) else None
        if re.search(r"\bunder\b", text):
            side = None if side else "Under"
        valid = _HALF_LINES if half else _FULL_TIME_LINES
        lines = tuple(l for l in dict.fromkeys(_LINE.findall(text)) if l in valid)
        wants_goals = bool(
            re.search(r"\bgoals?\b|\bover\s*/\s*under\b|\bo/u\b|\btotal\b", text)
            or lines
            or (side and half)
        )
        if wants_goals and not any("Odd/Even" in n for n in names):
            lines = lines or _DEFAULT_GOAL_LINES[half]
            add(tuple(f"{prefix}Total Goals {l}" for l in lines), f"{where}goals {'/'.join(lines)}")

        if half and not names:
            if re.search(r"\bresult\b|\b1x2\b|\bwin\b|\bwins\b|\bdraw\b|\bwinner\b", text):
                add((f"{prefix}Result",), f"{where}result")
            else:
                # "first half picks": the half's everyday markets.
                add((f"{prefix}Result", f"{prefix}Total Goals 0.5", f"{prefix}Total Goals 1.5",
                     f"{prefix}Both Teams To Score", f"{prefix}Double Chance"), f"{where}markets")

    if not names:
        return None
    side = None
    if any("Total Goals" in n or "Total Corners" in n for n in names):
        side = "Over" if re.search(r"\bover\b", text) else None
        if re.search(r"\bunder\b", text):
            side = None if side else "Under"
    return MarketRequest(tuple(names), " & ".join(labels), side)


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

# Hard ceiling on how many legs a chat request can ask for -- matches AI
# Generation's own accepted range (see BetCodeCriteriaIn.max_legs), small
# enough that "give me 500 selections" doesn't try to walk the entire
# fixture list into one reply.
MAX_SELECTION_COUNT = 30

_COMPILED_RISK_LEVELS: list[tuple[str, tuple[re.Pattern[str], ...]]] = [
    (level, tuple(_needle_pattern(n) for n in needles)) for level, needles in _RISK_LEVEL_PATTERNS
]


def _detect_risk_level(text: str) -> str | None:
    for level, patterns in _COMPILED_RISK_LEVELS:
        if _matches_any(text, patterns):
            return level
    return None


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


def resolve_teams(db: Session, text: str, limit: int = 2, teams: list[Team] | None = None) -> list[Team]:
    """Find teams the user named, scored by how much of the team's own
    normalized name the message actually covers.

    Scoring rather than first-match matters for names that nest inside each
    other: "Manchester City" and "Manchester United" share a token, so a
    message saying "man city" must not resolve to United just because it was
    inserted first. A team only qualifies if every significant token of its
    name is present, which makes the shared-token case a tie that the longer,
    more specific name wins.

    A team is also found by any of its stored aliases ("Spurs", "Man Utd"),
    each judged exactly like the name itself. ``teams`` lets a caller that
    already loaded the table share it rather than query it again.
    """

    tokens = set(_candidate_tokens(text))
    if not tokens:
        return []

    lowered = text.lower()
    scored: list[tuple[float, int, Team]] = []

    for team in teams if teams is not None else db.execute(select(Team)).scalars():
        best: tuple[float, int] | None = None
        for spelling in (team.name, *(team.aliases or [])):
            normalized = normalize_team_name(spelling)
            name_tokens = [t for t in re.findall(r"[a-z0-9']+", normalized) if len(t) >= _MIN_TOKEN_LEN]
            if not name_tokens:
                # Nothing distinctive survived normalization (e.g. a club named
                # only "FC"); fall back to a whole-name substring check.
                if normalized and normalized in lowered:
                    candidate = (1.0, len(normalized))
                    best = max(best, candidate) if best else candidate
                continue

            if any(t not in tokens for t in name_tokens):
                continue

            # Full phrase appearing verbatim is a stronger signal than the same
            # tokens scattered across the sentence.
            candidate = (1.0 + (0.5 if normalized in lowered else 0.0), len(normalized))
            best = max(best, candidate) if best else candidate
        if best is not None:
            scored.append((best[0], best[1], team))

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


def _fold_accents(text: str) -> str:
    """Strip accents so "Süper Lig" and "super lig" tokenize the same way --
    same technique as app.data.team_matching, applied here to league names
    rather than club names."""

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# League names in this project's data are consistently "<nationality/body>
# <competition>" ("English Premier League", "UEFA Champions League"), and
# people routinely drop that leading word in speech ("premier league",
# "champions league"). Treating it as optional -- rather than requiring
# every token in the stored name, the way resolve_teams does for club
# names -- is what makes those common phrasings resolve at all; the
# competition word(s) after it are still required, so "league" alone can't
# match anything.
_LEAGUE_LEADING_QUALIFIERS = {
    "english", "spanish", "german", "italian", "french", "dutch",
    "portuguese", "turkish", "uefa",
}


def resolve_league(db: Session, text: str) -> str | None:
    """Find a league the user named directly ("premier league picks",
    "la liga slip"), grounded against the real, distinct Team.league values
    this deployment actually has data for, never a hardcoded list that
    could claim a league with nothing behind it.
    """

    tokens = set(_candidate_tokens(text))
    if not tokens:
        return None

    leagues = {row[0] for row in db.execute(select(Team.league).distinct()) if row[0]}
    best: tuple[int, str] | None = None
    for league in leagues:
        name_tokens = [
            t for t in re.findall(r"[a-z0-9']+", _fold_accents(league.lower()))
            if len(t) >= _MIN_TOKEN_LEN
        ]
        required = name_tokens
        if len(name_tokens) > 1 and name_tokens[0] in _LEAGUE_LEADING_QUALIFIERS:
            required = name_tokens[1:]
        if not required or not all(t in tokens for t in required):
            continue
        # Prefer the more specific (more-token) match should more than one
        # league's tokens all be present at once.
        if best is None or len(name_tokens) > best[0]:
            best = (len(name_tokens), league)

    return best[1] if best else None


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
        return Intent.MATCH_PREDICTION if _FIXTURE_WORDS.search(text) else Intent.TEAM_FORM
    return Intent.UNKNOWN


_EXTRA_VOCABULARY = (
    "btts", "double", "chance", "corners", "correct", "clean", "sheet", "exact", "multigoals",
    "halftime", "accumulator", "selections", "prediction", "predictions", "probability",
    "bookmaker", "handicap", "against", "forecast",
)
_vocabulary_cache: set[str] | None = None


def _vocabulary() -> set[str]:
    """The words the assistant's own question patterns are built from -- what a
    misspelling is corrected toward."""

    global _vocabulary_cache
    if _vocabulary_cache is None:
        words: set[str] = set(_EXTRA_VOCABULARY) | _STOPWORDS
        for _, needles in [*_INTENT_PATTERNS, *_MARKET_PATTERNS]:
            for needle in needles:
                words.update(w for w in re.findall(r"[a-z]+", needle.lower()) if len(w) >= 3)
        _vocabulary_cache = words
    return _vocabulary_cache


def _name_tokens(teams: list[Team]) -> set[str]:
    """Every word of every team name, alias and league -- the other thing a
    misspelling can be corrected toward."""

    names: set[str] = set()
    for team in teams:
        for spelling in (team.name, *(team.aliases or []), team.league or ""):
            names.update(re.findall(r"[a-z0-9']+", _fold_accents(normalize_team_name(spelling))))
    return {n for n in names if len(n) >= _MIN_TOKEN_LEN}


# Words that make a message about one team a question about its next game
# ("will arsenal win", "arsenal tonight", "chelsea next match") rather than
# about its form.
_FIXTURE_WORDS = re.compile(
    r"\b(?:next|upcoming|play|plays|game|match|fixture|beat|beats|win|wins|lose|loses|score|scores|"
    r"chance|chances|tonight|today|tomorrow|predict|prediction|predictions|outlook|expect|forecast|"
    r"bet|bets|betting|tip|tips|back)\b"
)


def parse(
    db: Session,
    message: str,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
) -> ParsedQuery:
    now = now or utcnow()
    all_teams = list(db.execute(select(Team)).scalars())
    understood = understand(message.lower().strip(), _vocabulary(), _name_tokens(all_teams))
    text = understood.text

    teams = resolve_teams(db, text, teams=all_teams)
    date_from, date_to = _detect_dates(text, now)
    market = _detect_market(text)
    requested = resolve_markets(text)
    if requested is not None and market is None:
        # Any non-None family keeps the bare-market fallbacks below working;
        # the specific names, not this label, are what get ranked.
        market = "custom"

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
    # One team and a day ("arsenal tonight") is that team's next game too.
    if intent == Intent.TODAYS_CARD and len(teams) == 1:
        intent = Intent.MATCH_PREDICTION

    # A market keyword with nothing else -- no team, no other intent
    # matched -- is "BTTS today" / "over 2.5 tips" / "double chance picks",
    # not literally unanswerable. BEST_PICKS's own patterns require "best"
    # or "top" sitting right next to "pick"/"bet", which market vocabulary
    # like this doesn't satisfy, so this fallback only fires once every more
    # specific pattern already had its chance to match -- it can't steal a
    # message another intent's own needle already claimed.
    #
    # "1x2" is excluded: its own trigger words ("win", "draw") are single
    # common words rather than distinctive market vocabulary, so treating
    # them as a strong signal on their own would misfire on unrelated
    # messages like "did I win" -- honest UNKNOWN beats a confident but
    # irrelevant picks list there.
    # With a match open in the UI, a bare "what about 2nd half goals?" is a
    # question about that match -- unless the wording asks for a ranking.
    if (
        intent == Intent.UNKNOWN and requested is not None and not teams and context_match_id is not None
        and not re.search(r"\b(?:best|top|picks?|tips?|today|tomorrow)\b", text)
    ):
        intent = Intent.MATCH_PREDICTION
    if intent == Intent.UNKNOWN and market not in (None, "1x2") and not teams:
        intent = Intent.BEST_PICKS
    # "2nd half over 1.5 today" names a market and a day, not a request for the
    # day's full card: the market is the point, the day only narrows it.
    if intent == Intent.TODAYS_CARD and requested is not None and not teams:
        intent = Intent.BEST_PICKS

    # An explicitly named league ("premier league picks") wins over one
    # implied by a resolved team -- naming a team already implies its
    # league, so the two only ever disagree if the message contradicts
    # itself, and the explicit statement is the more deliberate one.
    league = resolve_league(db, text) or (teams[0].league if teams else None)

    selection_count = probability_floor = risk_level = None
    if intent == Intent.GENERATE_SELECTIONS:
        selection_count, probability_floor = _detect_selection_request(text)
        risk_level = _detect_risk_level(text)

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
        risk_level=risk_level,
        market_names=requested.names if requested else (),
        market_label=requested.label if requested else None,
        selection_side=requested.side if requested else None,
        assumed=understood.assumed,
        raw=message.strip(),
    )

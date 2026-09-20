"""Team-name normalization for entity resolution.

openfootball's own season files are not internally consistent about team
naming -- e.g. "Real Madrid" in one season's file and "Real Madrid CF" in
another. Without this, ingestion silently creates two separate ``Team`` rows
for the same real-world club, which fragments its Elo rating, form history,
and fixture list across two identities (a team page for one of them can look
like a smaller club with no upcoming fixtures, when the other identity has
them all).

This strips common club-suffix/legal-entity tokens so both spellings
normalize to the same key, without merging genuinely different clubs whose
names share a word (e.g. "Real Madrid" vs "Real Sociedad" still differ after
stripping "Real", because "Madrid" != "Sociedad").
"""

from __future__ import annotations

import re
import unicodedata

_STRIP_TOKENS = [
    "AFC", "CF", "FC", "CD", "UD", "SD", "AD", "RCD", "RC", "AC", "SC",
    "Club", "Balompié", "Deportivo", "Deportiva", "Real", "de",
]
_STRIP_PATTERN = re.compile(r"\b(" + "|".join(re.escape(t) for t in _STRIP_TOKENS) + r")\b", re.IGNORECASE)


def normalize_team_name(name: str) -> str:
    stripped = _STRIP_PATTERN.sub("", name)
    return re.sub(r"\s+", " ", stripped).strip().lower()


# Used only by the fuzzy cross-source scorer below (_tokens/name_match_score),
# never by normalize_team_name. "Real" is a genuine identity-bearing word for
# a Spanish club, not a legal-entity marker like "CF" or "Club" -- stripping
# it collapses "Real Madrid" down to the single token "madrid", which then
# scores a false perfect match against any other club that merely plays in
# Madrid too ("Atletico Madrid", "Rayo Vallecano de Madrid"). Every known
# "Real X" pair this project reconciles spells "Real" in both variants being
# compared (see test_known_alias_pairs_normalize_the_same), so
# normalize_team_name's own exact-key equivalence for those pairs is
# unaffected by keeping "Real" out of this stricter list; a source that
# drops "Real" entirely (bare "Sociedad") is reconciled via the explicit
# EXPLICIT_ALIASES entry for it instead, not this generic strip.
_FUZZY_STRIP_TOKENS = [t for t in _STRIP_TOKENS if t != "Real"]
_FUZZY_STRIP_PATTERN = re.compile(r"\b(" + "|".join(re.escape(t) for t in _FUZZY_STRIP_TOKENS) + r")\b", re.IGNORECASE)


def _fuzzy_normalize(name: str) -> str:
    stripped = _FUZZY_STRIP_PATTERN.sub("", name)
    return re.sub(r"\s+", " ", stripped).strip().lower()


# --- Cross-source name reconciliation ------------------------------------
#
# The same club is spelled differently by different providers, and the
# differences are mostly abbreviation rather than variation:
#
#     football-data.co.uk     openfootball
#     "Man United"            "Manchester United FC"
#     "Nott'm Forest"         "Nottingham Forest FC"
#     "Wolves"                "Wolverhampton Wanderers FC"
#     "Ath Madrid"            "Atlético Madrid"
#
# Exact and suffix-stripped matching both fail on every one of those. What
# does work is treating a token as matching when one is a prefix of the
# other ("man" ~ "manchester", "wolv" ~ "wolverhampton"), which is precisely
# the shape abbreviation takes. Scoring rather than accept/reject lets the
# caller pick the best candidate among a day's fixtures and reject weak ones,
# so an unrecognised club is skipped rather than attached to the wrong match.

_MIN_PREFIX_LEN = 3


def _strip_accents(text: str) -> str:
    """Fold accented characters to ASCII, so "Atlético" and "Atletico" are
    the same club. Decomposing and dropping combining marks handles every
    accent rather than a hand-maintained character list."""

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _tokens(name: str) -> list[str]:
    normalized = _fuzzy_normalize(_strip_accents(name.lower()))
    return [t for t in re.findall(r"[a-z0-9]+", normalized) if len(t) >= 2]


# Token pairs that satisfy the prefix-abbreviation shape below (one is a
# literal prefix of the other, long enough to clear _MIN_PREFIX_LEN) but are
# not an abbreviation of each other at all. "Milan" (AC Milan's own identity,
# reachable via EXPLICIT_ALIASES) and "Milano" (the Italian spelling
# embedded in Internazionale's full legal name, "FC Internazionale Milano")
# are the same word in two languages for the same city, not a shortened/full
# pair for the same club -- exactly the "Munich"/"Munchen" exonym problem
# this module already calls out in EXPLICIT_ALIASES, just producing a false
# match instead of a missed one. Add here, not to EXPLICIT_ALIASES: an alias
# entry would need to know every spelling of Inter's full name in advance,
# while this blocks the collision for any of them at the token level.
_FALSE_COGNATE_PAIRS: frozenset[frozenset[str]] = frozenset({frozenset({"milan", "milano"})})


def _tokens_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if frozenset({a, b}) in _FALSE_COGNATE_PAIRS:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # An abbreviation must still be distinctive: "st" should not match
    # "stoke" and "sunderland" both.
    return len(shorter) >= _MIN_PREFIX_LEN and longer.startswith(shorter)


# City names shared by more than one real club in this project's data, none
# of which is itself a club identity. A match built ENTIRELY from tokens in
# this set is not evidence the two names denote the same club -- "Real
# Madrid" and "Atletico Madrid" both contain "madrid", and "FC Barcelona"
# and "RCD Espanyol de Barcelona" both contain "barcelona", but sharing a
# home city is not sharing an identity. A club genuinely resolved by one of
# these words alone (an exact spelling match, or a real EXPLICIT_ALIASES
# entry) is unaffected -- this only blocks the coincidental, partial-token
# route to the same wrong conclusion that name_match_score's own docstring
# describes. See scripts/merge_duplicate_teams.py, whose dry run against
# this project's real data proposed merging exactly these pairs (plus
# Inter into AC Milan) before this guard existed.
_CITY_ONLY_TOKENS = frozenset({"madrid", "milan", "milano", "barcelona"})


# Clubs whose short form is not a prefix of their full name, so the scoring
# below cannot reach them. Empirically found by testing real spelling pairs:
# "Wolves" is not a prefix of "Wolverhampton", "Ath" is not a prefix of
# "Atletico", "SG" is an initialism. Keyed by the abbreviation, lowercased
# and punctuation-stripped.
#
# This list is deliberately explicit rather than fuzzy. The alternative --
# lowering the match threshold until these pass -- also lets "Man United"
# match "Manchester City", and attaching one club's statistics to another is
# far worse than having none.
EXPLICIT_ALIASES: dict[str, str] = {
    # England
    "wolves": "wolverhampton wanderers",
    "nottm forest": "nottingham forest",
    "sheffield weds": "sheffield wednesday",
    "west brom": "west bromwich albion",
    "qpr": "queens park rangers",
    "peterboro": "peterborough united",
    # Spain
    "ath madrid": "atletico madrid",
    "ath bilbao": "athletic bilbao",
    "sp gijon": "sporting gijon",
    "vallecano": "rayo vallecano",
    "celta": "celta vigo",
    "espanol": "espanyol",
    "sociedad": "real sociedad",
    # Germany
    "ein frankfurt": "eintracht frankfurt",
    "fc koln": "koln",
    "leverkusen": "bayer leverkusen",
    "mgladbach": "borussia monchengladbach",
    "dortmund": "borussia dortmund",
    "hertha": "hertha berlin",
    "stuttgart": "vfb stuttgart",
    "wolfsburg": "vfl wolfsburg",
    # Italy
    "inter": "internazionale",
    "verona": "hellas verona",
    # Exonyms. API-Football uses the English name for several European clubs
    # while the domestic feeds use the local one, and no amount of token
    # matching gets from "Munich" to "Munchen" -- they are different words for
    # the same city, not different spellings.
    "bayern munich": "bayern munchen",
    "borussia monchengladbach": "borussia monchengladbach",
    "cologne": "fc koln",
    "fc cologne": "fc koln",
    "eintracht frankfurt": "eintracht frankfurt",
    "inter milan": "internazionale",
    "ac milan": "milan",
    "sporting cp": "sporting lisbon",
    "fc porto": "porto",
    "sl benfica": "benfica",
    "psv eindhoven": "psv",
    "red bull salzburg": "salzburg",
    "olympiakos piraeus": "olympiakos",
    # France
    "paris sg": "paris saint germain",
    "st etienne": "saint etienne",
    "clermont": "clermont foot",
}


def canonical_alias(name: str) -> str:
    """Expand a known abbreviation to the fuller spelling, if we have one.

    Applied before scoring, so the scorer only ever sees names that differ by
    ordinary suffix or prefix abbreviation.
    """

    key = re.sub(r"[^a-z0-9 ]", "", _strip_accents(name.lower())).strip()
    key = re.sub(r"\s+", " ", key)
    return EXPLICIT_ALIASES.get(key, name)


def name_match_score(a: str, b: str) -> tuple[float, float]:
    """How much of each name the other accounts for, shorter name first.

    Two numbers rather than one, because the first cannot tell two clubs
    apart on its own. "Leeds" leaves "united" over in "Leeds United" and is
    still Leeds -- coverage below 1.0 is normal for a genuine abbreviation,
    not a rejection signal. It is a tie-break for ranking candidates, not a
    threshold: see ``TeamIndex._ranked`` in app.data.api_football_ingest for
    where that ranking actually happens.

    One case coverage cannot fix on its own: a name that reduces to nothing
    but a shared city -- "FC Barcelona" and "RCD Espanyol de Barcelona" both
    contain "barcelona", two entirely different, rival clubs. Coverage does
    separate them (0.5, since "espanyol" is left over) -- but a legitimate
    abbreviation pair can land at exactly the same 0.5 (see
    test_team_matching.py), so no fixed coverage threshold reliably tells
    the two apart. ``_CITY_ONLY_TOKENS`` handles this case directly: when
    the *shorter* name's tokens are entirely a shared-city word and
    something in the longer name is left unaccounted for, this is refused
    outright (0.0, 0.0) rather than scored as a plausible partial match --
    sharing a home city is not sharing an identity.
    """

    ta, tb = _tokens(canonical_alias(a)), _tokens(canonical_alias(b))
    if not ta or not tb:
        return 0.0, 0.0

    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    remaining = list(longer)
    matched = 0
    for token in shorter:
        for i, candidate in enumerate(remaining):
            if _tokens_match(token, candidate):
                matched += 1
                remaining.pop(i)
                break

    if matched < len(longer) and set(shorter) <= _CITY_ONLY_TOKENS:
        return 0.0, 0.0

    return matched / len(shorter), matched / len(longer)


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity between two spellings of a club name.

    1.0 means every token of the less specific name is accounted for in the
    other, which is what a correct abbreviation looks like. Callers should
    require a high score -- these names are short, so a partial match is
    usually a different club rather than a sloppy spelling of the same one --
    and should rank equal scores with ``name_match_score``, since this number
    alone cannot distinguish two clubs that share their only surviving token.
    """

    return name_match_score(a, b)[0]

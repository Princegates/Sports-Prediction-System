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
    normalized = normalize_team_name(_strip_accents(name.lower()))
    return [t for t in re.findall(r"[a-z0-9]+", normalized) if len(t) >= 2]


def _tokens_match(a: str, b: str) -> bool:
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # An abbreviation must still be distinctive: "st" should not match
    # "stoke" and "sunderland" both.
    return len(shorter) >= _MIN_PREFIX_LEN and longer.startswith(shorter)


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


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity between two spellings of a club name.

    1.0 means every token of the less specific name is accounted for in the
    other, which is what a correct abbreviation looks like. Callers should
    require a high score -- these names are short, so a partial match is
    usually a different club rather than a sloppy spelling of the same one.
    """

    ta, tb = _tokens(canonical_alias(a)), _tokens(canonical_alias(b))
    if not ta or not tb:
        return 0.0

    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    remaining = list(longer)
    matched = 0
    for token in shorter:
        for i, candidate in enumerate(remaining):
            if _tokens_match(token, candidate):
                matched += 1
                remaining.pop(i)
                break

    return matched / len(shorter)

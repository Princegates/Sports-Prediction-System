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

_STRIP_TOKENS = [
    "AFC", "CF", "FC", "CD", "UD", "SD", "AD", "RCD", "RC", "AC", "SC",
    "Club", "Balompié", "Deportivo", "Deportiva", "Real", "de",
]
_STRIP_PATTERN = re.compile(r"\b(" + "|".join(re.escape(t) for t in _STRIP_TOKENS) + r")\b", re.IGNORECASE)


def normalize_team_name(name: str) -> str:
    stripped = _STRIP_PATTERN.sub("", name)
    return re.sub(r"\s+", " ", stripped).strip().lower()

"""Plain-English tolerance for the chat assistant: read what people meant, not
only what they typed.

``nlu`` matches on exact words, which is what keeps the assistant grounded --
it can only ever resolve a team that exists and a market the registry has.
The cost is brittleness: "arsnal vs chelse tonite", "wats on tmrw" and "man
utd gonna win?" all fall through to the capability menu. This module sits in
front of ``nlu`` and repairs the message instead:

* slang and chat shorthand are spelled out ("tonite", "wats", "gonna", "o2.5");
* nicknames become the club's name ("spurs", "man utd", "barca");
* a misspelt word is corrected to the closest word the assistant actually
  knows -- a team, a league, or the vocabulary of its own question patterns.

Corrections are conservative on purpose, and *shown*: the answer says what it
assumed ("I read 'arsnal' as 'arsenal'"), so a wrong guess is visible and
cheap to fix rather than silently answering about the wrong team. A word is
only ever corrected *into* a known word, never dropped or invented, and a word
that is already known (any team token, any pattern word, any stopword) is left
alone, so ordinary English is not rewritten into football vocabulary.

``did_you_mean`` is the last resort for a message that still matches nothing:
the closest canonical questions, offered rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches

# Chat shorthand -> plain words. Whole-token only. Not shown to the user as a
# "correction": spelling out "u" as "you" is not an assumption worth flagging.
_SLANG = {
    "wat": "what", "wats": "whats", "wht": "what", "whts": "whats", "hw": "how", "wen": "when",
    "tmrw": "tomorrow", "tmr": "tomorrow", "tmrrw": "tomorrow", "2moro": "tomorrow",
    "2morrow": "tomorrow", "tomoro": "tomorrow", "tonite": "tonight", "tnite": "tonight",
    "2nite": "tonight", "2day": "today", "todai": "today",
    "pls": "please", "plz": "please", "thx": "thanks", "ty": "thanks",
    "u": "you", "ur": "your", "r": "are", "abt": "about", "bcos": "because", "cuz": "because",
    "gonna": "going to", "wanna": "want to", "gotta": "got to", "lemme": "let me",
    "gimme": "give me", "haf": "half", "hlf": "half", "scor": "score", "dunno": "do not know", "ppl": "people", "msg": "message",
    "prob": "probability", "probs": "probability",
}

# Nicknames and common short forms -> the name the database stores them
# under. Matching a nickname that is not in the database harmlessly resolves to
# nothing; the expansion only helps where the club exists.
_NICKNAMES = {
    "spurs": "tottenham hotspur", "tottenham": "tottenham hotspur",
    "man utd": "manchester united", "man u": "manchester united", "man united": "manchester united",
    "man city": "manchester city",
    "gunners": "arsenal", "the gunners": "arsenal",
    "barca": "barcelona", "atleti": "atletico madrid", "atletico": "atletico madrid",
    "wolves": "wolverhampton wanderers", "villa": "aston villa",
    "juve": "juventus", "bayern": "bayern munich", "psg": "paris saint-germain",
    "newcastle": "newcastle united",
    "leicester": "leicester city", "west ham": "west ham united", "brighton": "brighton and hove albion",
    "dortmund": "borussia dortmund", "gladbach": "borussia monchengladbach",
    "leverkusen": "bayer leverkusen", "leipzig": "rb leipzig",
}

# Words that are never "corrected" even though they are not in the pattern
# vocabulary: ordinary question words and the day/time vocabulary.
_COMMON = {
    "tonight", "today", "tomorrow", "weekend", "yesterday", "morning", "evening", "afternoon",
    "week", "month", "later", "soon", "next", "upcoming", "game", "games", "fixture", "fixtures",
    "gonna", "going", "tell", "about", "think", "thought", "guess", "reckon", "would", "should",
    "could", "might", "maybe", "probably", "definitely", "really", "actually", "tonite",
    "bet", "bets", "betting", "stake", "odds", "tip", "tips", "tipster", "banker", "safe",
    "risky", "easy", "hard", "tough", "strong", "weak", "good", "bad", "great", "best", "worst",
    "first", "second", "half", "halves", "goal", "goals", "corner", "corners", "card", "cards",
    "home", "away", "draw", "beat", "beats", "lose", "loses", "lost", "score", "scores", "scored",
    "underdog", "favourite", "favorite", "league", "cup", "table", "position", "points",
}

_WORD = re.compile(r"[a-z0-9']+")


@dataclass
class Understood:
    text: str  # the repaired message -- what nlu should parse
    # (what they typed, what it was read as) for each *assumption* made -- a
    # typo fixed or a nickname expanded -- so the answer can show its work.
    assumed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def note(self) -> str | None:
        if not self.assumed:
            return None
        said = "; ".join(f"'{a}' as '{b}'" for a, b in self.assumed)
        return f"*I read {said}.*"


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _expand_shorthand(text: str) -> str:
    text = re.sub(r"\bo\s?(\d{1,2}\.5)\b", r"over \1", text)
    text = re.sub(r"\bu\s?(\d{1,2}\.5)\b", r"under \1", text)
    text = re.sub(r"\b(over|under)(\d{1,2}\.5)\b", r"\1 \2", text)
    text = re.sub(r"\b(\d)(st|nd)(half)\b", r"\1\2 half", text)
    text = re.sub(r"\bhalf\s?time\s?full\s?time\b", "ht/ft", text)
    return text


def _expand_nicknames(text: str) -> tuple[str, list[tuple[str, str]]]:
    assumed: list[tuple[str, str]] = []
    # Longest first so "man utd" is read before "man".
    for nick in sorted(_NICKNAMES, key=len, reverse=True):
        full = _NICKNAMES[nick]
        if full in text or not re.search(rf"\b{re.escape(nick)}\b", text):
            continue
        # "tottenham" -> "tottenham hotspur" must not double the name when the
        # club's full name was typed.
        text = re.sub(rf"\b{re.escape(nick)}\b", full, text)
        if nick != full.split()[0]:
            assumed.append((nick, full))
    return text, assumed


def understand(text: str, vocabulary: set[str], names: set[str]) -> Understood:
    """Repair ``text`` (already lower-cased) against what the assistant knows.

    ``vocabulary`` is the words its own question patterns use; ``names`` the
    tokens of every team and league in the database. Both are passed in rather
    than imported so this stays free of ``nlu`` (which imports it) and
    testable on its own.
    """

    text = _expand_shorthand(text)
    text, assumed = _expand_nicknames(text)

    known = vocabulary | names | _COMMON
    candidates = sorted({w for w in known if len(w) >= 4})
    out: list[str] = []
    for token in re.split(r"(\s+)", text):
        word = token.strip()
        if not word or not _WORD.fullmatch(word):
            out.append(token)
            continue
        if word in _SLANG:
            out.append(token.replace(word, _SLANG[word]))
            continue
        if len(word) < 4 or word in known or _stem(word) in known or word.isdigit() or any(c.isdigit() for c in word):
            out.append(token)
            continue
        cutoff = 0.88 if len(word) == 4 else 0.82
        close = [c for c in get_close_matches(word, candidates, n=3, cutoff=cutoff) if c[0] == word[0]]
        if close:
            out.append(token.replace(word, close[0]))
            assumed.append((word, close[0]))
        else:
            out.append(token)
    return Understood("".join(out), assumed)


# Questions the assistant can answer, phrased the way a person would ask them
# -- what "did you mean" offers for a message that matched nothing.
EXAMPLE_QUESTIONS = [
    "What are the best picks today?",
    "What's on today?",
    "What fixtures are on this weekend?",
    "Best BTTS picks",
    "Best double chance picks",
    "Best 2nd half goals picks",
    "Best over 1.5 goals picks",
    "Best corners picks",
    "Give me 10 selections with at least 60% chance",
    "Build me a low risk accumulator",
    "Build me a high risk accumulator",
    "What's live right now?",
    "How accurate is the model?",
    "How does the model work?",
    "What can you do?",
    "Is it safe to bet on football?",
]
_FILLER = {
    "what", "whats", "the", "a", "an", "is", "are", "of", "to", "me", "my", "you", "i", "it", "do",
    "does", "can", "how", "give", "show", "tell", "please", "for", "on", "in", "at", "and", "with",
}


def _content(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _FILLER and len(w) > 1]


def did_you_mean(text: str, limit: int = 3) -> list[str]:
    """The closest canonical questions to ``text``, best first -- empty when
    nothing is meaningfully close (offering a random question is worse than
    offering none)."""

    mine = _content(text)
    if not mine:
        return []
    scored: list[tuple[float, str]] = []
    for example in EXAMPLE_QUESTIONS:
        theirs = _content(example)
        hits = sum(
            1 for w in mine if any(w == t or SequenceMatcher(None, w, t).ratio() >= 0.8 for t in theirs)
        )
        if not hits:
            continue
        # Share of what they said that matched, and of what the question has.
        score = (hits / len(mine) + hits / len(theirs)) / 2
        scored.append((score, example))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [example for score, example in scored if score >= 0.3][:limit]

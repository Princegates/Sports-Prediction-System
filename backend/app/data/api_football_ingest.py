"""Bringing API-Football data into this project's own tables.

Two jobs, both spending a strictly limited request budget.

**European fixtures.** openfootball has no current Champions League season, so
those matches cannot be predicted at all today. This fills that gap.

**Odds.** Stored against the match so the track record can eventually answer
"did we beat the price", not only "were we right".

The interesting problem is team identity. A Team row is unique on (name,
league), which suits self-contained domestic leagues and breaks immediately on
a competition drawing clubs from five of them: Real Madrid would exist twice,
once in La Liga with a real Elo history and once in the Champions League
starting from nothing. Predictions for the European tie would then be built on
thirteen matches of history while two hundred sat in the next row.

So a European fixture resolves to the *domestic* team rows wherever it can,
and skips the tie when it cannot. A skipped fixture is visibly missing; a
fixture silently attached to a duplicate club is a prediction that looks
normal and is nonsense.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers.api_football import ApiFootballClient, ID_TO_LEAGUE
from app.data.team_matching import canonical_alias, name_match_score
from app.db.models import Match, MatchOdds, Team

logger = logging.getLogger(__name__)

# How close a provider's club name must be to one already in the database
# before they are treated as the same club. The importer would rather skip a
# tie than attach it to the wrong team.
_NAME_THRESHOLD = 0.82


@dataclass
class UnresolvedClub:
    """A provider club name that matched nothing, and what it came closest to.

    The nearest stored club is the whole point. Without it every skip looks
    the same, and the two causes need opposite responses: a club playing in a
    league this project does not hold is *correctly* skipped and will never
    resolve, while a club we do hold under a different spelling is a one-line
    alias away from working. The score separates them -- a near miss is a
    spelling problem, a distant one is an absent league.
    """

    name: str
    fixtures: int = 0
    nearest: str | None = None
    score: float = 0.0


@dataclass
class ImportReport:
    considered: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_unresolved: list[str] = field(default_factory=list)
    unresolved_clubs: dict[str, UnresolvedClub] = field(default_factory=dict)
    requests_used: int = 0

    @property
    def resolved_rate(self) -> float:
        total = self.considered or 1
        return (self.inserted + self.updated) / total

    def note_unresolved(self, name: str, nearest: str | None, score: float) -> None:
        entry = self.unresolved_clubs.get(name)
        if entry is None:
            entry = UnresolvedClub(name=name, nearest=nearest, score=score)
            self.unresolved_clubs[name] = entry
        entry.fixtures += 1


class TeamIndex:
    """Every known club, searchable across leagues.

    Loaded once. The alternative -- a lookup per fixture -- is the pattern that
    has already cost this project a month of database bandwidth.
    """

    def __init__(self, db: Session) -> None:
        self._teams = list(db.execute(select(Team)).scalars())
        self._by_canonical: dict[str, Team] = {}
        for team in self._teams:
            self._by_canonical.setdefault(canonical_alias(team.name), team)

    def nearest(self, provider_name: str) -> tuple[Team | None, float]:
        """The closest stored club to a provider spelling, and how close.

        Returned even when it is too far to use: a rejected candidate with its
        score is what tells a human whether a skip needs an alias or is simply
        a league this project does not hold.
        """

        candidates = self._ranked(provider_name)
        if not candidates:
            return None, 0.0
        score, _, team = candidates[0]
        return team, score

    def _ranked(self, provider_name: str) -> list[tuple[float, float, Team]]:
        """Every stored club scored against this spelling, best first.

        Ranked on both halves of the score. Ranking on the first alone left
        the winner to whichever row the loop happened to reach first, and two
        clubs tie there routinely: "Atletico Madrid" scores 1.0 against both
        Atletico and Real Madrid, so which club got the Champions League tie
        came down to primary-key order in the teams table.
        """

        canonical = canonical_alias(provider_name)
        exact = self._by_canonical.get(canonical)
        if exact is not None:
            return [(1.0, 1.0, exact)]

        scored = []
        for team in self._teams:
            primary, coverage = name_match_score(provider_name, team.name)
            if primary:
                scored.append((primary, coverage, team))
        scored.sort(key=lambda row: (-row[0], -row[1], row[2].name))
        return scored

    def resolve(self, provider_name: str) -> Team | None:
        """Find the domestic club row for a name as the provider spells it.

        Refuses an ambiguous match. When two different clubs score identically
        there is no evidence for either, and picking one anyway is how a tie
        ends up in the wrong club's history -- the failure this whole module
        exists to avoid. Better to skip it and say so.
        """

        candidates = self._ranked(provider_name)
        if not candidates or candidates[0][0] < _NAME_THRESHOLD:
            return None

        primary, coverage, team = candidates[0]
        tied = [c for c in candidates[1:] if (c[0], c[1]) == (primary, coverage) and c[2].id != team.id]
        if tied:
            logger.warning(
                "Ambiguous club name %r: %s all score %.2f/%.2f -- skipping rather than guessing",
                provider_name,
                ", ".join([team.name] + [t[2].name for t in tied]),
                primary,
                coverage,
            )
            return None
        return team


def _parse_kickoff(raw: str) -> dt.datetime:
    # The provider sends ISO 8601 with an offset; everything stored here is
    # naive UTC, so convert rather than truncate.
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def import_fixtures(
    db: Session,
    client: ApiFootballClient,
    *,
    league_id: int,
    season: int,
    from_date: dt.date | None = None,
    to_date: dt.date | None = None,
) -> ImportReport:
    """Import one competition's fixtures, reusing existing club rows."""

    report = ImportReport()
    league_name = ID_TO_LEAGUE.get(league_id, f"League {league_id}")

    rows = client.fixtures(league_id=league_id, season=season, from_date=from_date, to_date=to_date)
    report.requests_used = client.quota.used_this_run

    index = TeamIndex(db)

    # One query for everything already stored for this competition, rather than
    # a lookup per fixture.
    existing = {
        (m.home_team_id, m.away_team_id, m.date): m
        for m in db.execute(select(Match).where(Match.league == league_name)).scalars()
    }

    for row in rows:
        report.considered += 1
        fixture = row.get("fixture") or {}
        teams = row.get("teams") or {}
        goals = row.get("goals") or {}

        home_name = (teams.get("home") or {}).get("name") or ""
        away_name = (teams.get("away") or {}).get("name") or ""
        home = index.resolve(home_name)
        away = index.resolve(away_name)

        if home is None or away is None:
            missing = [n for n, t in ((home_name, home), (away_name, away)) if t is None]
            report.skipped_unresolved.append(
                f"{home_name} vs {away_name} (no match for {', '.join(repr(n) for n in missing)})"
            )
            for name in missing:
                candidate, score = index.nearest(name)
                report.note_unresolved(name, candidate.name if candidate else None, score)
            continue

        kickoff = _parse_kickoff(fixture.get("date") or "")
        status_short = ((fixture.get("status") or {}).get("short") or "").upper()
        finished = status_short in {"FT", "AET", "PEN"}

        key = (home.id, away.id, kickoff)
        match = existing.get(key)
        if match is None:
            match = Match(
                league=league_name,
                season=str(season),
                date=kickoff,
                home_team_id=home.id,
                away_team_id=away.id,
                status="FINISHED" if finished else "SCHEDULED",
                home_score=goals.get("home") if finished else None,
                away_score=goals.get("away") if finished else None,
            )
            db.add(match)
            report.inserted += 1
        elif finished and match.home_score is None:
            match.home_score = goals.get("home")
            match.away_score = goals.get("away")
            match.status = "FINISHED"
            report.updated += 1

    db.commit()
    return report


# Only the three-way result market is stored for now. Over/under and BTTS
# prices exist too, but every extra market is more of a budget that is already
# only a hundred requests a day.
_RESULT_MARKET_NAMES = {"match winner", "1x2", "full time result"}
_SELECTION_MAP = {"home": "Home Win", "draw": "Draw", "away": "Away Win"}


def import_odds(
    db: Session,
    client: ApiFootballClient,
    *,
    league_id: int,
    season: int,
    date: dt.date | None = None,
) -> ImportReport:
    """Capture three-way prices for fixtures already in the database.

    Matches the provider's fixture to ours by kickoff and clubs. A price that
    cannot be tied to a stored match is dropped rather than stored loose --
    odds with no match are not data, they are a future join that will go wrong.
    """

    report = ImportReport()
    league_name = ID_TO_LEAGUE.get(league_id, f"League {league_id}")

    rows = client.odds(league_id=league_id, season=season, date=date)
    report.requests_used = client.quota.used_this_run

    index = TeamIndex(db)
    stored = {
        (m.home_team_id, m.away_team_id, m.date): m
        for m in db.execute(select(Match).where(Match.league == league_name)).scalars()
    }
    captured_at = dt.datetime.utcnow()

    for row in rows:
        report.considered += 1
        fixture = row.get("fixture") or {}
        teams = row.get("teams") or {}
        home = index.resolve((teams.get("home") or {}).get("name") or "")
        away = index.resolve((teams.get("away") or {}).get("name") or "")
        if home is None or away is None:
            continue

        match = stored.get((home.id, away.id, _parse_kickoff(fixture.get("date") or "")))
        if match is None:
            continue

        for bookmaker in row.get("bookmakers") or []:
            book_name = bookmaker.get("name") or "unknown"
            for bet in bookmaker.get("bets") or []:
                if (bet.get("name") or "").strip().lower() not in _RESULT_MARKET_NAMES:
                    continue
                for value in bet.get("values") or []:
                    selection = _SELECTION_MAP.get((value.get("value") or "").strip().lower())
                    if selection is None:
                        continue
                    try:
                        price = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    db.add(
                        MatchOdds(
                            match_id=match.id,
                            captured_at=captured_at,
                            bookmaker=book_name,
                            market="Match Result",
                            selection=selection,
                            decimal_odds=price,
                            source_fixture_id=fixture.get("id"),
                        )
                    )
                    report.inserted += 1

    db.commit()
    return report

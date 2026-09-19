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
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers.api_football import ApiFootballClient, ID_TO_LEAGUE
from app.data.team_matching import canonical_alias, name_match_score
from app.db.models import LivePrediction, Match, MatchOdds, Team
from app.live_engine import record_live_event
from app.prediction_models.elo import EUROPEAN_COMPETITIONS

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
    # A provider kickoff that didn't match ours exactly, reconciled onto an
    # existing SCHEDULED fixture between the same two clubs instead of
    # inserted as a new one -- see import_fixtures for why an exact-timestamp
    # key alone produced duplicate rows every time a broadcaster moved a
    # kickoff.
    rescheduled: int = 0
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
        self.db = db
        self._teams = list(db.execute(select(Team)).scalars())
        self._by_canonical: dict[str, Team] = {}
        for team in self._teams:
            self._by_canonical.setdefault(canonical_alias(team.name), team)

    def create(self, name: str, league: str) -> Team:
        """Add a new club, for a domestic league importing for the first time.

        Never used for a European competition -- there, an unresolved name
        must stay unresolved (see the module docstring), or it silently
        starts a second history for a club that already has one.
        """

        team = Team(name=name, league=league, aliases=[])
        self.db.add(team)
        self.db.flush()
        self._teams.append(team)
        self._by_canonical.setdefault(canonical_alias(name), team)
        return team

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
    """Import one competition's fixtures.

    A European competition draws clubs from domestic leagues already in the
    database and must resolve to those exact rows -- see the module
    docstring for why a mismatch is worse than a skip. A plain domestic
    league has no rows to resolve to on its first import, so unresolved
    clubs are created instead, the same as the free providers in
    app/data/ingest.py do.

    A domestic league can already hold this fixture under a different
    kickoff -- broadcasters move matches by hours or days after a schedule
    is first published, routinely. Matching on an exact timestamp treated
    every one of those as a brand-new fixture, silently duplicating the
    entire remaining calendar the first time this ran against a league whose
    matches came from somewhere else first. A still-SCHEDULED match between
    the same two clubs, close in time, is now reconciled onto instead --
    never a FINISHED one, whose date is a fact of history rather than
    something still moving.
    """

    report = ImportReport()
    league_name = ID_TO_LEAGUE.get(league_id, f"League {league_id}")
    domestic = league_name not in EUROPEAN_COMPETITIONS

    rows = client.fixtures(league_id=league_id, season=season, from_date=from_date, to_date=to_date)
    report.requests_used = client.quota.used_this_run

    index = TeamIndex(db)

    # One query for everything already stored for this competition, rather than
    # a lookup per fixture.
    stored = list(db.execute(select(Match).where(Match.league == league_name)).scalars())
    existing = {(m.home_team_id, m.away_team_id, m.date): m for m in stored}

    # A same-direction rematch between two clubs inside one league season
    # does not happen in a standard round-robin format, so at most one
    # SCHEDULED fixture should ever be waiting per (home, away) pair -- this
    # index exists purely to survive a moved kickoff, not to disambiguate a
    # real rematch.
    scheduled_by_pair: dict[tuple[int, int], list[Match]] = {}
    for m in stored:
        if m.status == "SCHEDULED":
            scheduled_by_pair.setdefault((m.home_team_id, m.away_team_id), []).append(m)

    for row in rows:
        report.considered += 1
        fixture = row.get("fixture") or {}
        teams = row.get("teams") or {}
        goals = row.get("goals") or {}

        home_name = (teams.get("home") or {}).get("name") or ""
        away_name = (teams.get("away") or {}).get("name") or ""
        home = index.resolve(home_name)
        away = index.resolve(away_name)

        if domestic:
            if home is None:
                home = index.create(home_name, league_name)
            if away is None:
                away = index.create(away_name, league_name)

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
        api_fixture_id = fixture.get("id")

        key = (home.id, away.id, kickoff)
        match = existing.get(key)

        if match is None:
            # Exact timestamp missed. Reconcile onto a SCHEDULED fixture
            # between the same two clubs within a broadcaster-reschedule
            # window rather than assume this is a genuinely new match.
            RESCHEDULE_WINDOW = dt.timedelta(days=7)
            pending = scheduled_by_pair.get((home.id, away.id)) or []
            for candidate in pending:
                if abs(candidate.date - kickoff) <= RESCHEDULE_WINDOW:
                    match = candidate
                    pending.remove(candidate)  # claimed; a later provider row must not also snap onto it
                    break

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
                api_fixture_id=api_fixture_id,
            )
            db.add(match)
            report.inserted += 1
            if not finished:
                scheduled_by_pair.setdefault((home.id, away.id), []).append(match)
            continue

        if match.api_fixture_id != api_fixture_id:
            # Backfills a row imported before this column existed, and
            # keeps a reconciled (rescheduled-onto) row pointed at the
            # provider fixture that most recently claimed it.
            match.api_fixture_id = api_fixture_id
        if match.date != kickoff:
            match.date = kickoff
            report.rescheduled += 1
        if finished and match.home_score is None:
            match.home_score = goals.get("home")
            match.away_score = goals.get("away")
            match.status = "FINISHED"
            report.updated += 1

    db.commit()
    return report


# Three markets, named and valued to match app.outcomes.registry exactly --
# "Match Result" / "Home Win", "Both Teams To Score" / "Yes", "Total Goals
# 2.5" / "Over 2.5". That is deliberate: a booking-code leg is built by
# joining a model outcome to a stored price on (market, selection), and if
# the two sides ever spelled the same market differently that join would
# silently return nothing rather than fail loudly.
#
# Provider bet-name matching is unverified against a live response -- this
# sandbox cannot reach api-sports.io -- and is built from the documented
# name/value conventions of the "Match Winner", "Both Teams Score" and
# "Goals Over/Under" bets. The Match Result path above it has run against
# real data; this has not. Confirm the bet names an actual response uses
# before relying on BTTS/Over-Under prices for anything.
_RESULT_MARKET_NAMES = {"match winner", "1x2", "full time result"}
_RESULT_SELECTIONS = {"home": "Home Win", "draw": "Draw", "away": "Away Win"}
_SELECTION_MAP = _RESULT_SELECTIONS  # kept for anything still importing the old name

_BTTS_MARKET_NAMES = {"both teams score", "both teams to score"}
_BTTS_SELECTIONS = {"yes": "Yes", "no": "No"}

_GOALS_MARKET_NAMES = {"goals over/under"}
_GOALS_LINE_PATTERN = re.compile(r"^(over|under)\s+([\d.]+)$", re.IGNORECASE)


def _parse_bet(bet_name: str, value_text: str) -> tuple[str, str] | None:
    """One bookmaker value -> (our market name, our selection name), or None
    for a bet this project does not price. Isolated in one place so a fourth
    market is one function to extend, not a third copy of this loop."""

    name = (bet_name or "").strip().lower()
    value = (value_text or "").strip()

    if name in _RESULT_MARKET_NAMES:
        selection = _RESULT_SELECTIONS.get(value.lower())
        return ("Match Result", selection) if selection else None

    if name in _BTTS_MARKET_NAMES:
        selection = _BTTS_SELECTIONS.get(value.lower())
        return ("Both Teams To Score", selection) if selection else None

    if name in _GOALS_MARKET_NAMES:
        match = _GOALS_LINE_PATTERN.match(value)
        if not match:
            return None
        direction, line = match.group(1).capitalize(), match.group(2)
        return (f"Total Goals {line}", f"{direction} {line}")

    return None


def import_odds(
    db: Session,
    client: ApiFootballClient,
    *,
    league_id: int,
    season: int,
    date: dt.date | None = None,
) -> ImportReport:
    """Capture three-way prices for fixtures already in the database.

    Matches the provider's fixture to ours by API-Football's own fixture id
    -- not by kickoff and club names. The real ``/odds`` response carries no
    ``teams`` block at all, only ``fixture.id``; team-name matching here was
    an unverified assumption that turned out wrong, and silently dropped
    every single row regardless of how correctly the clubs themselves had
    resolved. A price whose fixture id isn't one this project has stored
    (from a prior ``import_fixtures`` run against the same league) is
    dropped rather than stored loose -- odds with no match are not data,
    they are a future join that will go wrong.
    """

    report = ImportReport()
    league_name = ID_TO_LEAGUE.get(league_id, f"League {league_id}")

    rows = client.odds(league_id=league_id, season=season, date=date)
    report.requests_used = client.quota.used_this_run

    stored = {
        m.api_fixture_id: m
        for m in db.execute(
            select(Match).where(Match.league == league_name, Match.api_fixture_id.is_not(None))
        ).scalars()
    }
    captured_at = dt.datetime.utcnow()

    for row in rows:
        report.considered += 1
        fixture = row.get("fixture") or {}

        match = stored.get(fixture.get("id"))
        if match is None:
            continue

        for bookmaker in row.get("bookmakers") or []:
            book_name = bookmaker.get("name") or "unknown"
            for bet in bookmaker.get("bets") or []:
                for value in bet.get("values") or []:
                    parsed = _parse_bet(bet.get("name") or "", value.get("value") or "")
                    if parsed is None:
                        continue
                    market, selection = parsed
                    try:
                        price = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    db.add(
                        MatchOdds(
                            match_id=match.id,
                            captured_at=captured_at,
                            bookmaker=book_name,
                            market=market,
                            selection=selection,
                            decimal_odds=price,
                            source_fixture_id=fixture.get("id"),
                        )
                    )
                    report.inserted += 1

    db.commit()
    return report


@dataclass
class LiveSyncReport:
    considered: int = 0
    updated: int = 0
    unchanged: int = 0
    finished: int = 0
    skipped_no_match: list[str] = field(default_factory=list)
    requests_used: int = 0


# Fixture states that mean "not actually being played right now", so no
# in-play projection is meaningful. FT/AET/PEN are handled separately, as a
# transition to record rather than a state to skip.
_NON_PLAYING_STATUSES = {"PST", "CANC", "ABD", "AWD", "WO", "TBD", "SUSP", "INT"}


def sync_live_matches(db: Session, client: ApiFootballClient) -> LiveSyncReport:
    """Pull every fixture API-Football currently has in play, worldwide, in
    one request, and reconcile it onto our own Match rows.

    ``live_fixtures()`` is not scoped to a league or season -- it is the
    entire world's live board, and most of it is a club this project holds no
    row for. ``TeamIndex.resolve`` filters that down for free, the same as an
    unresolved club is skipped everywhere else in this module: never created
    from a live probe, only matched against what a real fixture import
    already established.

    A poll that finds nothing changed since the last one recorded writes
    nothing -- this runs every few minutes while matches are on, and a
    ``LivePrediction`` row per poll per live match would mostly be the same
    minute and score repeated.
    """

    report = LiveSyncReport()
    rows = client.live_fixtures()
    report.requests_used = client.quota.used_this_run

    index = TeamIndex(db)

    for row in rows:
        report.considered += 1
        fixture = row.get("fixture") or {}
        league = row.get("league") or {}
        teams = row.get("teams") or {}
        goals = row.get("goals") or {}
        status = fixture.get("status") or {}

        league_name = ID_TO_LEAGUE.get(league.get("id"))
        if league_name is None:
            continue  # a live match in a league this project doesn't hold

        home_name = (teams.get("home") or {}).get("name") or ""
        away_name = (teams.get("away") or {}).get("name") or ""
        home = index.resolve(home_name)
        away = index.resolve(away_name)
        if home is None or away is None:
            continue

        candidates = list(
            db.execute(
                select(Match).where(
                    Match.league == league_name,
                    Match.home_team_id == home.id,
                    Match.away_team_id == away.id,
                    Match.status.in_(["SCHEDULED", "LIVE"]),
                )
            ).scalars()
        )
        if not candidates:
            report.skipped_no_match.append(f"{home_name} vs {away_name} ({league_name})")
            continue

        kickoff = _parse_kickoff(fixture.get("date") or "")
        match = min(candidates, key=lambda m: abs(m.date - kickoff))

        status_short = (status.get("short") or "").upper()

        if status_short in {"FT", "AET", "PEN"}:
            if match.status != "FINISHED":
                match.status = "FINISHED"
                match.home_score = goals.get("home")
                match.away_score = goals.get("away")
                report.finished += 1
            continue

        if status_short in _NON_PLAYING_STATUSES:
            continue

        latest = db.execute(
            select(LivePrediction)
            .where(LivePrediction.match_id == match.id)
            .order_by(LivePrediction.created_at.desc())
        ).scalars().first()

        elapsed = status.get("elapsed")
        minute = elapsed if isinstance(elapsed, int) else (latest.minute if latest else 0)
        score_home = goals.get("home")
        score_home = score_home if score_home is not None else (latest.score_home if latest else 0)
        score_away = goals.get("away")
        score_away = score_away if score_away is not None else (latest.score_away if latest else 0)

        if latest is not None and (latest.minute, latest.score_home, latest.score_away) == (minute, score_home, score_away):
            report.unchanged += 1
            continue

        record_live_event(db, match, minute=minute, score_home=score_home, score_away=score_away, trigger_event="sync")
        # record_live_event guesses FINISHED from minute >= 90, which is wrong
        # for stoppage time in a match that is, per the provider, still on.
        # The status check above is the authority on FT; short of that, this
        # poll only ever means the match is live.
        match.status = "LIVE"
        db.commit()
        report.updated += 1

    db.commit()
    return report

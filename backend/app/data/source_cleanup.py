"""Retiring what the free fixture feeds left behind, now that API-Football
is the only source.

For a while two feeds wrote the same leagues: openfootball's free season
files and API-Football. They spell clubs differently ("Sporting Clube de
Braga" / "Sporting Braga", "RCD Espanyol de Barcelona" / "Espanyol"), and
openfootball's kickoff times are local time stored as if they were UTC --
so the same real match was stored twice, under two club rows, one to two
hours apart, and the Markets page listed both. Worse for the model, a
club's history was split across the two rows.

``retire_free_fixtures`` runs per league, after that league's API-Football
import, and uses a football fact to repair it without guessing at
spellings: a club plays one match at a time. A free-feed fixture and an
API-Football fixture in the same league, kicking off within a few hours,
where at least one side is plainly the same club, are the same match -- so
the *other* side is the same club too, whatever it's called. Then:

1. The two club rows are merged into whichever has more results on record
   (its history is the one worth keeping), taking API-Football's spelling
   as the name, since that's the feed that names every fixture from now on.
   The old spelling stays as an alias.
2. The free-feed fixture is folded into the API-Football one -- which has
   the right kickoff and the provider id that live scores and odds need --
   moving anything that points at it first.
3. A free-feed fixture still waiting to be played that API-Football doesn't
   have at all is removed: API-Football returns the whole season, so a
   fixture missing from it isn't on the real calendar. A finished one is
   left alone -- a result is history, and nothing here deletes history on
   an inference.

Only the period API-Football actually covers for the league is touched, and
a league API-Football has never returned a fixture for is not touched at
all -- so a failed import can never wipe out a league's fixtures.
"""

from __future__ import annotations

import bisect
import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.data.team_matching import canonical_alias, name_match_score
from app.db.models import (
    AdminPick,
    ChatMessage,
    EloHistory,
    FeaturedPick,
    LivePrediction,
    Match,
    MatchOdds,
    MatchView,
    Prediction,
    Team,
)

logger = logging.getLogger(__name__)

# Local time mistaken for UTC puts a European kickoff at most two hours out;
# three leaves room without reaching a club's next match, which is days away.
TWIN_WINDOW = dt.timedelta(hours=3)

# A result imported from a feed with no kickoff time at all sits at midnight,
# so finished duplicates -- which must also agree on the score -- get a day.
FINISHED_TWIN_WINDOW = dt.timedelta(days=1)

# Same bar the API-Football importer uses to accept a spelling as a club.
NAME_THRESHOLD = 0.82

# Match columns worth carrying over from a duplicate when the survivor lacks
# them -- the free feeds sometimes had half-time scores or match stats that
# the API-Football row doesn't.
_CARRY_OVER = (
    "ht_home_score", "ht_away_score", "referee",
    "home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
    "home_corners", "away_corners", "home_fouls", "away_fouls",
    "home_yellows", "away_yellows", "home_reds", "away_reds",
)


@dataclass
class CleanupReport:
    league: str
    # (merged-away spelling, surviving club's name afterwards)
    teams_merged: list[tuple[str, str]] = field(default_factory=list)
    fixtures_merged: int = 0
    fixtures_removed: int = 0
    # Free-feed fixtures kept because nothing proved them duplicates.
    left_alone: list[str] = field(default_factory=list)
    # Pairings refused because one club row would have merged two ways.
    conflicts: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.teams_merged or self.fixtures_merged or self.fixtures_removed)


def _names(team: Team) -> list[str]:
    return [team.name, *(team.aliases or [])]


def _same_club_by_name(a: Team, b: Team) -> bool:
    if a.id == b.id:
        return True
    for x in _names(a):
        for y in _names(b):
            if canonical_alias(x).lower() == canonical_alias(y).lower():
                return True
            if name_match_score(x, y)[0] >= NAME_THRESHOLD:
                return True
    return False


def _scores_agree(a: Match, b: Match) -> bool:
    if a.home_score is None or b.home_score is None:
        return True
    return (a.home_score, a.away_score) == (b.home_score, b.away_score)


def _find_twin(free: Match, api_rows: list[Match], api_dates: list[dt.datetime], teams: dict[int, Team]) -> Match | None:
    """The API-Football row that is this free-feed fixture, if exactly one is."""

    free_finished = free.home_score is not None
    window = FINISHED_TWIN_WINDOW if free_finished else TWIN_WINDOW
    lo = bisect.bisect_left(api_dates, free.date - window)
    hi = bisect.bisect_right(api_dates, free.date + window)

    found: list[Match] = []
    for api in api_rows[lo:hi]:
        if not _scores_agree(free, api):
            continue
        # The wider window is only for two results; otherwise kickoffs must be close.
        if free_finished != (api.home_score is not None) and abs(api.date - free.date) > TWIN_WINDOW:
            continue
        home_ok = _same_club_by_name(teams[free.home_team_id], teams[api.home_team_id])
        away_ok = _same_club_by_name(teams[free.away_team_id], teams[api.away_team_id])
        # One side proving it's the same match is enough (a club can't be
        # in two matches at once), and both sides failing is not a twin.
        if home_ok or away_ok:
            found.append(api)
    return found[0] if len(found) == 1 else None


def _finished_count(db: Session, team_id: int) -> int:
    return db.execute(
        select(func.count()).select_from(Match).where(
            Match.status == "FINISHED",
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        )
    ).scalar() or 0


def _merge_teams(db: Session, api_team: Team, free_team: Team) -> Team:
    """Fold two rows for one club together; returns the survivor."""

    api_history, free_history = _finished_count(db, api_team.id), _finished_count(db, free_team.id)
    if (free_history, -free_team.id) > (api_history, -api_team.id):
        keep, drop = free_team, api_team
    else:
        keep, drop = api_team, free_team

    db.execute(update(Match).where(Match.home_team_id == drop.id).values(home_team_id=keep.id))
    db.execute(update(Match).where(Match.away_team_id == drop.id).values(away_team_id=keep.id))
    db.execute(update(EloHistory).where(EloHistory.team_id == drop.id).values(team_id=keep.id))

    api_spelling = api_team.name
    known = [*_names(keep), *_names(drop)]
    db.delete(drop)
    db.flush()  # frees (name, league) before the survivor may take the name

    taken = db.execute(
        select(Team.id).where(Team.league == keep.league, Team.name == api_spelling, Team.id != keep.id)
    ).first()
    if not taken:
        keep.name = api_spelling
    keep.aliases = sorted({n for n in known if n != keep.name})
    db.flush()
    return keep


def _remove_dependents(db: Session, match_id: int) -> None:
    db.execute(delete(Prediction).where(Prediction.match_id == match_id))
    db.execute(delete(LivePrediction).where(LivePrediction.match_id == match_id))
    db.execute(delete(MatchOdds).where(MatchOdds.match_id == match_id))
    db.execute(delete(EloHistory).where(EloHistory.match_id == match_id))
    db.execute(delete(MatchView).where(MatchView.match_id == match_id))
    db.execute(delete(FeaturedPick).where(FeaturedPick.match_id == match_id))
    db.execute(update(ChatMessage).where(ChatMessage.context_match_id == match_id).values(context_match_id=None))


def _merge_fixture(db: Session, keep: Match, drop: Match, admin_picks: list[AdminPick]) -> None:
    """Fold a free-feed duplicate into its API-Football row."""

    if keep.home_score is None and drop.home_score is not None:
        keep.home_score, keep.away_score = drop.home_score, drop.away_score
        keep.status = "FINISHED"
    for column in _CARRY_OVER:
        if getattr(keep, column) is None and getattr(drop, column) is not None:
            setattr(keep, column, getattr(drop, column))

    # What someone chose or asked about follows the match; what the model
    # derived from the duplicate is simply rebuilt for the survivor.
    db.execute(update(FeaturedPick).where(FeaturedPick.match_id == drop.id).values(match_id=keep.id))
    db.execute(update(ChatMessage).where(ChatMessage.context_match_id == drop.id).values(context_match_id=keep.id))
    for pick in admin_picks:
        legs = pick.legs or []
        if any(leg.get("match_id") == drop.id for leg in legs):
            pick.legs = [{**leg, "match_id": keep.id} if leg.get("match_id") == drop.id else leg for leg in legs]
    keep_has_odds = db.execute(select(MatchOdds.id).where(MatchOdds.match_id == keep.id).limit(1)).first()
    if not keep_has_odds:
        db.execute(update(MatchOdds).where(MatchOdds.match_id == drop.id).values(match_id=keep.id))

    _remove_dependents(db, drop.id)
    db.execute(delete(Match).where(Match.id == drop.id))


def retire_free_fixtures(db: Session, league: str, *, apply: bool = True) -> CleanupReport:
    """See the module docstring. With ``apply=False`` nothing is written and
    the report says what would change."""

    report = CleanupReport(league=league)

    api_rows = list(
        db.execute(
            select(Match).where(Match.league == league, Match.api_fixture_id.is_not(None)).order_by(Match.date)
        ).scalars()
    )
    if not api_rows:
        return report

    covered_from = api_rows[0].date - FINISHED_TWIN_WINDOW
    free_rows = list(
        db.execute(
            select(Match)
            .where(Match.league == league, Match.api_fixture_id.is_(None), Match.date >= covered_from)
            .order_by(Match.date)
        ).scalars()
    )
    if not free_rows:
        return report

    team_ids = {t for m in (*api_rows, *free_rows) for t in (m.home_team_id, m.away_team_id)}
    teams = {t.id: t for t in db.execute(select(Team).where(Team.id.in_(team_ids))).scalars()}
    api_dates = [m.date for m in api_rows]

    def label(m: Match) -> str:
        return f"{teams[m.home_team_id].name} vs {teams[m.away_team_id].name} ({m.date:%Y-%m-%d %H:%M})"

    pairs: list[tuple[Match, Match]] = []
    claimed: set[int] = set()
    orphans: list[Match] = []
    for free in free_rows:
        twin = _find_twin(free, api_rows, api_dates, teams)
        if twin is None or twin.id in claimed:
            orphans.append(free)
            continue
        claimed.add(twin.id)
        pairs.append((free, twin))

    # Club rows the pairings say are one club: API row -> free row. Only
    # within this league -- a club's rows in two divisions are two real
    # histories, never merged here.
    links: dict[int, set[int]] = {}
    for free, api in pairs:
        for f_id, a_id in ((free.home_team_id, api.home_team_id), (free.away_team_id, api.away_team_id)):
            if f_id != a_id and teams[f_id].league == league and teams[a_id].league == league:
                links.setdefault(a_id, set()).add(f_id)
                links.setdefault(f_id, set()).add(a_id)
    ambiguous = {t for t, others in links.items() if len(others) > 1}
    for t in sorted(ambiguous):
        report.conflicts.append(
            f"{teams[t].name} lines up with several clubs: "
            + ", ".join(sorted(teams[o].name for o in links[t]))
        )

    admin_picks = list(db.execute(select(AdminPick)).scalars()) if apply and pairs else []
    merged_into: dict[int, int] = {}
    for free, api in pairs:
        sides = ((free.home_team_id, api.home_team_id), (free.away_team_id, api.away_team_id))
        if any(f != a and (f in ambiguous or a in ambiguous) for f, a in sides):
            report.left_alone.append(f"{label(free)} -- club pairing is ambiguous")
            continue
        cross_league = any(f != a and teams[f].league != teams[a].league for f, a in sides)

        for f_id, a_id in sides:
            f_id, a_id = merged_into.get(f_id, f_id), merged_into.get(a_id, a_id)
            if f_id == a_id or cross_league:
                continue
            api_team, free_team = teams[a_id], teams[f_id]
            report.teams_merged.append((free_team.name, api_team.name))
            if apply:
                keep = _merge_teams(db, api_team, free_team)
                gone = f_id if keep.id == a_id else a_id
                merged_into[gone] = keep.id
                teams[keep.id] = keep
            else:
                merged_into[f_id] = a_id

        report.fixtures_merged += 1
        if apply:
            _merge_fixture(db, api, free, admin_picks)

    covered_until = api_rows[-1].date + FINISHED_TWIN_WINDOW
    for free in orphans:
        if free.status == "FINISHED" or free.home_score is not None:
            report.left_alone.append(f"{label(free)} -- a result API-Football has no twin for")
            continue
        if free.date > covered_until:
            report.left_alone.append(f"{label(free)} -- later than anything API-Football has listed yet")
            continue
        report.fixtures_removed += 1
        if apply:
            _remove_dependents(db, free.id)
            db.execute(delete(Match).where(Match.id == free.id))

    if apply:
        db.commit()
    return report

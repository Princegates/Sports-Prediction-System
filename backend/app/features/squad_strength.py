"""Squad availability: how much weaker a team's actual lineup is today than
its normal one, derived from stored lineup history and reported absences.

Not an ML training feature (yet) -- app.prediction_service applies it as a
post-ensemble explanation factor and a small bounded probability nudge
instead. See that module's own comment for why: the gradient-boosting model
can only learn from a feature that varies across its historical training
set, and there was no lineup data collected for any match before this
existed. Once a real season of MatchLineup rows has accumulated, this
becomes a legitimate feature for a future retrain.

Two sources, combined without double-counting:

1. Reported injuries/suspensions (PlayerAbsence) -- known days ahead, usable
   the moment the daily batch runs.
2. A confirmed-lineup surprise -- a player this team normally starts who
   isn't reported absent but also isn't in today's actual confirmed XI
   (a tactical rest, a late fitness call). Only checkable once a lineup has
   actually been fetched for the match (app.data.squad_ingest.import_lineup),
   which happens in the near-kickoff window, not the daily batch.

Both lean on "how often has this player started recently" as the only
measure of how much losing them matters -- the simple-absence-count approach
chosen over building an actual player-strength rating model, which would
need a lot more infrastructure (a contribution/rating pipeline, a cold-start
answer for players with little history) for a first version.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Match, MatchLineup, Player, PlayerAbsence

# How many of a player's own most recent appearances decide their start-rate.
# Matches app.features.team_stats's own default form window for consistency.
DEFAULT_WINDOW = 10

# A player started at least this fraction of their own recent appearances to
# count as one of a team's "regulars" for the confirmed-lineup-surprise check.
REGULAR_START_RATE = 0.5

# Conservative on purpose -- there is no historical data to calibrate this
# against yet (see the module docstring), so even a badly wrong read of the
# absence data can never swing a prediction by more than six points.
MAX_PROBABILITY_SHIFT = 0.06

# How much one point of combined "strength lost" (missing players' own
# summed start-rates) moves the shift, before the cap above clips it -- a
# single nailed-on starter missing (strength_lost ~= 1.0) alone would reach
# roughly 12.5 points, clipped back down to the 6-point cap.
STRENGTH_PER_SHIFT_POINT = 0.08


@dataclass
class AbsenceImpact:
    missing_players: list[str] = field(default_factory=list)
    # Sum of each missing player's own recent start-rate (0-1) -- a fringe
    # squad player rarely in the XI barely moves this; a nailed-on starter
    # being out moves it close to 1.0 on their own.
    strength_lost: float = 0.0


def player_start_rate(db: Session, player_id: int, as_of: dt.datetime, window: int = DEFAULT_WINDOW) -> float:
    """Fraction of this player's own last ``window`` appearances (strictly
    before ``as_of``, across any team) that were starts -- 0.0 with no
    stored lineup history yet, never an error. Appearances rather than "the
    team's last N matches" on purpose: a player already out for weeks would
    otherwise show a start-rate near zero in their own team's recent
    matches, which measures their current absence, not how big a loss it
    is."""

    rows = db.execute(
        select(MatchLineup.is_starter)
        .join(Match, Match.id == MatchLineup.match_id)
        .where(MatchLineup.player_id == player_id, Match.date < as_of)
        .order_by(Match.date.desc())
        .limit(window)
    ).scalars().all()
    if not rows:
        return 0.0
    return sum(1 for started in rows if started) / len(rows)


def _team_regulars(
    db: Session, team_id: int, as_of: dt.datetime, window: int = DEFAULT_WINDOW, threshold: float = REGULAR_START_RATE
) -> dict[int, float]:
    """player_id -> start_rate for this team's recent regulars, over the
    team's last ``window`` matches that have a stored lineup. Empty with no
    lineup history yet for this team."""

    recent_match_ids = db.execute(
        select(MatchLineup.match_id)
        .join(Match, Match.id == MatchLineup.match_id)
        .where(MatchLineup.team_id == team_id, Match.date < as_of)
        .order_by(Match.date.desc())
        .distinct()
        .limit(window)
    ).scalars().all()
    if not recent_match_ids:
        return {}

    rows = db.execute(
        select(MatchLineup.player_id, MatchLineup.is_starter).where(
            MatchLineup.team_id == team_id, MatchLineup.match_id.in_(recent_match_ids)
        )
    ).all()
    appearances: dict[int, int] = {}
    starts: dict[int, int] = {}
    for player_id, is_starter in rows:
        appearances[player_id] = appearances.get(player_id, 0) + 1
        if is_starter:
            starts[player_id] = starts.get(player_id, 0) + 1

    n = len(recent_match_ids)
    return {pid: starts.get(pid, 0) / n for pid in appearances if starts.get(pid, 0) / n >= threshold}


def team_absence_impact(
    db: Session,
    team_id: int,
    as_of: dt.datetime,
    match: Match | None = None,
    *,
    window: int = DEFAULT_WINDOW,
) -> AbsenceImpact:
    """Everything known to be missing for ``team_id`` as of ``as_of``,
    scoped to ``match`` when given. Safe to call with no lineup/absence data
    at all -- an empty ``AbsenceImpact`` is the correct answer for a fresh
    deployment or a team this hasn't accumulated history for yet, not an
    error.
    """

    impact = AbsenceImpact()
    counted: set[int] = set()

    # 1. Reported injuries/suspensions -- scoped to this fixture when the
    # provider tied the report to one, else any current report for the team
    # (no fixture attached means "generally out right now").
    absence_query = select(PlayerAbsence.player_id).where(PlayerAbsence.team_id == team_id)
    if match is not None:
        absence_query = absence_query.where(or_(PlayerAbsence.match_id == match.id, PlayerAbsence.match_id.is_(None)))
    for player_id in db.execute(absence_query).scalars().all():
        rate = player_start_rate(db, player_id, as_of, window=window)
        if rate <= 0 or player_id in counted:
            continue
        counted.add(player_id)
        player = db.get(Player, player_id)
        impact.missing_players.append(player.name if player else f"player #{player_id}")
        impact.strength_lost += rate

    # 2. A confirmed-lineup surprise: a regular starter neither reported
    # absent nor in today's actual confirmed XI. Only checkable once a
    # lineup has actually been fetched for this match -- an empty
    # confirmed_starters set means "not fetched yet", not "nobody started".
    if match is not None:
        confirmed_starters = set(
            db.execute(
                select(MatchLineup.player_id).where(
                    MatchLineup.match_id == match.id,
                    MatchLineup.team_id == team_id,
                    MatchLineup.is_starter.is_(True),
                )
            ).scalars().all()
        )
        if confirmed_starters:
            for player_id, rate in _team_regulars(db, team_id, as_of, window=window).items():
                if player_id in counted or player_id in confirmed_starters:
                    continue
                counted.add(player_id)
                player = db.get(Player, player_id)
                impact.missing_players.append(player.name if player else f"player #{player_id}")
                impact.strength_lost += rate

    return impact


def absence_probability_nudge(home_impact: AbsenceImpact, away_impact: AbsenceImpact) -> float:
    """Signed shift to add to ``home_win`` (and subtract from ``away_win``)
    -- positive favors the home team. Bounded by ``MAX_PROBABILITY_SHIFT``
    regardless of how lopsided the computed absences are."""

    net = away_impact.strength_lost - home_impact.strength_lost
    shift = net * STRENGTH_PER_SHIFT_POINT
    return max(-MAX_PROBABILITY_SHIFT, min(MAX_PROBABILITY_SHIFT, shift))

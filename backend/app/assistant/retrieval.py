"""Database reads for the chat assistant.

Each function returns plain dataclasses rather than ORM rows so the responder
can't accidentally trigger lazy loads mid-sentence, and so a retrieval result
is trivially assertable in a test.

Nothing here computes a probability. If a match has no stored prediction, the
retrieval layer says so and the responder offers to generate one -- it never
substitutes an estimate of its own, because a number the ensemble didn't
produce has no calibration behind it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import LivePrediction, Match, ModelMetric, Prediction, Team
from app.features.team_stats import TeamForm, compute_team_form
from app.live_engine import LIVE_STALE_AFTER


@dataclass
class MatchCard:
    match_id: int
    league: str
    kickoff: dt.datetime
    home_team: str
    away_team: str
    status: str
    home_score: int | None = None
    away_score: int | None = None
    prediction: Prediction | None = None


@dataclass
class AccuracySnapshot:
    """Backtest metrics as recorded by ``scripts/backtest.py``.

    ``splits`` is keyed by split name ("test", "validation", ...) then metric
    name, so the responder can quote the held-out test split specifically
    rather than an average across splits that would flatter the model.

    With more than one league those metrics need combining, and the obvious
    way is wrong twice over. Overwriting -- which this did until a five-league
    deployment surfaced it -- reports one arbitrary league's accuracy while
    naming every league beside it. A plain mean is also wrong, because leagues
    contribute different numbers of evaluated matches. Both are match-weighted
    here, which is what makes the headline figure mean "across everything we
    tested on".

    ``per_league`` keeps the unaggregated figures, because the spread between
    leagues is real and interesting -- the Premier League scores several
    points below the others.
    """

    model_version: str | None = None
    computed_at: dt.datetime | None = None
    leagues: list[str] = field(default_factory=list)
    splits: dict[str, dict[str, float]] = field(default_factory=dict)
    per_league: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return bool(self.splits)


def find_match_for_teams(
    db: Session,
    team_a: Team,
    team_b: Team,
    now: dt.datetime | None = None,
) -> MatchCard | None:
    """The fixture between these two teams the user most likely means: the
    next scheduled one, or failing that the most recently played one."""

    now = now or dt.datetime.utcnow()
    pairing = or_(
        (Match.home_team_id == team_a.id) & (Match.away_team_id == team_b.id),
        (Match.home_team_id == team_b.id) & (Match.away_team_id == team_a.id),
    )

    upcoming = db.execute(
        select(Match).where(pairing, Match.date >= now).order_by(Match.date.asc()).limit(1)
    ).scalars().first()
    if upcoming is not None:
        return to_card(db, upcoming)

    previous = db.execute(
        select(Match).where(pairing, Match.date < now).order_by(Match.date.desc()).limit(1)
    ).scalars().first()
    return to_card(db, previous) if previous is not None else None


def to_card(db: Session, match: Match, with_prediction: bool = True) -> MatchCard:
    prediction = latest_prediction(db, match.id) if with_prediction else None
    return MatchCard(
        match_id=match.id,
        league=match.league,
        kickoff=match.date,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        status=match.status,
        home_score=match.home_score,
        away_score=match.away_score,
        prediction=prediction,
    )


def latest_prediction(db: Session, match_id: int) -> Prediction | None:
    return db.execute(
        select(Prediction).where(Prediction.match_id == match_id).order_by(Prediction.created_at.desc()).limit(1)
    ).scalars().first()


def get_match_card(db: Session, match_id: int) -> MatchCard | None:
    match = db.get(Match, match_id)
    return to_card(db, match) if match is not None else None


def fixtures_between(
    db: Session,
    date_from: dt.datetime,
    date_to: dt.datetime,
    league: str | None = None,
    limit: int = 12,
) -> list[MatchCard]:
    stmt = select(Match).where(Match.date >= date_from, Match.date < date_to)
    if league:
        stmt = stmt.where(Match.league == league)
    matches = db.execute(stmt.order_by(Match.date.asc()).limit(limit)).scalars().all()
    return [to_card(db, m) for m in matches]


def ranked_predictions(
    db: Session,
    date_from: dt.datetime,
    date_to: dt.datetime,
    league: str | None = None,
    limit: int = 5,
    min_data_quality: float = 0.0,
) -> list[MatchCard]:
    """Upcoming fixtures that already have a stored prediction, ordered by
    the global most-likely outcome's probability.

    Only stored predictions are considered on purpose: generating one per
    fixture inside a chat turn would make a single message trigger dozens of
    model runs. The responder tells the user when a fixture is missing a
    prediction so they can generate it from the match page instead.
    """

    stmt = (
        select(Match, Prediction)
        .join(Prediction, Prediction.match_id == Match.id)
        .where(Match.date >= date_from, Match.date < date_to)
        .where(Prediction.data_quality_score >= min_data_quality)
    )
    if league:
        stmt = stmt.where(Match.league == league)

    seen: dict[int, MatchCard] = {}
    rows = db.execute(stmt.order_by(Prediction.global_outcome_probability.desc())).all()
    for match, prediction in rows:
        # Rows are already probability-ordered, so the first time a match
        # appears is its best (newest-scoring) prediction.
        if match.id in seen:
            continue
        seen[match.id] = MatchCard(
            match_id=match.id,
            league=match.league,
            kickoff=match.date,
            home_team=match.home_team.name,
            away_team=match.away_team.name,
            status=match.status,
            home_score=match.home_score,
            away_score=match.away_score,
            prediction=prediction,
        )
        if len(seen) >= limit:
            break
    return list(seen.values())


def team_form(db: Session, team: Team, as_of: dt.datetime | None = None) -> TeamForm:
    as_of = as_of or dt.datetime.utcnow()
    return compute_team_form(db, team.id, as_of, team.league)


def next_fixture_for_team(db: Session, team: Team, now: dt.datetime | None = None) -> MatchCard | None:
    now = now or dt.datetime.utcnow()
    match = db.execute(
        select(Match)
        .where(or_(Match.home_team_id == team.id, Match.away_team_id == team.id), Match.date >= now)
        .order_by(Match.date.asc())
        .limit(1)
    ).scalars().first()
    return to_card(db, match) if match is not None else None


def head_to_head(db: Session, team_a: Team, team_b: Team, limit: int = 10) -> list[MatchCard]:
    pairing = or_(
        (Match.home_team_id == team_a.id) & (Match.away_team_id == team_b.id),
        (Match.home_team_id == team_b.id) & (Match.away_team_id == team_a.id),
    )
    matches = db.execute(
        select(Match)
        .where(pairing, Match.home_score.is_not(None))
        .order_by(Match.date.desc())
        .limit(limit)
    ).scalars().all()
    return [to_card(db, m, with_prediction=False) for m in matches]


def live_snapshots(db: Session, match_id: int, limit: int = 1) -> list[LivePrediction]:
    return list(
        db.execute(
            select(LivePrediction)
            .where(LivePrediction.match_id == match_id)
            .order_by(LivePrediction.minute.desc())
            .limit(limit)
        ).scalars()
    )


def live_matches(db: Session, limit: int = 8) -> list[MatchCard]:
    # A simulated event from a match's own Live tab sets status="LIVE" too
    # (see live_engine.record_live_event) -- that's a local sandbox demo,
    # not something Guda should tell every user is actually in play. Only a
    # *recent* confirmation from the real sync job counts here, the same
    # gate /api/matches?status=LIVE applies.
    cutoff = dt.datetime.utcnow() - LIVE_STALE_AFTER
    matches = db.execute(
        select(Match)
        .where(Match.status == "LIVE", Match.live_synced_at.is_not(None), Match.live_synced_at >= cutoff)
        .order_by(Match.date.asc())
        .limit(limit)
    ).scalars().all()
    return [to_card(db, m) for m in matches]


def accuracy_snapshot(db: Session) -> AccuracySnapshot:
    """The most recent backtest run's metrics, grouped by split.

    "Most recent" is resolved by ``computed_at`` of the newest *model* row
    (excluding the ``baseline-*`` rows scripts/backtest.py stores alongside
    it for comparison) and then filtered to that model version, so a stale
    run from an older version can't leak into the numbers quoted to a user.
    Every baseline row shares the real model's computed_at exactly, so
    without excluding them here, a tie could land on "baseline-majority_class"
    and quote a naive baseline's accuracy as if it were the model's.
    """

    newest = db.execute(
        select(ModelMetric)
        .where(~ModelMetric.model_version.like("baseline-%"))
        .order_by(ModelMetric.computed_at.desc())
        .limit(1)
    ).scalars().first()
    if newest is None:
        return AccuracySnapshot()

    rows = list(
        db.execute(select(ModelMetric).where(ModelMetric.model_version == newest.model_version)).scalars()
    )

    # (split, league) -> {metric: value}, so leagues can be combined rather
    # than silently overwriting one another.
    by_split_league: dict[str, dict[str, dict[str, float]]] = {}
    leagues: set[str] = set()
    for row in rows:
        by_split_league.setdefault(row.split, {}).setdefault(row.league, {})[row.metric_name] = row.metric_value
        leagues.add(row.league)

    splits: dict[str, dict[str, float]] = {}
    for split, league_metrics in by_split_league.items():
        splits[split] = _weighted_metrics(league_metrics)

    preferred = "test" if "test" in by_split_league else next(iter(by_split_league), None)

    return AccuracySnapshot(
        model_version=newest.model_version,
        computed_at=newest.computed_at,
        leagues=sorted(leagues),
        splits=splits,
        per_league=by_split_league.get(preferred, {}) if preferred else {},
    )


def _weighted_metrics(league_metrics: dict[str, dict[str, float]]) -> dict[str, float]:
    """Combine one split's per-league metrics into overall figures.

    Accuracy, log loss, Brier score and calibration error are all means over
    matches, so the correct combination is a mean weighted by each league's
    evaluated-match count -- not a plain average, which would let a small
    league swing the headline as hard as a large one. ``n`` itself sums.

    A league missing ``n`` (an older run, before the count was recorded)
    falls back to weight 1 rather than being dropped, so a partial history
    still produces a usable figure.
    """

    if not league_metrics:
        return {}

    weights = {lg: m.get("n", 1.0) or 1.0 for lg, m in league_metrics.items()}
    total_weight = sum(weights.values())

    metric_names = {name for m in league_metrics.values() for name in m}
    combined: dict[str, float] = {}

    for name in metric_names:
        if name == "n":
            combined["n"] = sum(m.get("n", 0.0) for m in league_metrics.values())
            continue
        contributing = [(weights[lg], m[name]) for lg, m in league_metrics.items() if name in m]
        if not contributing:
            continue
        w_sum = sum(w for w, _ in contributing)
        combined[name] = sum(w * v for w, v in contributing) / w_sum if w_sum else 0.0

    return combined


@dataclass
class ConfidenceBandRecord:
    confidence: str
    graded: int
    hits: int

    @property
    def hit_rate(self) -> float:
        return self.hits / self.graded if self.graded else 0.0


@dataclass
class TrackRecord:
    """How the system's own pre-match calls have actually done, as opposed
    to the offline backtest in AccuracySnapshot -- this is a verifiable
    record of predictions that were genuinely made before kickoff, scored
    against what genuinely happened.
    """

    graded_predictions: int = 0
    hits: int = 0
    by_confidence: list[ConfidenceBandRecord] = field(default_factory=list)
    earliest_graded_at: dt.datetime | None = None

    @property
    def has_data(self) -> bool:
        return self.graded_predictions > 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.graded_predictions if self.graded_predictions else 0.0


def track_record(db: Session) -> TrackRecord:
    """Grades each finished match's *first* stored prediction (the one made
    furthest from kickoff, before any later refresh could have seen more
    form data) against what actually happened.

    Using the first prediction per match rather than every stored one is
    what keeps this honest: a match whose prediction was regenerated five
    times would otherwise count five times as much as one predicted once.
    """

    earliest = (
        select(Prediction.match_id, func.min(Prediction.created_at).label("first_created_at"))
        .group_by(Prediction.match_id)
        .subquery()
    )

    rows = db.execute(
        select(Prediction, Match)
        .join(Match, Match.id == Prediction.match_id)
        .join(
            earliest,
            (earliest.c.match_id == Prediction.match_id) & (earliest.c.first_created_at == Prediction.created_at),
        )
        .where(Match.home_score.is_not(None), Match.away_score.is_not(None))
    ).all()

    band_totals: dict[str, list[int]] = {}  # confidence -> [graded, hits]
    total_hits = 0
    earliest_at: dt.datetime | None = None

    for prediction, match in rows:
        actual = "H" if match.home_score > match.away_score else ("D" if match.home_score == match.away_score else "A")
        predicted = max({"H": prediction.home_win, "D": prediction.draw, "A": prediction.away_win}, key=lambda k: {"H": prediction.home_win, "D": prediction.draw, "A": prediction.away_win}[k])
        hit = 1 if predicted == actual else 0
        total_hits += hit

        band = band_totals.setdefault(prediction.confidence, [0, 0])
        band[0] += 1
        band[1] += hit

        if earliest_at is None or prediction.created_at < earliest_at:
            earliest_at = prediction.created_at

    return TrackRecord(
        graded_predictions=len(rows),
        hits=total_hits,
        by_confidence=[ConfidenceBandRecord(confidence=c, graded=g, hits=h) for c, (g, h) in sorted(band_totals.items())],
        earliest_graded_at=earliest_at,
    )


def coverage_counts(db: Session) -> dict[str, int]:
    """Corpus size figures, used by both the assistant and the public
    landing page so the two can never quote different totals."""

    now = dt.datetime.utcnow()
    return {
        "teams": db.execute(select(func.count()).select_from(Team)).scalar() or 0,
        "leagues": db.execute(select(func.count(func.distinct(Match.league)))).scalar() or 0,
        "matches_analyzed": db.execute(
            select(func.count()).select_from(Match).where(Match.home_score.is_not(None))
        ).scalar() or 0,
        "predictions_generated": db.execute(select(func.count()).select_from(Prediction)).scalar() or 0,
        "upcoming_fixtures": db.execute(
            select(func.count()).select_from(Match).where(Match.date >= now)
        ).scalar() or 0,
    }

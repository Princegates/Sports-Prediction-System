"""Unauthenticated endpoints for the public marketing site.

Everything else in this API is gated behind an approved account. The welcome
page still needs real numbers on it -- a landing page that claims "thousands of
matches analyzed" without reading the database is the kind of thing that turns
out to be false the moment someone checks.

So these endpoints exist, and the rule for what may live here is strict: the
corpus size, the model's own measured accuracy, and the *shape* of an upcoming
fixture list. No probabilities, no selections, no reasoning -- those are the
product, and they stay behind the login.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import PublicAccuracyOut, PublicFixtureOut, PublicStatsOut
from app.assistant import retrieval
from app.db.models import Match, Prediction, User

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/stats", response_model=PublicStatsOut)
def public_stats(db: Session = Depends(get_db)) -> PublicStatsOut:
    """Corpus-size figures, read live so the landing page can't drift out of
    sync with what the system actually holds."""

    counts = retrieval.coverage_counts(db)
    leagues = list(
        db.execute(select(Match.league).distinct().order_by(Match.league.asc())).scalars()
    )
    active_members = db.execute(
        select(func.count()).select_from(User).where(User.status == "active")
    ).scalar() or 0

    return PublicStatsOut(
        matches_analyzed=counts["matches_analyzed"],
        teams_tracked=counts["teams"],
        leagues_covered=counts["leagues"],
        predictions_generated=counts["predictions_generated"],
        upcoming_fixtures=counts["upcoming_fixtures"],
        active_members=active_members,
        league_names=leagues,
        # Markets the outcome registry scores for every match: 1X2 (3),
        # double chance (3), BTTS (2), five over/under lines (10), plus the
        # correct-score grid.
        markets_per_match=18,
    )


@router.get("/accuracy", response_model=PublicAccuracyOut)
def public_accuracy(db: Session = Depends(get_db)) -> PublicAccuracyOut:
    """The most recent backtest's held-out metrics.

    Returns ``has_data: false`` rather than placeholder figures when no
    backtest has been run -- the landing page then shows the methodology
    instead of a fabricated accuracy claim.
    """

    snapshot = retrieval.accuracy_snapshot(db)
    if not snapshot.has_data:
        return PublicAccuracyOut(has_data=False)

    preferred_split = (
        "test" if "test" in snapshot.splits
        else "validation" if "validation" in snapshot.splits
        else next(iter(snapshot.splits))
    )
    metrics = snapshot.splits[preferred_split]

    return PublicAccuracyOut(
        has_data=True,
        model_version=snapshot.model_version,
        computed_at=snapshot.computed_at,
        split=preferred_split,
        leagues=snapshot.leagues,
        accuracy=metrics.get("accuracy"),
        log_loss=metrics.get("log_loss"),
        brier_score=metrics.get("brier_score") or metrics.get("brier"),
        matches_evaluated=int(metrics["n"]) if "n" in metrics else (
            int(metrics["matches"]) if "matches" in metrics else None
        ),
    )


@router.get("/fixtures", response_model=list[PublicFixtureOut])
def public_fixtures(days_ahead: int = 3, limit: int = 6, db: Session = Depends(get_db)) -> list[PublicFixtureOut]:
    """Upcoming fixtures with *whether* a prediction exists and how confident
    it is -- but never the selection or the probability. Enough to show the
    system is live and covering real matches; not enough to be the product."""

    days_ahead = max(0, min(days_ahead, 14))
    limit = max(1, min(limit, 20))
    now = dt.datetime.utcnow()

    rows = db.execute(
        select(Match).where(Match.date >= now, Match.date < now + dt.timedelta(days=days_ahead))
        .order_by(Match.date.asc())
        .limit(limit)
    ).scalars().all()

    out: list[PublicFixtureOut] = []
    for match in rows:
        prediction = db.execute(
            select(Prediction)
            .where(Prediction.match_id == match.id)
            .order_by(Prediction.created_at.desc())
            .limit(1)
        ).scalars().first()
        out.append(
            PublicFixtureOut(
                league=match.league,
                kickoff=match.date,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                has_prediction=prediction is not None,
                confidence=prediction.confidence if prediction else None,
            )
        )
    return out

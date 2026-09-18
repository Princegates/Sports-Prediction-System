from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.access import access_code_effective_status, access_grant_effective_status, current_grant, mask_code
from app.api.schemas import (
    AccessCodeOut,
    AccessGrantOut,
    AdminUserOut,
    GlobalOutcomeOut,
    LivePredictionOut,
    MatchHistoryOut,
    MatchOut,
    PredictionOut,
    TeamOut,
    UserOut,
)
from app.db.models import AccessCode, AccessGrant, LivePrediction, Match, MatchView, Prediction, Team, User


def team_to_schema(team: Team) -> TeamOut:
    return TeamOut(id=team.id, name=team.name, league=team.league, country=team.country)


def user_to_schema(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        theme=user.theme,
        accent_profile=user.accent_profile,
        created_at=user.created_at,
    )


def match_view_to_schema(view: MatchView, match: Match) -> MatchHistoryOut:
    return MatchHistoryOut(match=match_to_schema(match), viewed_at=view.viewed_at)


def admin_user_to_schema(user: User, db: Session) -> AdminUserOut:
    grant = current_grant(db, user)
    access_status = "none"
    access_expires_at = None
    if grant is not None:
        access_expires_at = grant.expires_at
        access_status = "active" if grant.expires_at > dt.datetime.utcnow() else "expired"

    return AdminUserOut(
        **user_to_schema(user).model_dump(),
        access_status=access_status,
        access_expires_at=access_expires_at,
    )


def access_code_to_schema(code: AccessCode, *, reveal_full: bool = False) -> AccessCodeOut:
    return AccessCodeOut(
        id=code.id,
        code=code.code if reveal_full else mask_code(code.code),
        status=access_code_effective_status(code),
        duration_days=code.duration_days,
        code_expires_at=code.code_expires_at,
        redemption_limit=code.redemption_limit,
        redemption_count=code.redemption_count,
        assigned_user_id=code.assigned_user_id,
        created_by_user_id=code.created_by_user_id,
        created_at=code.created_at,
        revoked_at=code.revoked_at,
        revoked_reason=code.revoked_reason,
        notes=code.notes,
    )


def access_grant_to_schema(grant: AccessGrant) -> AccessGrantOut:
    return AccessGrantOut(
        id=grant.id,
        access_code_id=grant.access_code_id,
        status=access_grant_effective_status(grant),
        activated_at=grant.activated_at,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
        revoked_reason=grant.revoked_reason,
    )


def match_to_schema(match: Match) -> MatchOut:
    return MatchOut(
        id=match.id,
        league=match.league,
        season=match.season,
        date=match.date,
        status=match.status,
        home_team=team_to_schema(match.home_team),
        away_team=team_to_schema(match.away_team),
        home_score=match.home_score,
        away_score=match.away_score,
    )


def prediction_to_schema(prediction: Prediction) -> PredictionOut:
    return PredictionOut(
        match_id=prediction.match_id,
        created_at=prediction.created_at,
        model_version=prediction.model_version,
        home_win=prediction.home_win,
        draw=prediction.draw,
        away_win=prediction.away_win,
        over_probabilities=prediction.over_probabilities,
        btts_yes=prediction.btts_yes,
        btts_no=prediction.btts_no,
        correct_score_probabilities=prediction.correct_score_probabilities,
        most_likely_score=prediction.most_likely_score,
        most_likely_score_probability=prediction.most_likely_score_probability,
        global_outcome=GlobalOutcomeOut(
            market=prediction.global_outcome_market,
            selection=prediction.global_outcome_selection,
            probability=prediction.global_outcome_probability,
        ),
        confidence=prediction.confidence,
        data_quality_score=prediction.data_quality_score,
        model_agreement_score=prediction.model_agreement_score,
        explanation=prediction.explanation,
        model_breakdown=prediction.model_breakdown,
    )


def live_prediction_to_schema(lp: LivePrediction) -> LivePredictionOut:
    return LivePredictionOut(
        match_id=lp.match_id,
        created_at=lp.created_at,
        minute=lp.minute,
        score_home=lp.score_home,
        score_away=lp.score_away,
        home_win=lp.home_win,
        draw=lp.draw,
        away_win=lp.away_win,
        over_probabilities=lp.over_probabilities,
        btts_yes=lp.btts_yes,
        global_outcome=GlobalOutcomeOut(
            market=lp.global_outcome_market,
            selection=lp.global_outcome_selection,
            probability=lp.global_outcome_probability,
        ),
        trigger_event=lp.trigger_event,
    )

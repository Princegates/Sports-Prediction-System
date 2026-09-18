"""Wires the ensemble, outcome registry, explainability and quality scoring
together into one persisted ``Prediction`` row per match. This is the module
both the API and the offline scripts call so the two never drift apart.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Match, Prediction
from app.explain import generate_explanation
from app.features.team_stats import compute_team_form, matches_played_before
from app.model_store import load_calibrators, load_ensemble_weights, load_ml_model
from app.outcomes.engine import select_global_most_likely
from app.outcomes.registry import build_outcome_registry
from app.prediction_models import elo, league_strength
from app.prediction_models.ensemble import generate_prediction
from app.prediction_models.ml_model import LeagueFeatureCache
from app.quality import confidence_label, data_quality_score

MODEL_VERSION = "ensemble-v1"


def build_prediction_for_match(
    db: Session,
    match: Match,
    feature_cache: "LeagueFeatureCache | None" = None,
    strength: "league_strength.LeagueStrength | None" = None,
) -> Prediction:
    """Pass ``feature_cache`` when building predictions for several matches
    in one league -- see ``FeatureCachePool``. Omitted, each call reloads
    that league's history.

    ``strength`` is the cross-league calibration, consulted only for European
    competitions. Pass it when predicting several matches; omitted, it is
    loaded per match."""

    settings = get_settings()
    as_of = match.date

    ml_model = load_ml_model(match.league)
    calibrators = load_calibrators(match.league)
    weights = load_ensemble_weights(match.league)

    result = generate_prediction(
        db,
        match.home_team_id,
        match.away_team_id,
        match.league,
        as_of,
        ml_model=ml_model,
        calibrators=calibrators,
        weights=weights,
        feature_cache=feature_cache,
        strength=strength,
    )

    matches_home = matches_played_before(db, match.home_team_id, as_of, match.league)
    matches_away = matches_played_before(db, match.away_team_id, as_of, match.league)
    dq = data_quality_score(matches_home, matches_away)

    outcomes = build_outcome_registry(
        result.home_win,
        result.draw,
        result.away_win,
        result.over_probabilities,
        result.btts_yes,
        result.btts_no,
        result.correct_score_probabilities,
        matches_available=min(matches_home, matches_away),
    )
    global_outcome = select_global_most_likely(outcomes)

    confidence = confidence_label(dq, result.model_agreement_1x2)

    home_form = compute_team_form(db, match.home_team_id, as_of, match.league)
    away_form = compute_team_form(db, match.away_team_id, as_of, match.league)
    home_elo = elo.get_rating_before(db, match.home_team_id, as_of, settings.elo_start_rating)
    away_elo = elo.get_rating_before(db, match.away_team_id, as_of, settings.elo_start_rating)

    # The same calibration the ensemble used, or the explanation would justify
    # a probability with ratings that did not produce it.
    calibrated = league_strength.calibrate(
        db,
        strength if strength is not None else league_strength.LeagueStrength.load(db),
        competition=match.league,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_elo=home_elo,
        away_elo=away_elo,
        default_home_advantage=settings.home_advantage_elo,
    )
    elo_diff = (calibrated.home + calibrated.home_advantage) - calibrated.away

    explanation = generate_explanation(
        match.home_team.name,
        match.away_team.name,
        home_form,
        away_form,
        elo_diff,
        calibrated.home_advantage,
    )
    if calibrated.applied:
        explanation = f"{explanation} Ratings are calibrated across leagues: {calibrated.note}."

    prediction = Prediction(
        match_id=match.id,
        model_version=MODEL_VERSION,
        home_win=result.home_win,
        draw=result.draw,
        away_win=result.away_win,
        over_probabilities=result.over_probabilities,
        btts_yes=result.btts_yes,
        btts_no=result.btts_no,
        correct_score_probabilities=result.correct_score_probabilities,
        most_likely_score=result.most_likely_score,
        most_likely_score_probability=result.most_likely_score_probability,
        global_outcome_market=global_outcome.market if global_outcome else "Insufficient Data",
        global_outcome_selection=global_outcome.selection if global_outcome else "Not enough match history yet",
        global_outcome_probability=global_outcome.probability if global_outcome else 0.0,
        confidence=confidence,
        data_quality_score=dq,
        model_agreement_score=result.model_agreement_1x2,
        explanation=explanation,
        model_breakdown=result.model_breakdown,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction

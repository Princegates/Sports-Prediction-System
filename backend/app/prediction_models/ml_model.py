"""Gradient boosting model (spec section 23, Model 4) trained on engineered
features: Elo gap, recent form, goal averages, rest-day differential.

This is deliberately a second, independently-wrong-in-different-ways model
from Elo/Poisson: the point of an ensemble is that each member's errors are
somewhat uncorrelated, which is also what the "model agreement score" (spec
section 34) measures.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.team_stats import compute_team_form
from app.prediction_models.elo import get_rating_before
from app.db.models import Match

FEATURE_COLUMNS = [
    "elo_diff",
    "home_ppg",
    "away_ppg",
    "home_gs_avg",
    "home_gc_avg",
    "away_gs_avg",
    "away_gc_avg",
    "home_rest_days",
    "away_rest_days",
    "form_diff",
]


def build_feature_row(
    db: Session,
    home_team_id: int,
    away_team_id: int,
    league: str,
    as_of: dt.datetime,
    home_advantage: float = 60.0,
) -> dict[str, float]:
    home_elo = get_rating_before(db, home_team_id, as_of)
    away_elo = get_rating_before(db, away_team_id, as_of)
    home_form = compute_team_form(db, home_team_id, as_of, league)
    away_form = compute_team_form(db, away_team_id, as_of, league)

    return {
        "elo_diff": (home_elo + home_advantage) - away_elo,
        "home_ppg": home_form.points_per_game,
        "away_ppg": away_form.points_per_game,
        "home_gs_avg": home_form.goals_scored_avg,
        "home_gc_avg": home_form.goals_conceded_avg,
        "away_gs_avg": away_form.goals_scored_avg,
        "away_gc_avg": away_form.goals_conceded_avg,
        "home_rest_days": home_form.rest_days,
        "away_rest_days": away_form.rest_days,
        "form_diff": home_form.points_per_game - away_form.points_per_game,
    }


def build_training_dataset(
    db: Session,
    league: str,
    start_date: dt.datetime | None = None,
    end_date: dt.datetime | None = None,
) -> pd.DataFrame:
    conditions = [Match.league == league, Match.home_score.is_not(None)]
    if start_date:
        conditions.append(Match.date >= start_date)
    if end_date:
        conditions.append(Match.date < end_date)

    matches = list(db.execute(select(Match).where(*conditions).order_by(Match.date.asc())).scalars())

    rows: list[dict] = []
    for m in matches:
        features = build_feature_row(db, m.home_team_id, m.away_team_id, league, m.date)
        if m.home_score > m.away_score:
            result = "H"
        elif m.home_score == m.away_score:
            result = "D"
        else:
            result = "A"
        features.update(
            {
                "match_id": m.id,
                "date": m.date,
                "result": result,
                "over_2_5": int((m.home_score + m.away_score) > 2.5),
                "btts": int(m.home_score >= 1 and m.away_score >= 1),
            }
        )
        rows.append(features)

    return pd.DataFrame(rows)


@dataclass
class MLPrediction:
    home_win: float
    draw: float
    away_win: float
    over_2_5: float
    btts_yes: float


class MLModel:
    """Wraps three GradientBoostingClassifiers behind one predict() call."""

    def __init__(self) -> None:
        self.result_clf: GradientBoostingClassifier | None = None
        self.over25_clf: GradientBoostingClassifier | None = None
        self.btts_clf: GradientBoostingClassifier | None = None
        self._result_classes: list[str] = []

    def fit(self, df: pd.DataFrame) -> None:
        if df.empty:
            raise ValueError("Cannot fit MLModel on an empty dataset")

        x = df[FEATURE_COLUMNS].to_numpy()

        self.result_clf = GradientBoostingClassifier(random_state=42, n_estimators=150, max_depth=3, learning_rate=0.08)
        self.result_clf.fit(x, df["result"])
        self._result_classes = list(self.result_clf.classes_)

        self.over25_clf = GradientBoostingClassifier(random_state=42, n_estimators=150, max_depth=3, learning_rate=0.08)
        self.over25_clf.fit(x, df["over_2_5"])

        self.btts_clf = GradientBoostingClassifier(random_state=42, n_estimators=150, max_depth=3, learning_rate=0.08)
        self.btts_clf.fit(x, df["btts"])

    def predict(self, feature_row: dict[str, float]) -> MLPrediction:
        if self.result_clf is None or self.over25_clf is None or self.btts_clf is None:
            raise RuntimeError("MLModel must be fit() or load()-ed before predict()")

        x = np.array([[feature_row[c] for c in FEATURE_COLUMNS]])

        result_probs = dict(zip(self._result_classes, self.result_clf.predict_proba(x)[0]))
        over25 = float(self.over25_clf.predict_proba(x)[0][1])
        btts = float(self.btts_clf.predict_proba(x)[0][1])

        return MLPrediction(
            home_win=float(result_probs.get("H", 0.0)),
            draw=float(result_probs.get("D", 0.0)),
            away_win=float(result_probs.get("A", 0.0)),
            over_2_5=over25,
            btts_yes=btts,
        )

    def save(self, path: str | Path) -> None:
        joblib.dump(
            {
                "result_clf": self.result_clf,
                "over25_clf": self.over25_clf,
                "btts_clf": self.btts_clf,
                "result_classes": self._result_classes,
            },
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> "MLModel":
        payload = joblib.load(path)
        model = cls()
        model.result_clf = payload["result_clf"]
        model.over25_clf = payload["over25_clf"]
        model.btts_clf = payload["btts_clf"]
        model._result_classes = payload["result_classes"]
        return model

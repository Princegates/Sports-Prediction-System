from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """A registered account. New accounts start in ``pending`` status and
    can't log in until a superadmin approves them (spec: manual payment
    confirmation via Hubtel comes later -- ``payment_reference`` is where a
    user-submitted transaction reference lives in the meantime, and
    ``approved_by_user_id``/``approved_at`` record who signed off and when).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))

    # "user" or "superadmin"
    role: Mapped[str] = mapped_column(String(16), default="user")
    # "pending" (awaiting approval) / "active" / "suspended"
    status: Mapped[str] = mapped_column(String(16), default="pending")

    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Personal display preferences, carried with the account (not just the
    # browser) so they follow the user across devices.
    theme: Mapped[str | None] = mapped_column(String(16), nullable=True)
    accent_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class MatchView(Base):
    """Records that a user opened a match's AI analysis, for their personal
    'recently viewed' history. Upserted per (user, match) so re-opening a
    match bumps it to the top instead of growing an unbounded log.
    """

    __tablename__ = "match_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    viewed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "match_id", name="uq_match_view_user_match"),)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    league: Mapped[str] = mapped_column(String(64), index=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Free-text alias so we can match names across data sources
    # (football-data.co.uk vs TheSportsDB spell some clubs differently).
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)

    __table_args__ = (UniqueConstraint("name", "league", name="uq_team_name_league"),)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    league: Mapped[str] = mapped_column(String(64), index=True)
    season: Mapped[str] = mapped_column(String(16))
    date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)

    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # SCHEDULED, LIVE, FINISHED
    status: Mapped[str] = mapped_column(String(16), default="SCHEDULED")
    source: Mapped[str] = mapped_column(String(32), default="football-data.co.uk")

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])

    @property
    def is_finished(self) -> bool:
        return self.home_score is not None and self.away_score is not None


class EloHistory(Base):
    """Point-in-time Elo snapshot, recorded after each match a team plays.

    Keeping the full history (rather than only "current" rating) is what
    lets the backtester compute a prediction using only information that was
    actually available before kickoff -- i.e. no data leakage.
    """

    __tablename__ = "elo_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    rating_before: Mapped[float] = mapped_column(Float)
    rating_after: Mapped[float] = mapped_column(Float)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    model_version: Mapped[str] = mapped_column(String(32))

    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)

    # goal line -> probability, e.g. {"0.5": 0.94, "1.5": 0.78, ...}
    over_probabilities: Mapped[dict] = mapped_column(JSON)
    btts_yes: Mapped[float] = mapped_column(Float)
    btts_no: Mapped[float] = mapped_column(Float)

    # "2-1" -> probability, top N scores only
    correct_score_probabilities: Mapped[dict] = mapped_column(JSON)
    most_likely_score: Mapped[str] = mapped_column(String(16))
    most_likely_score_probability: Mapped[float] = mapped_column(Float)

    global_outcome_market: Mapped[str] = mapped_column(String(64))
    global_outcome_selection: Mapped[str] = mapped_column(String(64))
    global_outcome_probability: Mapped[float] = mapped_column(Float)

    confidence: Mapped[str] = mapped_column(String(16))
    data_quality_score: Mapped[float] = mapped_column(Float)
    model_agreement_score: Mapped[float] = mapped_column(Float)

    explanation: Mapped[dict] = mapped_column(JSON)
    model_breakdown: Mapped[dict] = mapped_column(JSON)


class LivePrediction(Base):
    __tablename__ = "live_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    minute: Mapped[int] = mapped_column(Integer)
    score_home: Mapped[int] = mapped_column(Integer)
    score_away: Mapped[int] = mapped_column(Integer)

    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)
    over_probabilities: Mapped[dict] = mapped_column(JSON)
    btts_yes: Mapped[float] = mapped_column(Float)

    global_outcome_market: Mapped[str] = mapped_column(String(64))
    global_outcome_selection: Mapped[str] = mapped_column(String(64))
    global_outcome_probability: Mapped[float] = mapped_column(Float)

    trigger_event: Mapped[str] = mapped_column(String(32))


class ModelMetric(Base):
    """Backtest / production monitoring results (spec sections 41, 43)."""

    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(32), index=True)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    split: Mapped[str] = mapped_column(String(16))  # train / validation / test / forward
    league: Mapped[str] = mapped_column(String(64))
    metric_name: Mapped[str] = mapped_column(String(32))
    metric_value: Mapped[float] = mapped_column(Float)

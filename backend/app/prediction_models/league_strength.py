"""How the domestic leagues compare to each other, fitted from results.

Elo ratings here are built one league at a time, every team starting from the
same base rating, and Elo updates are zero-sum: what the winner gains the
loser loses. So each league's ratings average exactly the base rating no
matter how strong the league actually is. A 1650 in Ligue 1 and a 1650 in the
Premier League are the same number describing different things, and nothing
inside either league's results can tell them apart -- the two sets of teams
never play each other there.

European results can. A Champions League tie between a Spanish and a German
club is the one observation that prices the two leagues against each other,
and five seasons of them is enough to fit one offset per league.

The model is deliberately the smallest thing that could work:

    effective rating = domestic Elo + offset(league)

One number per league, fitted by maximum likelihood through the same Davidson
model the Elo predictor already uses, so a calibrated cross-league match is
scored by exactly the same curve as a domestic one. Only *differences* between
offsets affect any prediction, which has two consequences worth knowing: the
offsets are centred on zero for readability and could be shifted together
without changing anything, and a domestic match is untouched, because both
clubs carry the same offset and it cancels.

What this deliberately does not do is calibrate against bookmaker odds. It
would be easier and probably more accurate, and it would make the question
"does this model beat the market" unanswerable, because the model would then
be a compression of the market.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.db.models import AppSetting, EloHistory, Match, Team
from app.prediction_models.elo import EUROPEAN_COMPETITIONS, elo_diff_to_1x2

# Machine-written state rather than an operator setting, so it lives in
# app_settings as a row without a registry entry -- the same place the odds
# backfill keeps its progress.
SETTING_KEY = "league_strength"

DEFAULT_HOME_ADVANTAGE = 60.0

# An offset larger than this is not a league difference, it is a fitting
# artefact from too few matches. The Premier League and the weakest league in
# this project are perhaps 150 Elo apart; 400 would be a professional side
# against an amateur one.
MAX_OFFSET = 250.0


@dataclass(frozen=True)
class Bridge:
    """One played match between clubs from two different domestic leagues.

    The unit of evidence. ``home_elo`` and ``away_elo`` are each club's
    domestic rating as it stood before kickoff, so nothing here knows the
    result it is being asked to explain.
    """

    date: dt.datetime
    home_league: str
    away_league: str
    home_elo: float
    away_elo: float
    outcome: str  # "H", "D" or "A"
    # False when the club had no Elo history before kickoff and fell back to
    # the base rating. Such a match says nothing about its league -- the
    # offset would be absorbing the club's own unknown strength -- so a fit
    # built mostly from these is measuring the wrong thing, and the caller has
    # to be able to see that rather than infer it.
    home_rated: bool = True
    away_rated: bool = True


@dataclass
class LeagueStrength:
    offsets: dict[str, float] = field(default_factory=dict)
    home_advantage: float = DEFAULT_HOME_ADVANTAGE
    matches: int = 0
    fitted_at: dt.datetime | None = None

    def adjust(self, rating: float, league: str) -> float:
        """A domestic rating on the common scale.

        An unknown league gets no offset rather than a guess: a league nobody
        has calibrated is better treated as average than as whatever the
        nearest name happens to be.
        """

        return rating + self.offsets.get(league, 0.0)

    def to_json(self) -> str:
        return json.dumps(
            {
                "offsets": {k: round(v, 2) for k, v in sorted(self.offsets.items())},
                "home_advantage": round(self.home_advantage, 2),
                "matches": self.matches,
                "fitted_at": self.fitted_at.isoformat() if self.fitted_at else None,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "LeagueStrength":
        data = json.loads(raw)
        fitted = data.get("fitted_at")
        return cls(
            offsets={str(k): float(v) for k, v in (data.get("offsets") or {}).items()},
            home_advantage=float(data.get("home_advantage") or DEFAULT_HOME_ADVANTAGE),
            matches=int(data.get("matches") or 0),
            fitted_at=dt.datetime.fromisoformat(fitted) if fitted else None,
        )

    @classmethod
    def load(cls, db: Session) -> "LeagueStrength":
        """The stored calibration, or an uncalibrated one.

        Never raises. An absent or unreadable row means predictions carry on
        with no offsets, which is what they did before this existed -- a
        broken calibration must not be able to take the site down.
        """

        row = db.get(AppSetting, SETTING_KEY)
        if row is None or not row.value:
            return cls()
        try:
            return cls.from_json(row.value)
        except (ValueError, TypeError, json.JSONDecodeError):
            return cls()

    def save(self, db: Session) -> None:
        row = db.get(AppSetting, SETTING_KEY)
        now = dt.datetime.utcnow()
        if row is None:
            db.add(AppSetting(key=SETTING_KEY, value=self.to_json(), updated_at=now))
        else:
            row.value = self.to_json()
            row.updated_at = now
        db.commit()


def collect_bridges(
    db: Session,
    *,
    competitions: frozenset[str] | set[str] | None = None,
    start_rating: float = 1500.0,
) -> list[Bridge]:
    """Every played cross-league match, with both clubs' ratings beforehand.

    Two queries regardless of how many matches there are. The obvious version
    -- ``get_rating_before`` per club per match -- is two queries per match,
    which is the pattern that already cost this project a month of database
    bandwidth once.
    """

    competitions = set(competitions or EUROPEAN_COMPETITIONS)

    home, away = aliased(Team), aliased(Team)
    rows = db.execute(
        select(
            Match.date,
            Match.home_team_id,
            Match.away_team_id,
            home.league,
            away.league,
            Match.home_score,
            Match.away_score,
        )
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .where(Match.league.in_(competitions), Match.home_score.is_not(None))
        .order_by(Match.date)
    ).all()

    crossing = [r for r in rows if r[3] != r[4]]
    if not crossing:
        return []

    team_ids = {r[1] for r in crossing} | {r[2] for r in crossing}

    # Domestic history only. European matches should never have written Elo
    # history -- rebuild_elo_history refuses them -- but filtering here means a
    # stray row from before that guard existed cannot quietly become evidence
    # about the very thing it came from.
    history: dict[int, tuple[list[dt.datetime], list[float]]] = {}
    snapshots = db.execute(
        select(EloHistory.team_id, EloHistory.date, EloHistory.rating_after)
        .join(Match, EloHistory.match_id == Match.id)
        .where(EloHistory.team_id.in_(team_ids), Match.league.not_in(competitions))
        .order_by(EloHistory.team_id, EloHistory.date)
    ).all()
    for team_id, date, rating in snapshots:
        dates, ratings = history.setdefault(team_id, ([], []))
        dates.append(date)
        ratings.append(rating)

    def rating_before(team_id: int, as_of: dt.datetime) -> tuple[float, bool]:
        entry = history.get(team_id)
        if not entry:
            return start_rating, False
        dates, ratings = entry
        index = bisect.bisect_left(dates, as_of)
        if not index:
            return start_rating, False
        return ratings[index - 1], True

    bridges = []
    for date, home_id, away_id, home_league, away_league, home_score, away_score in crossing:
        outcome = "H" if home_score > away_score else ("D" if home_score == away_score else "A")
        home_elo, home_rated = rating_before(home_id, date)
        away_elo, away_rated = rating_before(away_id, date)
        bridges.append(
            Bridge(
                date=date,
                home_league=home_league,
                away_league=away_league,
                home_elo=home_elo,
                away_elo=away_elo,
                outcome=outcome,
                home_rated=home_rated,
                away_rated=away_rated,
            )
        )
    return bridges


def log_loss(bridges: list[Bridge], offsets: dict[str, float], home_advantage: float) -> float:
    """Mean negative log probability of what actually happened.

    Log loss rather than accuracy, because the whole point of calibration is
    the confidence attached to a pick, not the pick.
    """

    if not bridges:
        return float("nan")

    total = 0.0
    for b in bridges:
        diff = (
            (b.home_elo + offsets.get(b.home_league, 0.0) + home_advantage)
            - (b.away_elo + offsets.get(b.away_league, 0.0))
        )
        probs = elo_diff_to_1x2(diff)
        p = {"H": probs.home_win, "D": probs.draw, "A": probs.away_win}[b.outcome]
        total -= math.log(max(p, 1e-12))
    return total / len(bridges)


def _leagues_in(bridges: list[Bridge]) -> list[str]:
    seen: dict[str, int] = {}
    for b in bridges:
        seen[b.home_league] = seen.get(b.home_league, 0) + 1
        seen[b.away_league] = seen.get(b.away_league, 0) + 1
    return [league for league, _ in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


def fit(
    bridges: list[Bridge],
    *,
    fit_home_advantage: bool = True,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> LeagueStrength:
    """Fit one offset per league by maximum likelihood.

    The most-represented league is held at zero during fitting, because only
    differences are identifiable -- adding 50 to every offset changes no
    prediction. Afterwards the offsets are centred so no single league is
    privileged in the output.
    """

    from scipy.optimize import minimize

    if not bridges:
        return LeagueStrength(home_advantage=home_advantage)

    leagues = _leagues_in(bridges)
    if len(leagues) < 2:
        return LeagueStrength(home_advantage=home_advantage, matches=len(bridges))

    free = leagues[1:]  # leagues[0] is the reference, pinned at zero

    def unpack(x) -> tuple[dict[str, float], float]:
        offsets = {league: float(x[i]) for i, league in enumerate(free)}
        offsets[leagues[0]] = 0.0
        ha = float(x[-1]) if fit_home_advantage else home_advantage
        return offsets, ha

    def objective(x) -> float:
        offsets, ha = unpack(x)
        return log_loss(bridges, offsets, ha)

    x0 = [0.0] * len(free) + ([home_advantage] if fit_home_advantage else [])
    bounds = [(-MAX_OFFSET, MAX_OFFSET)] * len(free) + ([(0.0, 150.0)] if fit_home_advantage else [])

    result = minimize(objective, x0, method="L-BFGS-B", bounds=bounds)
    offsets, ha = unpack(result.x)

    centre = sum(offsets.values()) / len(offsets)
    offsets = {league: value - centre for league, value in offsets.items()}

    return LeagueStrength(
        offsets=offsets,
        home_advantage=ha,
        matches=len(bridges),
        fitted_at=dt.datetime.utcnow(),
    )


def bootstrap_draws(bridges: list[Bridge], *, draws: int = 200, seed: int = 0) -> list[dict[str, float]]:
    """Refit on ``draws`` resamples of the matches, returning every fit.

    The draws are returned rather than summarised because the interesting
    quantity is not always a single offset. A prediction uses the *gap*
    between two leagues, and the interval on a difference cannot be recovered
    from two separate intervals -- the two offsets move together across
    resamples, so treating them as independent overstates the uncertainty.
    """

    import random as _random

    if not bridges:
        return []

    rng = _random.Random(seed)
    size = len(bridges)
    results = []
    for _ in range(draws):
        sample = [bridges[rng.randrange(size)] for _ in range(size)]
        try:
            results.append(fit(sample).offsets)
        except (ValueError, FloatingPointError):
            continue
    return results


def _percentile_range(values: list[float], interval: float) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    values = sorted(values)
    lower_q = (1.0 - interval) / 2.0
    last = len(values) - 1
    return (
        values[min(last, int(lower_q * len(values)))],
        values[min(last, int((1.0 - lower_q) * len(values)))],
    )


def pairwise_intervals(
    draws: list[dict[str, float]], *, interval: float = 0.90
) -> dict[tuple[str, str], tuple[float, float]]:
    """Intervals on the gap between each pair of leagues.

    This is the quantity that moves a prediction: a Champions League tie is
    scored on the difference between two offsets, never on either alone. A
    league whose own offset is indistinguishable from average can still sit a
    measurable distance from a specific other league.
    """

    leagues = sorted({league for drawn in draws for league in drawn})
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for i, a in enumerate(leagues):
        for b in leagues[i + 1:]:
            gaps = [d[a] - d[b] for d in draws if a in d and b in d]
            if gaps:
                out[(a, b)] = _percentile_range(gaps, interval)
    return out


def bootstrap_offsets(
    bridges: list[Bridge],
    *,
    draws: int = 200,
    seed: int = 0,
    interval: float = 0.90,
) -> dict[str, tuple[float, float]]:
    """A confidence interval per offset, by resampling the matches.

    Necessary, not decorative. Five seasons of European football gives a few
    hundred cross-league matches, and at that size the standard error on an
    offset is tens of Elo points -- the same order as the gap being measured.
    A point estimate of "+45" that could as easily be -10 must not be
    presented as a fact, and the only way to know which it is, is to resample.

    Each draw refits on a sample of the same size taken with replacement, and
    the interval is the empirical percentile range across draws. An interval
    straddling zero means the data cannot place that league relative to the
    others yet.
    """

    collected: dict[str, list[float]] = {}
    for drawn in bootstrap_draws(bridges, draws=draws, seed=seed):
        for league, value in drawn.items():
            collected.setdefault(league, []).append(value)
    return {league: _percentile_range(values, interval) for league, values in collected.items()}


def fit_home_advantage_only(bridges: list[Bridge], *, default: float = DEFAULT_HOME_ADVANTAGE) -> float:
    """Home advantage with every offset pinned at zero.

    The baseline the calibration has to beat. Without this the comparison
    would credit the offsets for whatever the fitter really learned about
    playing at home.
    """

    from scipy.optimize import minimize_scalar

    if not bridges:
        return default
    result = minimize_scalar(
        lambda ha: log_loss(bridges, {}, float(ha)), bounds=(0.0, 150.0), method="bounded"
    )
    return float(result.x)


@dataclass
class Validation:
    """Whether the offsets help on matches they were not fitted to."""

    fitted: LeagueStrength
    train_matches: int
    holdout_matches: int
    holdout_log_loss: float
    baseline_log_loss: float

    @property
    def improvement(self) -> float:
        """Positive means the calibration predicts held-out matches better."""

        return self.baseline_log_loss - self.holdout_log_loss

    @property
    def helps(self) -> bool:
        return self.holdout_matches > 0 and self.improvement > 0


def fit_and_validate(bridges: list[Bridge], holdout_from: dt.datetime) -> Validation:
    """Fit on matches before ``holdout_from``, score the ones after.

    The baseline refits home advantage on the same training matches with every
    offset held at zero, so the comparison isolates the offsets. Comparing
    against a hard-coded home advantage would credit the calibration for
    whatever the fitter learned about home advantage instead.
    """

    train = [b for b in bridges if b.date < holdout_from]
    holdout = [b for b in bridges if b.date >= holdout_from]

    fitted = fit(train)
    baseline_ha = fit_home_advantage_only(train)

    return Validation(
        fitted=fitted,
        train_matches=len(train),
        holdout_matches=len(holdout),
        holdout_log_loss=log_loss(holdout, fitted.offsets, fitted.home_advantage) if holdout else float("nan"),
        baseline_log_loss=log_loss(holdout, {}, baseline_ha) if holdout else float("nan"),
    )

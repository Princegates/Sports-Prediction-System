"""Does the calibration recover a league gap that is really there, and refuse
to find one that isn't?

Both halves matter. A fitter that finds structure in noise is worse than no
fitter, because its output looks exactly like the real thing -- four numbers
with plausible magnitudes -- and it would quietly skew every cross-league
prediction the site publishes.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from app.db.models import EloHistory, Match, Team
from app.prediction_models import elo
from app.prediction_models.league_strength import (
    Bridge,
    LeagueStrength,
    collect_bridges,
    fit,
    fit_and_validate,
    log_loss,
)

STRONG, WEAK = "Strong League", "Weak League"


def _sample(rng: random.Random, elo_diff: float) -> str:
    probs = elo.elo_diff_to_1x2(elo_diff)
    roll = rng.random()
    if roll < probs.home_win:
        return "H"
    if roll < probs.home_win + probs.draw:
        return "D"
    return "A"


def _synthetic(gap: float, count: int = 1200, seed: int = 7, home_advantage: float = 60.0) -> list[Bridge]:
    """Matches generated from a world where STRONG really is ``gap`` better.

    Every club's stored rating is 1500 -- the zero-sum-per-league situation
    this exists to correct -- while the results are produced as if the strong
    league's clubs were ``gap`` higher.
    """

    rng = random.Random(seed)
    start = dt.datetime(2021, 9, 1)
    bridges = []
    for i in range(count):
        home_is_strong = i % 2 == 0
        home_league, away_league = (STRONG, WEAK) if home_is_strong else (WEAK, STRONG)
        true_diff = (gap if home_is_strong else 0.0) + home_advantage - (0.0 if home_is_strong else gap)
        bridges.append(
            Bridge(
                date=start + dt.timedelta(days=i // 4),
                home_league=home_league,
                away_league=away_league,
                home_elo=1500.0,
                away_elo=1500.0,
                outcome=_sample(rng, true_diff),
            )
        )
    return bridges


def test_recovers_a_real_league_gap():
    bridges = _synthetic(gap=120.0)
    strength = fit(bridges)

    recovered = strength.offsets[STRONG] - strength.offsets[WEAK]
    assert recovered == pytest.approx(120.0, abs=35.0), strength.offsets
    assert strength.home_advantage == pytest.approx(60.0, abs=35.0)


def test_offsets_are_centred_so_no_league_is_the_yardstick():
    strength = fit(_synthetic(gap=120.0))
    assert sum(strength.offsets.values()) == pytest.approx(0.0, abs=1e-6)


def test_finds_almost_nothing_when_the_leagues_are_equal():
    """The null case. Two leagues of identical strength must not produce an
    offset large enough to move a prediction."""

    strength = fit(_synthetic(gap=0.0))
    gap = abs(strength.offsets[STRONG] - strength.offsets[WEAK])
    assert gap < 30.0, f"invented a {gap:.0f}-point gap out of noise: {strength.offsets}"


def test_equal_leagues_show_no_gain_on_held_out_matches():
    bridges = _synthetic(gap=0.0)
    result = fit_and_validate(bridges, holdout_from=dt.datetime(2021, 11, 1))

    assert result.holdout_matches > 0
    # Fitting noise can only cost accuracy out of sample; a claimed gain here
    # would mean the validation split is leaking.
    assert result.improvement < 0.01, result


def test_a_real_gap_shows_a_gain_on_held_out_matches():
    bridges = _synthetic(gap=120.0)
    result = fit_and_validate(bridges, holdout_from=dt.datetime(2021, 11, 1))

    assert result.helps, result
    assert result.improvement > 0.01, result


def test_a_domestic_match_is_untouched_by_calibration():
    """Both clubs carry the same offset, so it cancels exactly. This is why
    calibration can be switched on without re-examining domestic accuracy."""

    strength = LeagueStrength(offsets={STRONG: 80.0, WEAK: -80.0})
    home = strength.adjust(1600.0, STRONG)
    away = strength.adjust(1500.0, STRONG)
    assert home - away == 100.0


def test_an_uncalibrated_league_gets_no_offset_rather_than_a_guess():
    strength = LeagueStrength(offsets={STRONG: 80.0})
    assert strength.adjust(1500.0, "Ghana Premier League") == 1500.0


def test_stored_calibration_survives_a_round_trip(db_session):
    original = fit(_synthetic(gap=120.0))
    original.save(db_session)

    loaded = LeagueStrength.load(db_session)
    assert loaded.offsets.keys() == original.offsets.keys()
    for league, value in original.offsets.items():
        assert loaded.offsets[league] == pytest.approx(value, abs=0.01)
    assert loaded.matches == original.matches


def test_unreadable_calibration_degrades_to_none(db_session):
    """A corrupt row must not be able to take predictions down with it."""

    from app.db.models import AppSetting
    from app.prediction_models.league_strength import SETTING_KEY

    db_session.add(AppSetting(key=SETTING_KEY, value="{not json", updated_at=dt.datetime.utcnow()))
    db_session.commit()

    assert LeagueStrength.load(db_session).offsets == {}


def test_european_replay_is_refused(db_session):
    """Replaying a European competition as a league would restart every club
    at 1500 and overwrite the domestic rating that get_rating_before reads."""

    with pytest.raises(ValueError, match="domestic"):
        elo.rebuild_elo_history(db_session, "UEFA Champions League")


def test_bridges_use_the_rating_before_kickoff(db_session):
    spain = Team(name="Real Madrid CF", league="Spanish La Liga", aliases=[])
    germany = Team(name="FC Bayern München", league="German Bundesliga", aliases=[])
    db_session.add_all([spain, germany])
    db_session.commit()

    # A domestic match each, then the European tie between them.
    domestic = Match(
        league="Spanish La Liga", season="2024", date=dt.datetime(2024, 8, 1),
        home_team_id=spain.id, away_team_id=spain.id, status="FINISHED",
        home_score=1, away_score=0,
    )
    db_session.add(domestic)
    db_session.commit()
    db_session.add(EloHistory(team_id=spain.id, match_id=domestic.id, date=dt.datetime(2024, 8, 1),
                              rating_before=1500.0, rating_after=1720.0))
    tie = Match(
        league="UEFA Champions League", season="2024", date=dt.datetime(2024, 10, 1),
        home_team_id=spain.id, away_team_id=germany.id, status="FINISHED",
        home_score=2, away_score=1,
    )
    db_session.add(tie)
    db_session.commit()

    [bridge] = collect_bridges(db_session)
    assert bridge.home_elo == 1720.0        # the snapshot from before the tie
    assert bridge.away_elo == 1500.0        # no history, so the base rating
    assert bridge.home_rated and not bridge.away_rated
    assert bridge.outcome == "H"
    assert (bridge.home_league, bridge.away_league) == ("Spanish La Liga", "German Bundesliga")


def test_same_league_ties_are_not_evidence(db_session):
    """Two Spanish clubs meeting in Europe says nothing about how Spain
    compares with anywhere else."""

    a = Team(name="Real Madrid CF", league="Spanish La Liga", aliases=[])
    b = Team(name="FC Barcelona", league="Spanish La Liga", aliases=[])
    db_session.add_all([a, b])
    db_session.commit()
    db_session.add(Match(
        league="UEFA Champions League", season="2024", date=dt.datetime(2024, 10, 1),
        home_team_id=a.id, away_team_id=b.id, status="FINISHED", home_score=1, away_score=1,
    ))
    db_session.commit()

    assert collect_bridges(db_session) == []


def test_log_loss_rewards_the_better_calibration():
    bridges = _synthetic(gap=120.0, count=400, seed=11)
    fitted = fit(bridges)

    assert log_loss(bridges, fitted.offsets, fitted.home_advantage) < log_loss(bridges, {}, 60.0)


def test_the_interval_straddles_zero_when_there_is_no_gap():
    """The honest answer for two equal leagues is "cannot tell", and that has
    to be visible in the output rather than inferred from a small number."""

    from app.prediction_models.league_strength import bootstrap_offsets

    bounds = bootstrap_offsets(_synthetic(gap=0.0, count=400), draws=80)
    low, high = bounds[STRONG]
    assert low < 0 < high, bounds


def test_the_interval_excludes_zero_for_a_real_gap():
    from app.prediction_models.league_strength import bootstrap_offsets

    bounds = bootstrap_offsets(_synthetic(gap=150.0, count=400), draws=80)
    low, _ = bounds[STRONG]
    assert low > 0, bounds

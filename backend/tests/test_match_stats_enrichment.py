"""Tests for attaching football-data.co.uk statistics to existing matches.

The two sources spell clubs differently and date fixtures slightly
differently, so reconciliation is the whole job here. The property that
matters most is negative: a CSV row that cannot be confidently resolved must
be skipped and reported, never attached to a nearby match. Silently teaching
the model from another club's shot counts would be worse than having no shot
counts at all.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.data import ingest
from app.data.providers.football_data_co_uk import RawMatch
from app.data.team_matching import name_similarity
from app.db.models import Match, Team

LEAGUE = "English Premier League"


@pytest.fixture()
def seeded(db_session):
    """Matches spelled the openfootball way -- full club names with suffixes."""

    names = [
        "Manchester United FC", "Manchester City FC", "Nottingham Forest FC",
        "Wolverhampton Wanderers FC", "Tottenham Hotspur FC", "Arsenal FC",
    ]
    teams = {}
    for name in names:
        team = Team(name=name, league=LEAGUE, aliases=[])
        db_session.add(team)
        teams[name] = team
    db_session.commit()
    for t in teams.values():
        db_session.refresh(t)

    base = dt.datetime(2025, 3, 1, 15, 0)
    pairings = [
        ("Manchester United FC", "Manchester City FC"),
        ("Nottingham Forest FC", "Wolverhampton Wanderers FC"),
        ("Tottenham Hotspur FC", "Arsenal FC"),
    ]
    matches = {}
    for i, (home, away) in enumerate(pairings):
        m = Match(
            league=LEAGUE, season="2024-25", date=base + dt.timedelta(days=i),
            home_team_id=teams[home].id, away_team_id=teams[away].id,
            home_score=1, away_score=0, status="FINISHED", source="openfootball/football.json",
        )
        db_session.add(m)
        matches[(home, away)] = m
    db_session.commit()
    for m in matches.values():
        db_session.refresh(m)
    return {"teams": teams, "matches": matches, "base": base}


def _raw(home, away, date, **stats) -> RawMatch:
    defaults = dict(
        league_code="E0", season="2425", date=date, home_team=home, away_team=away,
        home_score=1, away_score=0, ht_home_score=0, ht_away_score=0,
    )
    return RawMatch(**{**defaults, **stats})


def _patch_fetch(monkeypatch, rows):
    monkeypatch.setattr(ingest.fdcu, "fetch_season", lambda code, season, timeout=30: rows)


# --- Name reconciliation --------------------------------------------------


def test_abbreviated_names_resolve_to_the_right_match(db_session, seeded, monkeypatch):
    """football-data.co.uk abbreviates. "Man United" must find
    "Manchester United FC" -- and specifically not "Manchester City FC"."""

    _patch_fetch(monkeypatch, [
        _raw("Man United", "Man City", seeded["base"], home_shots=14, away_shots=9, referee="M Oliver"),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.matched == 1
    assert report.unmatched == []

    match = seeded["matches"][("Manchester United FC", "Manchester City FC")]
    db_session.refresh(match)
    assert match.home_shots == 14
    assert match.away_shots == 9
    assert match.referee == "M Oliver"


def test_non_prefix_abbreviations_resolve(db_session, seeded, monkeypatch):
    """"Wolves" is not a prefix of "Wolverhampton" and "Nott'm" is not a
    prefix of "Nottingham" -- these only work via the explicit alias map."""

    _patch_fetch(monkeypatch, [
        _raw("Nott'm Forest", "Wolves", seeded["base"] + dt.timedelta(days=1), home_shots=11, away_shots=13),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.matched == 1, f"unmatched: {report.unmatched}"
    match = seeded["matches"][("Nottingham Forest FC", "Wolverhampton Wanderers FC")]
    db_session.refresh(match)
    assert match.home_shots == 11


def test_unknown_club_is_reported_not_guessed(db_session, seeded, monkeypatch):
    """A club with no counterpart must be skipped and named, so it surfaces
    as an alias to add rather than a quietly lower match rate."""

    _patch_fetch(monkeypatch, [
        _raw("Some Unknown FC", "Another Unknown", seeded["base"], home_shots=20),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.matched == 0
    assert len(report.unmatched) == 1
    assert "Some Unknown FC" in report.unmatched[0]

    # Nothing was attached to the real fixture on that date.
    match = seeded["matches"][("Manchester United FC", "Manchester City FC")]
    db_session.refresh(match)
    assert match.home_shots is None


def test_reversed_fixture_does_not_match(db_session, seeded, monkeypatch):
    """City at home to United is a different match from United at home to
    City. Home and away must not be interchangeable."""

    _patch_fetch(monkeypatch, [
        _raw("Man City", "Man United", seeded["base"], home_shots=20, away_shots=4),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.matched == 0, "matched a fixture with the sides swapped"
    match = seeded["matches"][("Manchester United FC", "Manchester City FC")]
    db_session.refresh(match)
    assert match.home_shots is None


# --- Date tolerance -------------------------------------------------------


def test_one_day_date_difference_still_matches(db_session, seeded, monkeypatch):
    """Sources disagree by a day for kickoffs either side of midnight UTC."""

    _patch_fetch(monkeypatch, [
        _raw("Man United", "Man City", seeded["base"] - dt.timedelta(days=1), home_shots=7),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.matched == 1
    match = seeded["matches"][("Manchester United FC", "Manchester City FC")]
    db_session.refresh(match)
    assert match.home_shots == 7


def test_distant_date_does_not_match(db_session, seeded, monkeypatch):
    """The same two clubs meet twice a season. A fixture a month away is the
    reverse tie, not this one."""

    _patch_fetch(monkeypatch, [
        _raw("Man United", "Man City", seeded["base"] + dt.timedelta(days=30), home_shots=7),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.matched == 0
    match = seeded["matches"][("Manchester United FC", "Manchester City FC")]
    db_session.refresh(match)
    assert match.home_shots is None


# --- Data handling --------------------------------------------------------


def test_missing_stats_leave_existing_values_alone(db_session, seeded, monkeypatch):
    """Older seasons omit columns. A None must not overwrite a value that is
    already there."""

    match = seeded["matches"][("Manchester United FC", "Manchester City FC")]
    match.home_shots = 12
    db_session.commit()

    _patch_fetch(monkeypatch, [
        _raw("Man United", "Man City", seeded["base"], home_shots=None, away_corners=6),
    ])

    ingest.enrich_match_stats(db_session, "E0", "2425")

    db_session.refresh(match)
    assert match.home_shots == 12, "a missing stat erased an existing one"
    assert match.away_corners == 6


def test_enrichment_never_creates_matches(db_session, seeded, monkeypatch):
    """This path reconciles; it must not insert. A CSV row with no
    counterpart is a reporting problem, not a new fixture."""

    before = db_session.query(Match).count()
    _patch_fetch(monkeypatch, [
        _raw("Some Unknown FC", "Another Unknown", seeded["base"]),
        _raw("Man United", "Man City", seeded["base"], home_shots=5),
    ])

    ingest.enrich_match_stats(db_session, "E0", "2425")

    assert db_session.query(Match).count() == before


def test_rerunning_is_idempotent(db_session, seeded, monkeypatch):
    _patch_fetch(monkeypatch, [
        _raw("Man United", "Man City", seeded["base"], home_shots=14, away_shots=9),
    ])

    first = ingest.enrich_match_stats(db_session, "E0", "2425")
    second = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert first.updated == 1
    assert second.updated == 0, "re-running rewrote unchanged values"
    assert second.matched == 1


def test_match_rate_is_reported(db_session, seeded, monkeypatch):
    _patch_fetch(monkeypatch, [
        _raw("Man United", "Man City", seeded["base"], home_shots=5),
        _raw("Unknown A", "Unknown B", seeded["base"]),
    ])

    report = ingest.enrich_match_stats(db_session, "E0", "2425")

    assert report.csv_rows == 2
    assert report.matched == 1
    assert report.match_rate == pytest.approx(0.5)


# --- The similarity function itself ---------------------------------------


@pytest.mark.parametrize("a,b", [
    ("Man United", "Manchester United FC"),
    ("Wolves", "Wolverhampton Wanderers FC"),
    ("Nott'm Forest", "Nottingham Forest FC"),
    ("Ath Madrid", "Atlético Madrid"),
    ("Paris SG", "Paris Saint-Germain FC"),
    ("Ein Frankfurt", "Eintracht Frankfurt"),
    ("Inter", "Internazionale Milano"),
    ("Betis", "Real Betis Balompié"),
])
def test_genuine_pairs_match_fully(a, b):
    assert name_similarity(a, b) == 1.0


@pytest.mark.parametrize("a,b", [
    ("Man United", "Manchester City FC"),
    ("Sheffield United", "Sheffield Wednesday FC"),
    ("Real Madrid", "Real Sociedad"),
    ("Ath Madrid", "Ath Bilbao"),
    ("Leeds United", "Leicester City FC"),
])
def test_different_clubs_never_match_fully(a, b):
    """These are the dangerous ones -- clubs that share a word. A full score
    here would attach one club's statistics to another."""

    assert name_similarity(a, b) < 1.0

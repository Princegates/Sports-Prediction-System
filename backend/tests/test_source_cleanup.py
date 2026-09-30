"""Retiring what the free fixture feeds left behind -- see
app/data/source_cleanup.py.

The fixtures here are the real pairs a member saw listed twice on the
Markets page: openfootball's "Sporting Clube de Braga vs Sporting Clube de
Portugal" an hour after API-Football's "Sporting Braga vs Sporting CP", and
"Málaga CF vs RCD Espanyol de Barcelona" two hours after "Málaga CF vs
Espanyol" -- local kickoff times stored as UTC, under a second spelling of
each club.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.data.api_football_ingest import TeamIndex, import_fixtures
from app.data.source_cleanup import retire_free_fixtures
from app.db.models import AdminPick, FeaturedPick, Match, Prediction, Team
from tests.test_api_football import FakeClient

PT = "Portuguese Primeira Liga"
ES = "Spanish La Liga"
KICKOFF = dt.datetime.utcnow().replace(microsecond=0, second=0, minute=15) + dt.timedelta(days=9)


def _team(db, name, league):
    team = Team(name=name, league=league, aliases=[])
    db.add(team)
    db.flush()
    return team


def _match(db, league, home, away, date, *, api_id=None, score=None, source="openfootball/football.json"):
    m = Match(
        league=league, season="2026", date=date, home_team_id=home.id, away_team_id=away.id,
        status="FINISHED" if score else "SCHEDULED",
        home_score=score[0] if score else None, away_score=score[1] if score else None,
        api_fixture_id=api_id, source=source,
    )
    db.add(m)
    db.flush()
    return m


def _history(db, league, club, opponent, n=3):
    """Past results, so the long-named row is the one with history."""
    for i in range(n):
        _match(db, league, club, opponent, KICKOFF - dt.timedelta(days=400 + 7 * i), score=(1, 0))


@pytest.fixture()
def portugal(db_session):
    db = db_session
    braga_long = _team(db, "Sporting Clube de Braga", PT)
    sporting_long = _team(db, "Sporting Clube de Portugal", PT)
    braga_api = _team(db, "Sporting Braga", PT)
    sporting_api = _team(db, "Sporting CP", PT)
    _history(db, PT, braga_long, sporting_long)

    api = _match(db, PT, braga_api, sporting_api, KICKOFF, api_id=5001, source="api-football")
    free = _match(db, PT, braga_long, sporting_long, KICKOFF + dt.timedelta(hours=1))
    db.add(Prediction(match_id=free.id, model_version="t", home_win=0.4, draw=0.3, away_win=0.3,
                      over_probabilities={}, btts_yes=0.5, btts_no=0.5, correct_score_probabilities={},
                      most_likely_score="1-1", most_likely_score_probability=0.1,
                      global_outcome_market="Match Result", global_outcome_selection="Home Win",
                      global_outcome_probability=0.4, confidence="MEDIUM", data_quality_score=1.0,
                      model_agreement_score=0.9, explanation={}, model_breakdown={}))
    db.commit()
    return {"braga_long": braga_long, "sporting_long": sporting_long,
            "braga_api": braga_api, "sporting_api": sporting_api, "api": api, "free": free}


def test_the_braga_sporting_pair_becomes_one_fixture_between_one_pair_of_clubs(db_session, portugal):
    api_id, free_id = portugal["api"].id, portugal["free"].id

    report = retire_free_fixtures(db_session, PT)

    assert report.fixtures_merged == 1
    upcoming = db_session.query(Match).filter(Match.league == PT, Match.status == "SCHEDULED").all()
    assert [m.id for m in upcoming] == [api_id], "API-Football's row is the one kept"
    assert upcoming[0].date == KICKOFF, "with API-Football's (correct, UTC) kickoff"
    assert upcoming[0].api_fixture_id == 5001
    assert db_session.get(Match, free_id) is None
    assert db_session.query(Prediction).filter_by(match_id=free_id).count() == 0

    # "Sporting CP" shares no spelling with "Sporting Clube de Portugal" --
    # it's merged because Braga, on the other side of the same match, is
    # plainly Braga.
    clubs = {t.name: t for t in db_session.query(Team).filter_by(league=PT)}
    assert set(clubs) == {"Sporting Braga", "Sporting CP"}
    assert "Sporting Clube de Braga" in clubs["Sporting Braga"].aliases
    assert "Sporting Clube de Portugal" in clubs["Sporting CP"].aliases

    # The history came with them: the long-named rows held the results, so
    # those rows survived (under API-Football's spelling).
    assert clubs["Sporting Braga"].id == portugal["braga_long"].id
    history = db_session.query(Match).filter(Match.league == PT, Match.status == "FINISHED").all()
    assert len(history) == 3
    assert all(m.home_team_id == clubs["Sporting Braga"].id for m in history)
    assert upcoming[0].home_team_id == clubs["Sporting Braga"].id
    assert upcoming[0].away_team_id == clubs["Sporting CP"].id


def test_malaga_espanyol_merges_the_espanyol_rows_through_the_shared_malaga(db_session):
    db = db_session
    malaga = _team(db, "Málaga CF", ES)
    espanyol_long = _team(db, "RCD Espanyol de Barcelona", ES)
    espanyol_api = _team(db, "Espanyol", ES)
    _history(db, ES, espanyol_long, malaga)
    api = _match(db, ES, malaga, espanyol_api, KICKOFF, api_id=7001, source="api-football")
    _match(db, ES, malaga, espanyol_long, KICKOFF + dt.timedelta(hours=2))
    db.commit()

    report = retire_free_fixtures(db, ES)

    assert report.fixtures_merged == 1
    assert [(old, new) for old, new in report.teams_merged] == [("RCD Espanyol de Barcelona", "Espanyol")]
    assert {t.name for t in db.query(Team).filter_by(league=ES)} == {"Málaga CF", "Espanyol"}
    assert db.query(Match).filter_by(league=ES, status="SCHEDULED").one().id == api.id


def test_a_dry_run_reports_and_writes_nothing(db_session, portugal):
    before = (db_session.query(Match).count(), db_session.query(Team).count())

    report = retire_free_fixtures(db_session, PT, apply=False)

    assert report.fixtures_merged == 1 and len(report.teams_merged) == 2
    db_session.expire_all()
    assert (db_session.query(Match).count(), db_session.query(Team).count()) == before


def test_what_people_chose_follows_the_match(db_session, portugal):
    free_id, api_id = portugal["free"].id, portugal["api"].id
    db_session.add(FeaturedPick(match_id=free_id, market="Match Result", selection="Home Win",
                                expires_at=KICKOFF + dt.timedelta(days=2)))
    db_session.add(AdminPick(legs=[{"match_id": free_id, "market": "Match Result", "selection": "Draw"}],
                             expires_at=KICKOFF + dt.timedelta(days=2)))
    db_session.commit()

    retire_free_fixtures(db_session, PT)

    assert db_session.query(FeaturedPick).one().match_id == api_id
    assert db_session.query(AdminPick).one().legs[0]["match_id"] == api_id


def test_a_free_fixture_api_football_does_not_list_is_removed_but_a_result_is_kept(db_session, portugal):
    db = db_session
    benfica, porto = _team(db, "Sport Lisboa e Benfica", PT), _team(db, "Futebol Clube do Porto", PT)
    # API-Football's calendar for the league starts a week before KICKOFF.
    _match(db, PT, portugal["sporting_api"], portugal["braga_api"], KICKOFF - dt.timedelta(days=7),
           api_id=5000, source="api-football")
    ghost = _match(db, PT, benfica, porto, KICKOFF - dt.timedelta(days=2))
    result = _match(db, PT, porto, benfica, KICKOFF - dt.timedelta(days=3), score=(2, 2))
    later = _match(db, PT, benfica, porto, KICKOFF + dt.timedelta(days=60))
    ghost_id, result_id, later_id = ghost.id, result.id, later.id
    db.commit()

    report = retire_free_fixtures(db, PT)

    assert report.fixtures_removed == 1
    assert db.get(Match, ghost_id) is None
    assert db.get(Match, result_id) is not None, "a result is history, never deleted on an inference"
    assert db.get(Match, later_id) is not None, "past API-Football's calendar, so not provably absent"


def test_unrelated_matches_at_the_same_time_are_not_paired(db_session, portugal):
    """Benfica vs Porto kicking off alongside Braga vs Sporting shares no club
    with it -- it must not be folded in, and no club may be merged over it."""

    db = db_session
    benfica, porto = _team(db, "Benfica", PT), _team(db, "FC Porto", PT)
    other_api = _match(db, PT, benfica, porto, KICKOFF, api_id=5002, source="api-football")
    db.commit()

    retire_free_fixtures(db, PT)

    assert db.get(Match, other_api.id).home_team_id == benfica.id
    assert {t.name for t in db.query(Team).filter_by(league=PT)} == {
        "Sporting Braga", "Sporting CP", "Benfica", "FC Porto",
    }


def test_a_league_api_football_never_returned_is_left_alone(db_session):
    db = db_session
    home, away = _team(db, "Arsenal FC", "English Premier League"), _team(db, "Chelsea FC", "English Premier League")
    _match(db, "English Premier League", home, away, KICKOFF)
    db.commit()

    report = retire_free_fixtures(db, "English Premier League")

    assert not report.changed
    assert db.query(Match).count() == 1


def test_finished_duplicates_with_the_same_score_fold_together(db_session, portugal):
    """A result both feeds stored would be learned twice by the model."""

    db = db_session
    played = KICKOFF - dt.timedelta(days=5)
    _match(db, PT, portugal["sporting_api"], portugal["braga_api"], played, api_id=4999, score=(3, 1),
           source="api-football")
    _match(db, PT, portugal["sporting_long"], portugal["braga_long"], played + dt.timedelta(hours=1), score=(3, 1))
    db.commit()

    retire_free_fixtures(db, PT)

    recent = db.query(Match).filter(Match.league == PT, Match.date >= played - dt.timedelta(days=1),
                                    Match.status == "FINISHED").all()
    assert len(recent) == 1 and recent[0].api_fixture_id == 4999


def test_after_the_merge_every_spelling_resolves_to_the_club_with_history(db_session, portugal):
    retire_free_fixtures(db_session, PT)
    survivor = db_session.query(Team).filter_by(league=PT, name="Sporting CP").one()

    index = TeamIndex(db_session)
    assert index.resolve("Sporting CP", prefer_league=PT).id == survivor.id
    assert index.resolve("Sporting Clube de Portugal", prefer_league=PT).id == survivor.id


def test_the_importer_claims_a_stored_result_instead_of_storing_it_twice(db_session):
    """openfootball stored a result at local time; API-Football reports the
    same match an hour earlier in UTC. One row, now carrying the provider id."""

    db = db_session
    braga, sporting = _team(db, "Sporting Braga", PT), _team(db, "Sporting CP", PT)
    played = dt.datetime(2026, 9, 20, 20, 15)
    stored = _match(db, PT, braga, sporting, played, score=(1, 1))
    db.commit()

    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 6001, "date": "2026-09-20T19:15:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"name": "Sporting Braga"}, "away": {"name": "Sporting CP"}},
            "goals": {"home": 1, "away": 1},
        }]
    })
    report = import_fixtures(db, client, league_id=94, season=2026)

    assert report.inserted == 0
    assert db.query(Match).count() == 1
    db.refresh(stored)
    assert stored.api_fixture_id == 6001
    assert stored.date == dt.datetime(2026, 9, 20, 19, 15)


def test_the_importer_does_not_claim_a_result_with_a_different_score(db_session):
    db = db_session
    braga, sporting = _team(db, "Sporting Braga", PT), _team(db, "Sporting CP", PT)
    _match(db, PT, braga, sporting, dt.datetime(2026, 9, 20, 20, 15), score=(2, 0))
    db.commit()

    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 6002, "date": "2026-09-20T19:15:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"name": "Sporting Braga"}, "away": {"name": "Sporting CP"}},
            "goals": {"home": 1, "away": 1},
        }]
    })
    report = import_fixtures(db, client, league_id=94, season=2026)

    assert report.inserted == 1

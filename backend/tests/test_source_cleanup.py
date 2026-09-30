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


# --- What the production inspection found ---------------------------------


def test_an_old_import_that_put_atleticos_match_under_real_madrid_is_folded_away(db_session):
    """Fixture 1570334 was stored twice: once under Real Madrid by an import
    from before the Madrid name fix, once correctly under Atletico. The
    importer's current club matching decides, and the wrong row goes."""

    db = db_session
    real, atleti, malaga = (_team(db, n, ES) for n in ("Real Madrid CF", "Club Atlético de Madrid", "Málaga CF"))
    wrong = _match(db, ES, real, malaga, dt.datetime(2026, 8, 19, 19), api_id=1570334, score=(2, 0),
                   source="football-data.co.uk")
    right = _match(db, ES, atleti, malaga, dt.datetime(2026, 8, 19, 21), score=(2, 0))
    pending_wrong = _match(db, ES, malaga, real, KICKOFF, api_id=1570416, source="football-data.co.uk")
    db.add(FeaturedPick(match_id=pending_wrong.id, market="Match Result", selection="Draw",
                        expires_at=KICKOFF + dt.timedelta(days=2)))
    wrong_id, right_id, pending_wrong_id = wrong.id, right.id, pending_wrong.id
    db.commit()

    client = FakeClient({"fixtures": [
        {"fixture": {"id": 1570334, "date": "2026-08-19T19:00:00+00:00", "status": {"short": "FT"}},
         "teams": {"home": {"name": "Atletico Madrid"}, "away": {"name": "Malaga"}},
         "goals": {"home": 2, "away": 0}},
        {"fixture": {"id": 1570416, "date": KICKOFF.strftime("%Y-%m-%dT%H:%M:00+00:00"), "status": {"short": "NS"}},
         "teams": {"home": {"name": "Malaga"}, "away": {"name": "Atletico Madrid"}},
         "goals": {"home": None, "away": None}},
    ]})
    report = import_fixtures(db, client, league_id=140, season=2026)

    assert report.misattached == 2
    assert db.get(Match, wrong_id) is None and db.get(Match, pending_wrong_id) is None
    kept = db.get(Match, right_id)
    assert kept.api_fixture_id == 1570334 and kept.home_team_id == atleti.id
    upcoming = db.query(Match).filter_by(api_fixture_id=1570416).one()
    assert upcoming.away_team_id == atleti.id
    assert db.query(FeaturedPick).one().match_id == upcoming.id, "the pick follows the real fixture"
    assert db.query(Match).filter(Match.home_team_id == real.id).count() == 0
    assert db.query(Match).filter(Match.away_team_id == real.id).count() == 0


def test_a_test_edited_copy_of_a_fixture_folds_into_api_footballs_row_and_its_score(db_session):
    """Spurs vs Villa: API-Football says 2-3; an openfootball copy an hour
    later had been set to 0-0 by Live-tab test events."""

    db = db_session
    epl = "English Premier League"
    spurs, villa = _team(db, "Tottenham Hotspur FC", epl), _team(db, "Aston Villa FC", epl)
    api = _match(db, epl, spurs, villa, dt.datetime(2026, 9, 19, 11, 30), api_id=1557416, score=(2, 3))
    copy = _match(db, epl, spurs, villa, dt.datetime(2026, 9, 19, 12, 30), score=(0, 0))
    api_id, copy_id = api.id, copy.id
    db.commit()

    report = retire_free_fixtures(db, epl)

    assert report.fixtures_merged == 1
    assert db.get(Match, copy_id) is None
    kept = db.get(Match, api_id)
    assert (kept.home_score, kept.away_score) == (2, 3)


def test_free_feed_copies_of_each_other_keep_one_but_a_disputed_score_keeps_both(db_session):
    db = db_session
    real, sociedad, barca = (_team(db, n, ES) for n in ("Real Madrid CF", "Real Sociedad de Fútbol", "FC Barcelona"))
    _match(db, ES, barca, sociedad, dt.datetime(2026, 8, 1, 19), api_id=1, score=(1, 1))  # API coverage starts
    first = _match(db, ES, real, sociedad, dt.datetime(2026, 8, 26, 21), score=(4, 1))
    second = _match(db, ES, real, sociedad, dt.datetime(2026, 8, 26, 21), score=(4, 1))
    a = _match(db, ES, barca, real, dt.datetime(2026, 9, 1, 19), score=(1, 0))
    b = _match(db, ES, barca, real, dt.datetime(2026, 9, 1, 21), score=(2, 2))
    ids = first.id, second.id, a.id, b.id
    db.commit()

    retire_free_fixtures(db, ES)

    assert db.get(Match, ids[0]) is not None and db.get(Match, ids[1]) is None
    assert db.get(Match, ids[2]) is not None and db.get(Match, ids[3]) is not None


def test_a_mislabeled_row_never_merges_two_real_clubs(db_session):
    """An openfootball row with Atletico's match stored under Real Madrid
    pairs with API-Football's "Atletico vs Barcelona" through Barcelona. That
    must not "prove" Real Madrid and Atletico are one club -- they've played
    each other. The mislabeled row still goes."""

    db = db_session
    real, atleti, barca = (_team(db, n, ES) for n in ("Real Madrid CF", "Club Atlético de Madrid", "FC Barcelona"))
    _match(db, ES, real, atleti, dt.datetime(2025, 2, 8, 20), score=(1, 1))  # a derby
    api = _match(db, ES, atleti, barca, dt.datetime(2026, 9, 15, 17), api_id=1570390, score=(2, 1))
    mislabeled = _match(db, ES, real, barca, dt.datetime(2026, 9, 15, 19), score=(2, 1))
    api_id, mislabeled_id = api.id, mislabeled.id
    db.commit()

    report = retire_free_fixtures(db, ES)

    assert not report.teams_merged
    assert any("different clubs" in c and "played each other" in c for c in report.conflicts)
    assert {t.name for t in db.query(Team).filter_by(league=ES)} == {
        "Real Madrid CF", "Club Atlético de Madrid", "FC Barcelona",
    }
    assert db.get(Match, mislabeled_id) is None
    assert db.get(Match, api_id).home_team_id == atleti.id


def test_inter_and_ac_milan_are_never_merged(db_session):
    """Two clubs from one city. An old import stored AC Milan's matches under
    Inter; cleaning that up must remove the mislabeled copies and leave both
    clubs exactly as they are."""

    db = db_session
    it = "Italian Serie A"
    inter, milan, torino = (_team(db, n, it) for n in ("FC Internazionale Milano", "AC Milan", "Torino FC"))
    _match(db, it, inter, milan, dt.datetime(2025, 9, 21, 18, 45), score=(2, 1))  # the derby
    real_inter = _match(db, it, inter, torino, dt.datetime(2026, 8, 30, 18, 45), api_id=1550099, score=(1, 0))
    wrong = _match(db, it, torino, inter, dt.datetime(2026, 8, 23, 18, 45), api_id=1550094, score=(1, 2),
                   source="football-data.co.uk")
    right = _match(db, it, torino, milan, dt.datetime(2026, 8, 23, 20, 45), score=(1, 2))
    mislabeled_copy = _match(db, it, torino, inter, dt.datetime(2026, 8, 23, 20, 45), score=(1, 2))
    ids = real_inter.id, wrong.id, right.id, mislabeled_copy.id
    db.commit()

    client = FakeClient({"fixtures": [
        {"fixture": {"id": 1550094, "date": "2026-08-23T18:45:00+00:00", "status": {"short": "FT"}},
         "teams": {"home": {"name": "Torino"}, "away": {"name": "AC Milan"}},
         "goals": {"home": 1, "away": 2}},
        {"fixture": {"id": 1550099, "date": "2026-08-30T18:45:00+00:00", "status": {"short": "FT"}},
         "teams": {"home": {"name": "Inter"}, "away": {"name": "Torino"}},
         "goals": {"home": 1, "away": 0}},
    ]})
    import_fixtures(db, client, league_id=135, season=2026)
    report = retire_free_fixtures(db, it)

    assert not report.teams_merged
    clubs = {t.name: t.id for t in db.query(Team).filter_by(league=it)}
    assert clubs == {"FC Internazionale Milano": inter.id, "AC Milan": milan.id, "Torino FC": torino.id}
    assert db.get(Match, ids[0]).home_team_id == inter.id, "Inter's own match is untouched"
    assert db.get(Match, ids[1]) is None and db.get(Match, ids[3]) is None, "the mislabeled copies are gone"
    assert db.get(Match, ids[2]).away_team_id == milan.id
    assert db.get(Match, ids[2]).api_fixture_id == 1550094
    derby = db.query(Match).filter_by(home_team_id=inter.id, away_team_id=milan.id).one()
    assert (derby.home_score, derby.away_score) == (2, 1)


def test_a_european_competition_never_merges_domestic_clubs(db_session):
    """What actually happened once: a mislabeled Europa League row (AC
    Milan's tie stored under Inter) paired with API-Football's row and the
    cleanup merged Inter into AC Milan. Clubs are only ever merged by their
    own league's cleanup."""

    db = db_session
    uel, it = "UEFA Europa League", "Italian Serie A"
    inter, milan = _team(db, "FC Internazionale Milano", it), _team(db, "AC Milan", it)
    porto = _team(db, "FC Porto", "Portuguese Primeira Liga")
    _match(db, uel, porto, milan, dt.datetime(2026, 9, 24, 19), api_id=1600001, score=(1, 1))
    mislabeled = _match(db, uel, porto, inter, dt.datetime(2026, 9, 24, 21), score=(1, 1))
    mislabeled_id = mislabeled.id
    db.commit()

    report = retire_free_fixtures(db, uel)

    assert not report.teams_merged
    assert {t.name for t in db.query(Team).filter_by(league=it)} == {"FC Internazionale Milano", "AC Milan"}
    assert db.get(Match, mislabeled_id) is None

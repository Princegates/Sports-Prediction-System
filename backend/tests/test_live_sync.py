"""Automatic live-match sync: a real fixture in progress should appear in
the Live Match Center on its own, without anyone pushing a simulated event.

``fixtures?live=all`` hands back the entire world's live board in one
request. The interesting behaviour is what this project does with the
99% of it that isn't a club or league it holds -- skip quietly, the same
discipline the rest of api_football_ingest.py applies to an unresolved
club -- and what it does with the 1% that is: reconcile it onto the
existing SCHEDULED row, project the live engine from the real minute and
score, and never write the same state twice.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.data.api_football_ingest import sync_live_matches
from app.data.providers.api_football import ApiFootballClient
from app.db.models import LivePrediction, Match, Team

KICKOFF = dt.datetime(2026, 9, 19, 15, 0)


class FakeClient(ApiFootballClient):
    """Real quota accounting, a scripted /fixtures?live=all response."""

    def __init__(self, rows: list[dict], **kwargs):
        super().__init__("test-key", **kwargs)
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict | None = None) -> list[dict]:
        self.calls.append((path, params or {}))
        self.quota.used_this_run += 1
        return self.rows


def _live_row(*, league_id=39, home="Arsenal FC", away="Chelsea FC", short="2H", elapsed=63,
              home_goals=1, away_goals=0, kickoff=KICKOFF):
    return {
        "fixture": {
            "id": 999,
            "date": kickoff.isoformat() + "+00:00",
            "status": {"short": short, "elapsed": elapsed},
        },
        "league": {"id": league_id, "name": "Premier League"},
        "teams": {"home": {"name": home}, "away": {"name": away}},
        "goals": {"home": home_goals, "away": away_goals},
    }


@pytest.fixture()
def clubs(db_session):
    home = Team(name="Arsenal FC", league="English Premier League", aliases=[])
    away = Team(name="Chelsea FC", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)
    return home, away


@pytest.fixture()
def scheduled_match(db_session, clubs):
    home, away = clubs
    match = Match(
        league="English Premier League", season="2026", date=KICKOFF,
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


def test_matches_a_live_fixture_and_records_a_projection(db_session, scheduled_match):
    before = dt.datetime.utcnow()
    client = FakeClient([_live_row()])
    report = sync_live_matches(db_session, client)

    assert report.considered == 1
    assert report.updated == 1
    db_session.refresh(scheduled_match)
    assert scheduled_match.status == "LIVE"
    assert scheduled_match.home_score == 1
    assert scheduled_match.away_score == 0
    assert scheduled_match.live_synced_at is not None
    assert scheduled_match.live_synced_at >= before

    live = db_session.query(LivePrediction).one()
    assert live.match_id == scheduled_match.id
    assert live.minute == 63
    assert live.score_home == 1
    assert live.score_away == 0
    assert live.trigger_event == "sync"


def test_one_request_covers_the_whole_world(db_session, scheduled_match):
    client = FakeClient([_live_row(), _live_row(league_id=99999, home="Some FC", away="Other FC")])
    sync_live_matches(db_session, client)
    assert len(client.calls) == 1
    assert client.calls[0] == ("fixtures", {"live": "all"})


def test_skips_a_league_this_project_does_not_track(db_session, scheduled_match):
    client = FakeClient([_live_row(league_id=99999)])
    report = sync_live_matches(db_session, client)

    assert report.updated == 0
    db_session.refresh(scheduled_match)
    assert scheduled_match.status == "SCHEDULED"
    assert db_session.query(LivePrediction).count() == 0


def test_skips_an_unresolved_club(db_session, clubs):
    client = FakeClient([_live_row(home="Some Club Nobody Stores FC")])
    report = sync_live_matches(db_session, client)

    assert report.updated == 0
    assert db_session.query(LivePrediction).count() == 0


def test_reports_a_live_fixture_with_no_stored_match(db_session, clubs):
    """The clubs are known but no fixture between them was ever imported --
    still correct to skip; a live probe must never invent a Match row."""

    client = FakeClient([_live_row()])
    report = sync_live_matches(db_session, client)

    assert report.updated == 0
    assert len(report.skipped_no_match) == 1
    assert db_session.query(Match).count() == 0


def test_does_not_duplicate_when_nothing_changed(db_session, scheduled_match):
    client = FakeClient([_live_row(elapsed=63)])
    sync_live_matches(db_session, client)
    report = sync_live_matches(db_session, client)

    assert report.unchanged == 1
    assert report.updated == 0
    assert db_session.query(LivePrediction).count() == 1
    # An "unchanged" poll still confirms the fixture is live -- this is what
    # keeps live_synced_at fresh through the long stretches of a match where
    # nothing scores.
    db_session.refresh(scheduled_match)
    assert scheduled_match.live_synced_at is not None


def test_a_later_minute_records_a_fresh_projection(db_session, scheduled_match):
    client = FakeClient([_live_row(elapsed=63, home_goals=1)])
    sync_live_matches(db_session, client)

    client.rows = [_live_row(elapsed=71, home_goals=2)]
    report = sync_live_matches(db_session, client)

    assert report.updated == 1
    rows = db_session.query(LivePrediction).order_by(LivePrediction.minute.asc()).all()
    assert [r.minute for r in rows] == [63, 71]
    assert rows[-1].score_home == 2


def test_full_time_transitions_status_without_a_live_prediction(db_session, scheduled_match):
    client = FakeClient([_live_row(short="2H", elapsed=63, home_goals=1)])
    sync_live_matches(db_session, client)

    client.rows = [_live_row(short="FT", elapsed=90, home_goals=2, away_goals=1)]
    report = sync_live_matches(db_session, client)

    assert report.finished == 1
    db_session.refresh(scheduled_match)
    assert scheduled_match.status == "FINISHED"
    assert scheduled_match.home_score == 2
    assert scheduled_match.away_score == 1
    assert scheduled_match.live_synced_at is None
    # No new projection for a match that just ended -- there is nothing left
    # to project.
    assert db_session.query(LivePrediction).count() == 1


def test_a_postponed_fixture_is_not_treated_as_live(db_session, scheduled_match):
    client = FakeClient([_live_row(short="PST", elapsed=None)])
    report = sync_live_matches(db_session, client)

    assert report.updated == 0
    assert report.finished == 0
    db_session.refresh(scheduled_match)
    assert scheduled_match.status == "SCHEDULED"


def test_extra_time_does_not_get_mistaken_for_full_time(db_session, scheduled_match):
    """record_live_event's own minute>=90 heuristic would call this
    FINISHED; the provider's status code is the authority, not the clock."""

    client = FakeClient([_live_row(short="2H", elapsed=93, home_goals=2)])
    report = sync_live_matches(db_session, client)

    assert report.updated == 1
    db_session.refresh(scheduled_match)
    assert scheduled_match.status == "LIVE"

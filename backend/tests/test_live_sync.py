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

from app import app_settings
from app.data import api_football_ingest
from app.data.api_football_ingest import run_live_sync_from_settings, sync_live_matches
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


# --- Stuck LIVE matches: a match that drops off the live board entirely --
# before any poll catches it mid-FT, instead of transitioning through it ---


class StuckAwareFakeClient(FakeClient):
    """Like FakeClient, but answers a single-fixture lookup (``{"id": ...}``)
    from its own ``by_id`` map instead of echoing ``self.rows`` -- needed to
    simulate "the live board no longer has this fixture at all, but asking
    for it directly returns its final score"."""

    def __init__(self, rows: list[dict], by_id: dict[int, dict], **kwargs):
        super().__init__(rows, **kwargs)
        self.by_id = by_id

    def get(self, path: str, params: dict | None = None) -> list[dict]:
        self.calls.append((path, params or {}))
        self.quota.used_this_run += 1
        if params and "id" in params:
            row = self.by_id.get(params["id"])
            return [row] if row else []
        return self.rows


@pytest.fixture()
def stuck_live_match(db_session, clubs):
    """Marked LIVE here directly (not via a sync poll) with a kickoff well
    past any real match's length, and live_synced_at already stale -- the
    state a match reaches when it finished without ever being caught by a
    poll in an FT/AET/PEN status (see sync_live_matches's own reconciliation
    step)."""

    home, away = clubs
    kickoff = dt.datetime.utcnow() - dt.timedelta(hours=5)
    match = Match(
        league="English Premier League", season="2026", date=kickoff,
        home_team_id=home.id, away_team_id=away.id, status="LIVE",
        api_fixture_id=999, live_synced_at=kickoff + dt.timedelta(hours=2),
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


def test_a_match_missing_from_the_live_board_is_reconciled_via_direct_lookup(db_session, stuck_live_match):
    client = StuckAwareFakeClient(
        rows=[],  # live board has nothing for this project at all this poll
        by_id={999: {"fixture": {"status": {"short": "FT"}}, "goals": {"home": 2, "away": 1}}},
    )
    report = sync_live_matches(db_session, client)

    assert report.stuck_checked == 1
    assert report.stuck_resolved == 1
    assert report.finished == 1
    db_session.refresh(stuck_live_match)
    assert stuck_live_match.status == "FINISHED"
    assert stuck_live_match.home_score == 2
    assert stuck_live_match.away_score == 1
    assert stuck_live_match.live_synced_at is None


def test_a_match_the_live_board_still_has_is_not_reconciled_separately(db_session, stuck_live_match):
    """If the live board DOES still carry the fixture, the main loop above
    already handles it (whatever its status) -- the reconciliation step must
    not also spend a second, redundant lookup on the same match."""

    client = StuckAwareFakeClient(
        rows=[_live_row(kickoff=stuck_live_match.date, short="2H", elapsed=70)],
        by_id={999: {"fixture": {"status": {"short": "FT"}}, "goals": {"home": 9, "away": 9}}},
    )
    report = sync_live_matches(db_session, client)

    assert report.stuck_checked == 0
    assert ("fixtures", {"id": 999}) not in client.calls


def test_a_recent_kickoff_is_not_treated_as_stuck(db_session, clubs):
    """Still well within a plausible match length -- the live board simply
    not including it this one poll is not evidence it finished."""

    home, away = clubs
    kickoff = dt.datetime.utcnow() - dt.timedelta(minutes=45)
    match = Match(
        league="English Premier League", season="2026", date=kickoff,
        home_team_id=home.id, away_team_id=away.id, status="LIVE",
        api_fixture_id=999, live_synced_at=kickoff,
    )
    db_session.add(match)
    db_session.commit()

    client = StuckAwareFakeClient(rows=[], by_id={})
    report = sync_live_matches(db_session, client)

    assert report.stuck_checked == 0
    db_session.refresh(match)
    assert match.status == "LIVE"


def test_a_fixture_the_provider_no_longer_has_is_left_alone(db_session, stuck_live_match):
    """The single-fixture lookup coming back empty (removed/postponed
    fixture) must not be mistaken for anything gradeable -- leave the match
    exactly as it was rather than guess."""

    client = StuckAwareFakeClient(rows=[], by_id={})
    report = sync_live_matches(db_session, client)

    assert report.stuck_checked == 1
    assert report.stuck_resolved == 0
    db_session.refresh(stuck_live_match)
    assert stuck_live_match.status == "LIVE"


# --- run_live_sync_from_settings: the settings-to-client wiring shared by --
# scripts/sync_live_matches.py and app.main's own in-process scheduler -----


def test_run_live_sync_from_settings_is_a_noop_with_no_key(db_session, monkeypatch):
    monkeypatch.setattr(app_settings, "all_values", lambda db: {"api_football_key": ""})
    assert run_live_sync_from_settings(db_session) is None


def _with_key(monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "all_values",
        lambda db: {
            "api_football_key": "test-key",
            "api_football_host": "v3.football.api-sports.io",
            "api_football_daily_budget": 7500,
            "api_football_per_minute": 300,
        },
    )


def test_run_live_sync_from_settings_skips_the_request_when_no_tracked_match_is_on(db_session, clubs, monkeypatch):
    """The live board costs a request per poll; with nothing of ours near
    kickoff there's nothing to learn from it."""

    home, away = clubs
    now = dt.datetime.utcnow()
    db_session.add_all([
        Match(league="English Premier League", season="2026", date=now + dt.timedelta(hours=5),
              home_team_id=home.id, away_team_id=away.id, status="SCHEDULED"),
        Match(league="English Premier League", season="2026", date=now - dt.timedelta(hours=6),
              home_team_id=home.id, away_team_id=away.id, status="FINISHED", home_score=1, away_score=0),
    ])
    db_session.commit()
    _with_key(monkeypatch)
    clients = []
    monkeypatch.setattr(api_football_ingest, "ApiFootballClient", lambda key, **kw: clients.append(1) or FakeClient([], **kw))

    report = run_live_sync_from_settings(db_session)

    assert report is not None and report.idle
    assert report.requests_used == 0
    assert clients == []


@pytest.mark.parametrize(
    "offset, status, fixture_id, due",
    [
        (dt.timedelta(minutes=5), "SCHEDULED", None, True),     # about to kick off
        (dt.timedelta(minutes=-80), "LIVE", None, True),        # in play
        (dt.timedelta(hours=-2, minutes=-50), "SCHEDULED", None, True),  # late start / extra time
        (dt.timedelta(minutes=30), "SCHEDULED", None, False),   # not yet
        (dt.timedelta(hours=-4), "SCHEDULED", None, False),     # long over, never synced
        (dt.timedelta(days=-2), "LIVE", 555, True),             # stuck LIVE, still needs reconciling
        (dt.timedelta(days=-2), "LIVE", None, False),           # stale sandbox row, nothing to reconcile
    ],
)
def test_live_poll_due_window(db_session, clubs, offset, status, fixture_id, due):
    home, away = clubs
    now = dt.datetime(2026, 10, 20, 15, 0)
    db_session.add(Match(league="English Premier League", season="2026", date=now + offset,
                         home_team_id=home.id, away_team_id=away.id, status=status, api_fixture_id=fixture_id))
    db_session.commit()
    assert api_football_ingest.live_poll_due(db_session, now=now) is due


def test_run_live_sync_from_settings_builds_a_client_and_syncs(db_session, scheduled_match, monkeypatch):
    monkeypatch.setattr(api_football_ingest, "live_poll_due", lambda db, now=None: True)
    monkeypatch.setattr(
        app_settings,
        "all_values",
        lambda db: {
            "api_football_key": "test-key",
            "api_football_host": "v3.football.api-sports.io",
            "api_football_daily_budget": 7500,
            "api_football_per_minute": 300,
        },
    )
    monkeypatch.setattr(
        api_football_ingest,
        "ApiFootballClient",
        lambda key, **kwargs: FakeClient([_live_row()], **kwargs),
    )

    report = run_live_sync_from_settings(db_session)

    assert report is not None
    assert report.considered == 1
    assert report.updated == 1
    db_session.refresh(scheduled_match)
    assert scheduled_match.status == "LIVE"

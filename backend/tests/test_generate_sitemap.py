"""scripts/generate_sitemap.py -- the sitemap that feeds search engines the
per-fixture preview pages, regenerated after each fixture refresh.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.db.models import Match, Team

LEAGUE = "English Premier League"


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_sitemap.py"
    spec = importlib.util.spec_from_file_location("generate_sitemap_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _team(db_session, name: str) -> Team:
    team = Team(name=name, league=LEAGUE, country="England", aliases=[])
    db_session.add(team)
    db_session.commit()
    db_session.refresh(team)
    return team


def test_fixture_slug_is_lowercase_hyphenated_and_strips_accents(script, db_session):
    home = _team(db_session, "Koln")
    away = _team(db_session, "Saint-Etienne FC")
    import datetime as dt

    match = Match(
        league=LEAGUE, season="2025-26", date=dt.datetime.utcnow() + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)

    slug = script.fixture_slug(match)
    assert slug == f"{match.id}-koln-vs-saint-etienne-fc"


def test_render_sitemap_includes_every_static_page(script):
    xml = script.render_sitemap([])
    for path in ("/", "/how-it-works", "/fixtures", "/responsible", "/register", "/login"):
        assert f"<loc>https://soccaintel.com{path}</loc>" in xml


def test_render_sitemap_includes_each_fixture_slug(script):
    xml = script.render_sitemap(["123-arsenal-vs-chelsea", "456-city-vs-united"])
    assert "<loc>https://soccaintel.com/predict/123-arsenal-vs-chelsea</loc>" in xml
    assert "<loc>https://soccaintel.com/predict/456-city-vs-united</loc>" in xml


def test_main_dry_run_does_not_write_the_file(script, db_session, tmp_path, monkeypatch, capsys):
    target = tmp_path / "sitemap.xml"
    monkeypatch.setattr(script, "SITEMAP_PATH", target)

    sys.argv = ["generate_sitemap.py", "--dry-run"]
    script.main()

    assert not target.exists()
    assert "<urlset" in capsys.readouterr().out


def test_main_writes_a_url_for_each_upcoming_fixture(script, db_session, tmp_path, monkeypatch):
    import datetime as dt

    target = tmp_path / "sitemap.xml"
    monkeypatch.setattr(script, "SITEMAP_PATH", target)

    home = _team(db_session, "Arsenal")
    away = _team(db_session, "Chelsea")
    upcoming = Match(
        league=LEAGUE, season="2025-26", date=dt.datetime.utcnow() + dt.timedelta(days=2),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    too_far = Match(
        league=LEAGUE, season="2025-26", date=dt.datetime.utcnow() + dt.timedelta(days=30),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    played = Match(
        league=LEAGUE, season="2025-26", date=dt.datetime.utcnow() - dt.timedelta(days=2),
        home_team_id=home.id, away_team_id=away.id, status="FINISHED",
        home_score=1, away_score=0,
    )
    db_session.add_all([upcoming, too_far, played])
    db_session.commit()
    db_session.refresh(upcoming)

    sys.argv = ["generate_sitemap.py"]
    script.main()

    content = target.read_text()
    assert f"/predict/{upcoming.id}-arsenal-vs-chelsea" in content
    # Out of the public preview window (too far ahead) or already played --
    # neither belongs in a sitemap meant only for currently-previewable fixtures.
    assert f"/predict/{too_far.id}-" not in content
    assert f"/predict/{played.id}-" not in content

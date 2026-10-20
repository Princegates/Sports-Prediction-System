"""Clubs matched by API-Football's own team id, not just by name.

Name matching alone is what produced the long run of duplicate- and
merged-club repair scripts. The id is learned the first time a name match
succeeds, then wins outright -- and a name match onto a row that already
carries a *different* id is refused rather than merged.
"""

from __future__ import annotations

from app.data.api_football_ingest import TeamIndex
from app.db.models import Team


def _team(db, name: str, league: str = "English Premier League", provider_id: int | None = None) -> Team:
    team = Team(name=name, league=league, aliases=[], api_football_team_id=provider_id)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def test_a_name_match_learns_the_provider_id(db_session):
    arsenal = _team(db_session, "Arsenal FC")
    index = TeamIndex(db_session)

    assert index.resolve("Arsenal", provider_id=42) == arsenal
    assert arsenal.api_football_team_id == 42


def test_a_known_id_wins_over_a_changed_spelling(db_session):
    spurs = _team(db_session, "Tottenham Hotspur", provider_id=47)
    index = TeamIndex(db_session)

    # A spelling no alias covers still lands on the right club.
    assert index.resolve("Spurs (London)", provider_id=47) == spurs


def test_a_name_match_onto_a_row_with_a_different_id_is_refused(db_session):
    """Two provider ids for one row means two real clubs a fuzzy name has
    merged -- the bug the repair scripts kept cleaning up after."""

    _team(db_session, "Real Madrid", league="Spanish La Liga", provider_id=541)
    index = TeamIndex(db_session)

    assert index.resolve("Real Madrid Castilla", provider_id=9999) is None


def test_the_same_club_in_two_divisions_is_settled_by_league(db_session):
    old_row = _team(db_session, "Leeds United", league="English Championship", provider_id=63)
    new_row = _team(db_session, "Leeds United", league="English Premier League", provider_id=63)
    index = TeamIndex(db_session)

    assert index.resolve("Leeds", provider_id=63, prefer_league="English Premier League") == new_row
    assert index.resolve("Leeds", provider_id=63, prefer_league="English Championship") == old_row


def test_without_an_id_name_matching_works_as_before(db_session):
    chelsea = _team(db_session, "Chelsea FC")
    index = TeamIndex(db_session)
    assert index.resolve("Chelsea") == chelsea
    assert chelsea.api_football_team_id is None


def test_created_clubs_carry_their_id(db_session):
    index = TeamIndex(db_session)
    team = index.create("Brand New FC", "English Premier League", provider_id=777)
    assert team.api_football_team_id == 777
    assert index.resolve("Totally different spelling", provider_id=777) == team

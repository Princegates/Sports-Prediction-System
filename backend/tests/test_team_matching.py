from app.data.team_matching import normalize_team_name


def test_known_alias_pairs_normalize_the_same():
    pairs = [
        ("Real Madrid", "Real Madrid CF"),
        ("Deportivo Alavés", "CD Alavés"),
        ("Real Betis", "Real Betis Balompié"),
        ("Real Valladolid", "Real Valladolid CF"),
    ]
    for a, b in pairs:
        assert normalize_team_name(a) == normalize_team_name(b), f"{a!r} vs {b!r}"


def test_distinct_clubs_do_not_collide():
    distinct = ["Real Madrid", "Real Sociedad de Fútbol", "Real Betis Balompié", "Real Racing Club de Santander"]
    normalized = [normalize_team_name(n) for n in distinct]
    assert len(set(normalized)) == len(normalized)


def test_unrelated_teams_stay_distinct():
    assert normalize_team_name("Manchester United FC") != normalize_team_name("Manchester City FC")
    assert normalize_team_name("Arsenal FC") != normalize_team_name("Chelsea FC")

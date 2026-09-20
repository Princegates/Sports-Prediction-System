from app.data.team_matching import name_match_score, name_similarity, normalize_team_name


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


# --- name_match_score: two clubs sharing a city must not look identical ----
#
# A dry run of scripts/merge_duplicate_teams.py against this project's real
# data once proposed merging AC Milan with Internazionale, Real Madrid with
# Atletico Madrid and with Rayo Vallecano, and FC Barcelona with RCD
# Espanyol -- four pairs of genuinely different, sometimes rival, clubs that
# happen to share a home city. Each one reduced to a single shared city
# token once ordinary club-name furniture ("FC", "RCD", "de", ...) was
# stripped, and name_similarity (or a bare primary-score check) cannot tell
# that apart from a real abbreviation.


def test_same_city_does_not_mean_same_club():
    pairs = [
        ("AC Milan", "FC Internazionale Milano"),
        ("AC Milan", "Internazionale Milano"),
        ("Real Madrid", "Atletico Madrid"),
        ("Real Madrid CF", "Club Atlético de Madrid"),
        ("Real Madrid CF", "Rayo Vallecano de Madrid"),
        ("FC Barcelona", "RCD Espanyol de Barcelona"),
    ]
    for a, b in pairs:
        primary, coverage = name_match_score(a, b)
        assert primary < 1.0, f"{a!r} vs {b!r} scored a full primary match: {(primary, coverage)}"
        assert name_similarity(a, b) < 1.0, f"{a!r} vs {b!r}"


def test_milan_and_milano_are_an_exonym_pair_not_an_abbreviation():
    """"Milan" (English) and "Milano" (Italian) name the same city, the same
    way "Munich"/"Munchen" do -- but here the coincidence runs the other
    way: it must not let AC Milan's own short name match a token buried in
    Internazionale's full legal name."""

    assert name_match_score("AC Milan", "FC Internazionale Milano") == (0.0, 0.0)
    # AC Milan's actual own alias/exact-spelling paths are unaffected.
    assert name_match_score("AC Milan", "AC Milan") == (1.0, 1.0)
    assert name_match_score("Inter Milan", "Internazionale") == (1.0, 1.0)


def test_real_prefix_is_no_longer_dropped_for_fuzzy_matching():
    """Real Madrid and Real Sociedad both keep "real" for the fuzzy scorer
    now (see _FUZZY_STRIP_TOKENS) -- normalize_team_name's own equivalence
    (used for exact-key and chat resolution) is untouched by this."""

    assert name_match_score("Real Madrid", "Real Madrid CF") == (1.0, 1.0)
    assert name_match_score("Real Betis", "Real Betis Balompié")[0] == 1.0
    assert normalize_team_name("Real Madrid") == normalize_team_name("Real Madrid CF")


def test_legitimate_abbreviations_still_match_despite_the_city_guard():
    """The fix must not overcorrect into refusing genuine abbreviation
    pairs that happen to involve one of the guarded city tokens."""

    pairs = [
        ("Man United", "Manchester United FC"),
        ("Wolves", "Wolverhampton Wanderers FC"),
        ("Ath Madrid", "Atlético Madrid"),
        ("Leeds", "Leeds United"),
        ("Sampdoria", "UC Sampdoria"),
        ("Atalanta BC", "Atalanta"),
    ]
    for a, b in pairs:
        assert name_similarity(a, b) == 1.0, f"{a!r} vs {b!r}"

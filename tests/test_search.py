from app.search import SearchQuery, expand_slash_numbers, normalize


def test_normalize_strips_polish_diacritics_and_case():
    assert normalize("ŁÓDŹ Żółć") == "lodz zolc"


def test_slash_numbers_expand_to_both_directions():
    assert expand_slash_numbers("3822/3") == ["3822", "3823"]
    assert expand_slash_numbers("77118") == ["77118"]


def test_name_matches_case_and_diacritics_insensitive():
    assert SearchQuery.parse("szkuner").matches_train("SZKUNER", "8312/3", "IC")
    assert SearchQuery.parse("galczynski").matches_train("GAŁCZYŃSKI", "5310", "IC")


def test_number_matches_any_expanded_number_by_prefix():
    query = SearchQuery.parse("3823")
    assert query.matches_train(None, "3822/3", "IC")
    assert not query.matches_train(None, "3812/3", "IC")


def test_all_tokens_must_match():
    assert SearchQuery.parse("IC 3822").matches_train("SUKIENNICE", "3822/3", "IC")
    assert not SearchQuery.parse("TLK 3822").matches_train("SUKIENNICE", "3822/3", "IC")


def test_empty_query_matches_nothing():
    assert not SearchQuery.parse("  ").matches_train("SZKUNER", "8312/3", "IC")


def test_station_name_matches_all_tokens_in_any_order():
    assert SearchQuery.parse("glowny poznan").matches_name("Poznań Główny")
    assert not SearchQuery.parse("poznan wschod").matches_name("Poznań Główny")


def test_exact_station_name_ranks_before_prefix_and_substring():
    query = SearchQuery.parse("poznan glowny")
    names = ["Stary Poznań Główny", "Poznań Główny Towarowy", "Poznań Główny"]
    assert sorted(names, key=query.name_rank) == ["Poznań Główny", "Poznań Główny Towarowy", "Stary Poznań Główny"]


def test_shared_link_identifier_finds_train_by_carrier_and_number():
    assert SearchQuery.parse("KW-77426").matches_train(None, "77426/7", "Os", "KW")
    assert not SearchQuery.parse("PR-77426").matches_train(None, "77426/7", "Os", "KW")


def test_hyphenated_station_name_is_still_an_exact_match():
    assert SearchQuery.parse("Gdynia Stocznia-Uniwersytet Morski").is_exact_name("Gdynia Stocznia-Uniwersytet Morski")
    assert SearchQuery.parse("gdynia stocznia uniwersytet morski").is_exact_name("Gdynia Stocznia-Uniwersytet Morski")

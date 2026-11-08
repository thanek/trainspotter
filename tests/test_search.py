from app.search import TrainQuery, expand_slash_numbers, normalize


def test_normalize_strips_polish_diacritics_and_case():
    assert normalize("ŁÓDŹ Żółć") == "lodz zolc"


def test_slash_numbers_expand_to_both_directions():
    assert expand_slash_numbers("3822/3") == ["3822", "3823"]
    assert expand_slash_numbers("77118") == ["77118"]


def test_name_matches_case_and_diacritics_insensitive():
    assert TrainQuery.parse("szkuner").matches("SZKUNER", "8312/3", "IC")
    assert TrainQuery.parse("galczynski").matches("GAŁCZYŃSKI", "5310", "IC")


def test_number_matches_any_expanded_number_by_prefix():
    query = TrainQuery.parse("3823")
    assert query.matches(None, "3822/3", "IC")
    assert not query.matches(None, "3812/3", "IC")


def test_all_tokens_must_match():
    assert TrainQuery.parse("IC 3822").matches("SUKIENNICE", "3822/3", "IC")
    assert not TrainQuery.parse("TLK 3822").matches("SUKIENNICE", "3822/3", "IC")


def test_empty_query_matches_nothing():
    assert not TrainQuery.parse("  ").matches("SZKUNER", "8312/3", "IC")

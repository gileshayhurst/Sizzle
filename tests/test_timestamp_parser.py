from timestamp_parser import parse_scored_timestamps


def test_single_range_without_score_defaults_to_5():
    assert parse_scored_timestamps("0:23-1:05") == [("0:23-1:05", 5)]


def test_multiple_ranges():
    assert parse_scored_timestamps("0:23-1:05, 2:14-2:40") == [
        ("0:23-1:05", 5), ("2:14-2:40", 5)
    ]


def test_none_returns_none():
    assert parse_scored_timestamps("none") is None


def test_none_is_case_insensitive():
    assert parse_scored_timestamps("None") is None
    assert parse_scored_timestamps("NONE") is None


def test_unparseable_returns_none():
    assert parse_scored_timestamps("I cannot determine any relevant segments.") is None


def test_strips_surrounding_whitespace():
    assert parse_scored_timestamps("  0:23-1:05  ") == [("0:23-1:05", 5)]


def test_three_ranges():
    assert parse_scored_timestamps("0:05-0:18, 1:30-2:00, 3:44-4:02") == [
        ("0:05-0:18", 5), ("1:30-2:00", 5), ("3:44-4:02", 5)
    ]

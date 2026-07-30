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


def test_bracketed_range_keeps_its_score():
    """Rich-tier responses bracket the range. Verbatim capture from opus-4-8
    against a rich transcript — every score here used to collapse to 5, which
    put every segment below any useful threshold."""
    response = (
        "[2:18-2:30]|9\n"
        "[4:59-5:03]|8\n"
        "[5:08-5:10]|6\n"
        "[9:02-9:06]|7"
    )
    assert parse_scored_timestamps(response) == [
        ("2:18-2:30", 9), ("4:59-5:03", 8), ("5:08-5:10", 6), ("9:02-9:06", 7)
    ]


def test_bracketed_range_without_score_still_defaults():
    assert parse_scored_timestamps("[0:23-1:05]") == [("0:23-1:05", 5)]


def test_three_ranges():
    assert parse_scored_timestamps("0:05-0:18, 1:30-2:00, 3:44-4:02") == [
        ("0:05-0:18", 5), ("1:30-2:00", 5), ("3:44-4:02", 5)
    ]

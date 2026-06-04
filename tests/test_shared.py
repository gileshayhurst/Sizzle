from shared import parse_transcript_lines


def test_parse_empty_string():
    assert parse_transcript_lines("") == []


def test_parse_ignores_non_matching_lines():
    assert parse_transcript_lines("random line\nanother line") == []


def test_parse_single_line():
    result = parse_transcript_lines("[0:05] Speaker: Hello world.")
    assert len(result) == 1
    line = result[0]
    assert line["raw"] == "[0:05] Speaker: Hello world."
    assert line["timestamp"] == "0:05"
    assert line["text"] == "Hello world."
    assert line["seconds"] == 5.0
    assert line["minute_bucket"] == 0


def test_parse_minute_bucket():
    result = parse_transcript_lines("[1:10] Speaker: Second minute.")
    assert result[0]["minute_bucket"] == 1
    assert result[0]["seconds"] == 70.0


def test_parse_multiple_lines():
    raw = "[0:05] Speaker: First.\n[1:30] Speaker: Second."
    result = parse_transcript_lines(raw)
    assert len(result) == 2
    assert result[0]["text"] == "First."
    assert result[1]["text"] == "Second."


def test_parse_skips_blank_lines():
    raw = "[0:05] Speaker: First.\n\n[1:30] Speaker: Second."
    result = parse_transcript_lines(raw)
    assert len(result) == 2

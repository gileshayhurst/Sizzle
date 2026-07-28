from encoder.core.forven import parse, sentences


def test_parse_single_turn():
    result = parse("[02:01] Participant: We try to get him groomed.")
    assert result == [
        {"start": 121.0, "role": "Participant", "text": "We try to get him groomed."}
    ]


def test_parse_skips_unparseable_lines():
    assert parse("not a line\n\n[00:03] Interviewer: Hello.") == [
        {"start": 3.0, "role": "Interviewer", "text": "Hello."}
    ]


def test_parse_handles_unpadded_minutes():
    assert parse("[4:23] Participant: No.")[0]["start"] == 263.0


def test_sentences_splits_on_terminal_punctuation():
    assert sentences("He's a Corgi mix. We got him as a rescue.") == [
        "He's a Corgi mix.",
        "We got him as a rescue.",
    ]


def test_sentences_does_not_split_after_abbreviation():
    assert sentences("We eat around 4:00 or 5:00 p.m. Then he sleeps.") == [
        "We eat around 4:00 or 5:00 p.m. Then he sleeps."
    ]


def test_sentences_does_not_split_on_comma():
    text = "Um, one is a German shepherd mix and the other is a pit bull lab mix."
    assert sentences(text) == [text]


def test_sentences_keeps_short_answer_whole():
    assert sentences("Yeah.") == ["Yeah."]


def test_sentences_keeps_disfluency_prefix_attached():
    assert sentences("Um, we got him as a rescue.") == ["Um, we got him as a rescue."]


def test_sentences_does_not_split_mid_number():
    assert sentences("He is 10.5 years old.") == ["He is 10.5 years old."]


def test_sentences_empty_text():
    assert sentences("") == []

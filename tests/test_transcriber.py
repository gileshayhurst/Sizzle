from unittest.mock import MagicMock, patch
from transcriber import transcribe_video, _seconds_to_timestamp, _split_into_sentences
from types import SimpleNamespace


def test_seconds_to_timestamp_zero():
    assert _seconds_to_timestamp(0.0) == "0:00"


def test_seconds_to_timestamp_whole_minutes():
    assert _seconds_to_timestamp(60.0) == "1:00"


def test_seconds_to_timestamp_minutes_and_seconds():
    assert _seconds_to_timestamp(125.0) == "2:05"


def test_seconds_to_timestamp_pads_single_digit_seconds():
    assert _seconds_to_timestamp(5.0) == "0:05"


def test_seconds_to_timestamp_truncates_fractional_seconds():
    assert _seconds_to_timestamp(65.9) == "1:05"


def _word(word: str, start: float, end: float):
    return SimpleNamespace(word=word, start=start, end=end)


def _segment(start: float, text: str, words=None, end: float | None = None):
    return SimpleNamespace(start=start, end=start + 1.0 if end is None else end, text=text, words=words)


def _make_mock_model(segments):
    """Mock a faster-whisper WhisperModel: .transcribe() returns (segments_gen, info)."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segments), SimpleNamespace(language="en"))
    return mock_model


def test_formats_single_segment():
    # No word timestamps -> falls back to segment start/end, so the rich
    # [start-end] range still comes through (segment.end is always present).
    model = _make_mock_model([_segment(5.0, "Hello there")])
    result = transcribe_video("video.mp4", model=model)
    assert result == "[0:05-0:06] Speaker: Hello there"


def test_formats_multiple_segments():
    model = _make_mock_model([
        _segment(5.0, "Hello there"),
        _segment(65.0, "And then she said"),
    ])
    result = transcribe_video("video.mp4", model=model)
    assert result == "[0:05-0:06] Speaker: Hello there\n[1:05-1:06] Speaker: And then she said"


def test_strips_whitespace_from_segment_text():
    model = _make_mock_model([_segment(0.0, "  padded  ")])
    result = transcribe_video("video.mp4", model=model)
    assert result == "[0:00-0:01] Speaker: padded"


def test_requests_word_timestamps():
    model = _make_mock_model([_segment(0.0, "Test")])
    transcribe_video("video.mp4", model=model)
    _, kwargs = model.transcribe.call_args
    assert kwargs.get("word_timestamps") is True


def test_builds_base_model_when_none_provided():
    fake_model = _make_mock_model([_segment(0.0, "Test")])
    with patch("faster_whisper.WhisperModel", return_value=fake_model) as mock_ctor:
        transcribe_video("video.mp4")
    mock_ctor.assert_called_once()
    assert mock_ctor.call_args[0][0] == "base"


def test_segment_to_dict_maps_word_objects():
    from transcriber import _segment_to_dict
    seg = _segment(2.0, "Hi there.", words=[_word("Hi", 2.0, 2.3), _word(" there.", 2.3, 2.9)])
    d = _segment_to_dict(seg)
    assert d == {
        "start": 2.0,
        "end": 3.0,
        "text": "Hi there.",
        "words": [
            {"word": "Hi", "start": 2.0, "end": 2.3},
            {"word": " there.", "start": 2.3, "end": 2.9},
        ],
    }


def test_segment_to_dict_handles_no_words():
    from transcriber import _segment_to_dict
    seg = _segment(1.0, "No words", words=None)
    d = _segment_to_dict(seg)
    assert d == {"start": 1.0, "end": 2.0, "text": "No words", "words": []}


# --- _split_into_sentences ---

def _make_word(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


def test_split_falls_back_to_segment_level_when_no_words():
    # No "end" key in the segment dict either -> end comes back None, which
    # transcribe_video's caller treats as "no rich range available".
    segment = {"start": 5.0, "text": "Hello there"}
    result = _split_into_sentences(segment)
    assert result == [(5.0, None, "Hello there")]


def test_split_single_sentence_with_words():
    segment = {
        "start": 5.0,
        "text": "Hello there.",
        "words": [
            _make_word("Hello", 5.0, 5.3),
            _make_word(" there.", 5.3, 5.8),
        ],
    }
    result = _split_into_sentences(segment)
    assert result == [(5.0, 5.8, "Hello there.")]


def test_split_two_sentences_use_word_start_times():
    segment = {
        "start": 5.0,
        "text": "First sentence. Second one.",
        "words": [
            _make_word("First", 5.0, 5.2),
            _make_word(" sentence.", 5.2, 5.8),
            _make_word(" Second", 6.0, 6.3),
            _make_word(" one.", 6.3, 6.8),
        ],
    }
    result = _split_into_sentences(segment)
    assert result == [(5.0, 5.8, "First sentence."), (6.0, 6.8, "Second one.")]


def test_split_no_terminal_punctuation_flushes_as_one_entry():
    segment = {
        "start": 0.0,
        "text": "No punctuation here",
        "words": [
            _make_word("No", 0.0, 0.3),
            _make_word(" punctuation", 0.3, 0.7),
            _make_word(" here", 0.7, 1.0),
        ],
    }
    result = _split_into_sentences(segment)
    assert result == [(0.0, 1.0, "No punctuation here")]


def test_split_exclamation_mark_ends_sentence():
    segment = {
        "start": 0.0,
        "text": "Wow! Amazing.",
        "words": [
            _make_word("Wow!", 0.0, 0.4),
            _make_word(" Amazing.", 1.0, 1.5),
        ],
    }
    result = _split_into_sentences(segment)
    assert result == [(0.0, 0.4, "Wow!"), (1.0, 1.5, "Amazing.")]


def test_transcribe_video_uses_word_timestamps_for_sentence_splitting():
    """Integration: multi-sentence segment produces separate transcript lines with distinct timestamps."""
    seg = _segment(10.0, "The miso soup was great. Now for the yellowtail.", words=[
        _word("The", 10.0, 10.2),
        _word(" miso", 10.2, 10.5),
        _word(" soup", 10.5, 10.7),
        _word(" was", 10.7, 10.9),
        _word(" great.", 10.9, 11.3),
        _word(" Now", 12.0, 12.2),
        _word(" for", 12.2, 12.4),
        _word(" the", 12.4, 12.5),
        _word(" yellowtail.", 12.5, 13.0),
    ])
    model = _make_mock_model([seg])
    result = transcribe_video("video.mp4", model=model)
    assert result == (
        "[0:10-0:12] Speaker: The miso soup was great.\n"
        "[0:12-0:13] Speaker: Now for the yellowtail."
    )


def test_split_into_sentences_returns_start_and_end():
    from transcriber import _split_into_sentences
    seg = {
        "start": 0.0, "text": "One. Two.",
        "words": [
            {"word": "One.", "start": 0.0, "end": 1.5},
            {"word": " Two.", "start": 2.0, "end": 3.5},
        ],
    }
    assert _split_into_sentences(seg) == [(0.0, 1.5, "One."), (2.0, 3.5, "Two.")]


def test_transcribe_emits_rich_lines_with_ceiled_ends():
    from transcriber import transcribe_video

    class FakeWord:
        def __init__(self, word, start, end):
            self.word, self.start, self.end = word, start, end

    class FakeSegment:
        start, end, text = 0.0, 3.9, "Hello there."
        words = [FakeWord("Hello", 0.2, 1.0), FakeWord(" there.", 1.1, 3.9)]

    class FakeModel:
        def transcribe(self, path, **kw):
            return [FakeSegment()], None

    out = transcribe_video("ignored.mp4", model=FakeModel())
    # start floors to 0:00, end CEILS to 0:04 — never truncate an end or the
    # last word is clipped.
    assert out == "[0:00-0:04] Speaker: Hello there."

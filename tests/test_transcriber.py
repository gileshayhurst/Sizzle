from unittest.mock import MagicMock, patch
from transcriber import transcribe_video, _seconds_to_timestamp, _split_into_sentences


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


def _make_mock_model(segments):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"segments": segments}
    return mock_model


def test_formats_single_segment():
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([{"start": 5.0, "text": "Hello there"}])
        result = transcribe_video("video.mp4")
    assert result == "[0:05] Speaker: Hello there"


def test_formats_multiple_segments():
    segments = [
        {"start": 5.0, "text": "Hello there"},
        {"start": 65.0, "text": "And then she said"},
    ]
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model(segments)
        result = transcribe_video("video.mp4")
    assert result == "[0:05] Speaker: Hello there\n[1:05] Speaker: And then she said"


def test_strips_whitespace_from_segment_text():
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([{"start": 0.0, "text": "  padded  "}])
        result = transcribe_video("video.mp4")
    assert result == "[0:00] Speaker: padded"


def test_loads_base_model():
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([{"start": 0.0, "text": "Test"}])
        transcribe_video("video.mp4")
    mock_load.assert_called_once_with("base")


# --- _split_into_sentences ---

def _make_word(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


def test_split_falls_back_to_segment_level_when_no_words():
    segment = {"start": 5.0, "text": "Hello there"}
    result = _split_into_sentences(segment)
    assert result == [(5.0, "Hello there")]


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
    assert result == [(5.0, "Hello there.")]


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
    assert result == [(5.0, "First sentence."), (6.0, "Second one.")]


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
    assert result == [(0.0, "No punctuation here")]


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
    assert result == [(0.0, "Wow!"), (1.0, "Amazing.")]


def test_transcribe_video_uses_word_timestamps_for_sentence_splitting():
    """Integration: multi-sentence segment produces separate transcript lines with distinct timestamps."""
    segment = {
        "start": 10.0,
        "text": "The miso soup was great. Now for the yellowtail.",
        "words": [
            _make_word("The", 10.0, 10.2),
            _make_word(" miso", 10.2, 10.5),
            _make_word(" soup", 10.5, 10.7),
            _make_word(" was", 10.7, 10.9),
            _make_word(" great.", 10.9, 11.3),
            _make_word(" Now", 12.0, 12.2),
            _make_word(" for", 12.2, 12.4),
            _make_word(" the", 12.4, 12.5),
            _make_word(" yellowtail.", 12.5, 13.0),
        ],
    }
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([segment])
        result = transcribe_video("video.mp4")
    assert result == "[0:10] Speaker: The miso soup was great.\n[0:12] Speaker: Now for the yellowtail."

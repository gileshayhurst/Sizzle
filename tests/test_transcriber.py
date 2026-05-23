from unittest.mock import MagicMock, patch
from transcriber import transcribe_video, _seconds_to_timestamp


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

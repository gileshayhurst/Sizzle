from pathlib import Path
from unittest.mock import patch

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


def test_filter_removes_reel_path_local_mode():
    from shared import filter_generated_reels
    library = [{"path": str(Path("/videos/reel.mp4").resolve()), "filename": "reel.mp4"}]
    paths = [Path("/videos/source.mp4"), Path("/videos/reel.mp4")]
    with patch("storage.is_cloud", return_value=False):
        result = filter_generated_reels(paths, library=library)
    assert [p.name for p in result] == ["source.mp4"]


def test_filter_removes_reel_by_filename_cloud_mode():
    from shared import filter_generated_reels
    library = [{"filename": "reel.mp4", "path": "/ignored"}]
    paths = [Path("/tmp/source.mp4"), Path("/tmp/reel.mp4")]
    with patch("storage.is_cloud", return_value=True):
        result = filter_generated_reels(paths, library=library)
    assert [p.name for p in result] == ["source.mp4"]


def test_filter_fails_open_on_load_library_exception():
    from shared import filter_generated_reels
    paths = [Path("/a.mp4"), Path("/b.mp4")]
    with patch("storage.load_library", side_effect=RuntimeError("db error")):
        result = filter_generated_reels(paths)   # library=None → calls load_library
    assert result == paths


def test_filter_returns_all_when_library_empty():
    from shared import filter_generated_reels
    paths = [Path("/a.mp4"), Path("/b.mp4")]
    with patch("storage.is_cloud", return_value=False):
        result = filter_generated_reels(paths, library=[])
    assert result == paths


def test_parse_captures_speaker_and_is_interviewer_flags():
    raw = (
        "[0:10] Interviewer: Have you heard of Freshpet?\n"
        "[0:14] Participant: Yes I love it.\n"
    )
    result = parse_transcript_lines(raw)
    assert result[0]["speaker"] == "Interviewer"
    assert result[0]["is_interviewer"] is True
    assert result[0]["text"] == "Have you heard of Freshpet?"
    assert result[1]["speaker"] == "Participant"
    assert result[1]["is_interviewer"] is False
    assert result[1]["text"] == "Yes I love it."


def test_parse_captures_multiword_speaker_label():
    result = parse_transcript_lines("[0:03] AI Agent: Shall we begin?")
    assert result[0]["speaker"] == "AI Agent"
    assert result[0]["is_interviewer"] is True
    assert result[0]["text"] == "Shall we begin?"


def test_parse_unlabeled_speaker_is_not_interviewer():
    # Whisper fallback emits "Speaker:" — must stay selectable content.
    result = parse_transcript_lines("[0:05] Speaker: Hello world.")
    assert result[0]["speaker"] == "Speaker"
    assert result[0]["is_interviewer"] is False


def test_is_interviewer_label_is_case_insensitive_over_synonyms():
    from shared import is_interviewer_label
    for label in ["interviewer", "INTERVIEWER", "Ai", "AI Agent",
                  "moderator", "Bot", "assistant", "Host", "agent"]:
        assert is_interviewer_label(label) is True
    for label in ["Participant", "Respondent", "Speaker", "Interviewee", "Guest"]:
        assert is_interviewer_label(label) is False

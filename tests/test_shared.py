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


from shared import normalize_transcript


def test_normalize_leaves_single_sentence_line_untouched():
    raw = "[0:05] Participant: Hello world."
    assert normalize_transcript(raw) == raw


def test_normalize_leaves_non_matching_lines_untouched():
    raw = "some header\n[0:05] Participant: Hello world."
    assert normalize_transcript(raw) == raw


def test_normalize_splits_multi_sentence_turn():
    raw = (
        "[1:29] Participant: Um, so we do just a canned wet dog food, like the chunks ones. "
        "Um, we'll do that. "
        "Um, and I also, I try to give, uh, I put supplements in every one.\n"
        "[2:30] Interviewer: That sounds like a very nutritious meal setup."
    )
    out = normalize_transcript(raw).splitlines()
    assert len(out) == 4
    assert out[0] == (
        "[1:29] Participant: Um, so we do just a canned wet dog food, like the chunks ones."
    )
    assert out[1] == "[1:35] Participant: Um, we'll do that."
    assert out[2] == (
        "[1:37] Participant: Um, and I also, I try to give, uh, I put supplements in every one."
    )
    assert out[3] == "[2:30] Interviewer: That sounds like a very nutritious meal setup."


def test_normalize_preserves_speaker_label_on_every_sentence():
    raw = "[0:10] AI Agent: First question. Second question."
    out = normalize_transcript(raw).splitlines()
    assert all(line.split("] ", 1)[1].startswith("AI Agent: ") for line in out)


def test_normalize_first_sentence_keeps_original_timestamp():
    raw = "[1:00] Participant: One. Two. Three.\n[2:00] Participant: Next turn."
    out = normalize_transcript(raw).splitlines()
    assert out[0].startswith("[1:00] ")


def test_normalize_timestamps_are_monotonic_and_within_turn():
    raw = "[1:00] Participant: One. Two. Three. Four. Five.\n[2:00] Participant: Next."
    lines = parse_transcript_lines(normalize_transcript(raw))
    turn = [line for line in lines if line["text"] != "Next."]
    seconds = [line["seconds"] for line in turn]
    assert seconds == sorted(seconds)
    assert seconds[0] == 60.0
    assert seconds[-1] < 120.0


def test_normalize_never_exceeds_next_line_start():
    # A turn whose estimated speech window would overrun the next line.
    raw = "[1:00] Participant: One. Two.\n[1:03] Participant: Next."
    lines = parse_transcript_lines(normalize_transcript(raw))
    assert all(line["seconds"] <= 63.0 for line in lines)


def test_normalize_applies_early_start_bias():
    # Second sentence's un-biased offset is large enough that the 1s bias is
    # visible: without bias it would land a full second later.
    raw = "[0:00] Participant: " + ("word " * 40).strip() + ". Second sentence here.\n[1:00] Participant: Next."
    lines = parse_transcript_lines(normalize_transcript(raw))
    second = [line for line in lines if line["text"] == "Second sentence here."][0]
    words = 40 + 3
    speech_end = 0.0 + words / 2.0 + 1.0
    unbiased = (40 / words) * speech_end
    assert second["seconds"] == int(max(0.0, unbiased - 1.0))


def test_normalize_is_idempotent():
    raw = (
        "[1:29] Participant: First sentence here. Second sentence here. Third one.\n"
        "[2:30] Interviewer: Done."
    )
    once = normalize_transcript(raw)
    assert normalize_transcript(once) == once


def test_normalize_is_deterministic():
    raw = "[1:29] Participant: First sentence. Second sentence. Third sentence."
    assert normalize_transcript(raw) == normalize_transcript(raw)


def test_normalize_passes_through_unpunctuated_turn():
    raw = "[1:00] Participant: um so like we just keep going and going without any punctuation at all"
    assert normalize_transcript(raw) == raw


def test_normalize_handles_empty_string():
    assert normalize_transcript("") == ""


def test_normalize_last_turn_without_following_line():
    raw = "[1:00] Participant: One sentence. Two sentence."
    out = normalize_transcript(raw).splitlines()
    assert len(out) == 2
    assert out[0] == "[1:00] Participant: One sentence."
    assert out[1].endswith("Participant: Two sentence.")


def test_normalize_splits_on_question_and_exclamation():
    raw = "[0:00] Participant: Really? Yes! Absolutely."
    out = normalize_transcript(raw).splitlines()
    assert len(out) == 3

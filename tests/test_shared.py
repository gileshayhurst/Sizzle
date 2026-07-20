from pathlib import Path
from unittest.mock import patch

import pytest

from shared import (
    MAX_CLIP_SECONDS,
    group_lines_into_segments,
    normalize_transcript,
    parse_transcript_lines,
    read_transcript,
)


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
    assert second["timestamp"] == "0:19"   # unbiased 20s, biased 1s early


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


def test_normalize_timestamps_monotonic_with_repeated_sentences():
    # Repeated short sentences ("Yeah.") can clamp to the same or adjacent
    # seconds. A prior fix here nudged duplicates forward to force unique
    # `raw` lines, but that broke monotonicity for later sentences (their
    # offset is computed independent of any earlier nudge). Reverted; this
    # locks in that plain interpolation stays non-decreasing on its own,
    # since offset is monotonic in word position.
    raw = "[1:00] Participant: Yeah. Yeah. Ok. Yeah.\n[2:00] Participant: Next."
    lines = parse_transcript_lines(normalize_transcript(raw))
    seconds = [line["seconds"] for line in lines]
    assert seconds == sorted(seconds)


def test_normalize_handles_out_of_order_next_start():
    # next_start earlier than the turn's own start (out-of-order/duplicate
    # source timestamps). The window collapses to zero span: every sentence
    # lands on the turn's own start, nothing walks backwards, no crash.
    raw = "[2:00] Participant: One. Two.\n[1:00] Participant: Next."
    lines = parse_transcript_lines(normalize_transcript(raw))
    turn = [line for line in lines if line["text"] in ("One.", "Two.")]
    assert len(turn) == 2
    assert all(line["seconds"] == 120.0 for line in turn)


def test_normalize_passes_through_whitespace_only_text():
    raw = "[0:05] P:   "
    assert normalize_transcript(raw) == raw


def test_normalize_realistic_multi_turn_fixture():
    raw = (
        "[0:00] Interviewer: Thanks for joining today, let's dive right into talking about dog food.\n"
        "[0:05] Participant: Um, so we do just a canned wet dog food, like the chunks ones. "
        "Um, we'll do that. "
        "Um, and I also, I try to give, uh, I put supplements in every one, you know, just to keep him healthy overall.\n"
        "[1:10] Interviewer: That's great, can you tell me a little more about which supplements you use?\n"
        "[1:15] Participant: Yeah. Yeah. So I use a joint supplement, and then I also give him a fish oil pill every single morning. "
        "Um, he really seems to like the taste of it, actually. "
        "It's been really good for his coat too, you know.\n"
        "[2:25] Interviewer: Wonderful, thank you so much for sharing all of that with me today."
    )
    lines = parse_transcript_lines(normalize_transcript(raw))
    turn_starts = [0.0, 5.0, 70.0, 75.0, 145.0]

    # Every original turn boundary survives as some sentence's exact start.
    for ts in turn_starts:
        assert any(line["seconds"] == ts for line in lines)

    # Timestamps are globally monotonic non-decreasing...
    seconds = [line["seconds"] for line in lines]
    assert seconds == sorted(seconds)

    # ...and every sentence lands within its own turn's window, never
    # spilling into the next turn — the interaction this fixture is meant
    # to catch between next_starts and the speech-window cap.
    for line in lines:
        turn_idx = max(i for i, s in enumerate(turn_starts) if s <= line["seconds"])
        turn_end = turn_starts[turn_idx + 1] if turn_idx + 1 < len(turn_starts) else float("inf")
        assert turn_starts[turn_idx] <= line["seconds"] < turn_end


def test_clip_is_capped_at_max_clip_seconds():
    # An unpunctuated 120-word turn: without the cap its estimated speech end
    # is 60s+ past the start.
    text = " ".join(["word"] * 120)
    raw = f"[0:00] Participant: {text}"
    lines = parse_transcript_lines(raw)
    segments = group_lines_into_segments(lines, {raw}, video_duration=300.0)
    assert len(segments) == 1
    start, end = segments[0]
    assert end - start == MAX_CLIP_SECONDS


def test_short_clip_is_not_affected_by_cap():
    raw = "[0:00] Participant: Four short words here."
    lines = parse_transcript_lines(raw)
    segments = group_lines_into_segments(lines, {raw}, video_duration=300.0)
    start, end = segments[0]
    assert end - start < MAX_CLIP_SECONDS


def test_clip_tail_buffer_survives_the_start_bias():
    """The full TAIL_BUFFER must remain after the tail estimate, not be spent
    undoing START_BIAS_SECONDS.

    normalize_transcript records each sentence's start START_BIAS_SECONDS early
    to protect the FIRST word. The clip end was measured from that biased start,
    so the buffer meant to protect the LAST word went entirely on cancelling the
    shift -- an effective margin of TAIL_BUFFER - START_BIAS_SECONDS = 0.0s. Any
    speaker slower than the assumed rate, or pausing for emphasis, was cut off.
    """
    from shared import CLIP_TAIL_RATE, START_BIAS_SECONDS, TAIL_BUFFER

    turn = ("[0:00] Respondent: We looked at three suppliers last quarter. "
            "But the thing that decided it was the service level agreement, "
            "because our previous vendor had burned us badly on uptime.")
    lines = parse_transcript_lines(
        normalize_transcript(turn + "\n[1:00] Interviewer: Got it."))
    spoken = [l for l in lines if not l["is_interviewer"]]
    start, end = group_lines_into_segments(lines, {l["raw"] for l in spoken})[0]

    last = spoken[-1]
    words = len(last["text"].split())
    # Where speech actually starts, with the recording bias removed.
    speech_start = last["seconds"] + START_BIAS_SECONDS
    margin = end - (speech_start + words / CLIP_TAIL_RATE)
    assert margin == pytest.approx(TAIL_BUFFER), (
        f"tail margin is {margin}s, expected the full TAIL_BUFFER of "
        f"{TAIL_BUFFER}s; a margin near 0 means the start bias is being "
        f"double-counted and slow speakers get cut off"
    )


def test_read_transcript_normalizes_on_read(tmp_path):
    txt = tmp_path / "interview.txt"
    txt.write_text(
        "[1:00] Participant: First sentence here. Second sentence here.",
        encoding="utf-8",
    )
    out = read_transcript(txt)
    assert len(out.splitlines()) == 2
    # The file on disk is client data and must not be rewritten.
    assert len(txt.read_text(encoding="utf-8").splitlines()) == 1


def test_both_services_read_transcripts_identically(tmp_path):
    """The main app and the generator service each read the same .txt
    independently. A line's raw string is the selection identity passed between
    them, so their parsed output must be byte-identical — if one skips
    normalization, selection silently matches nothing and the reel comes out
    empty with no error."""
    import app as main_app
    import generator_app

    txt = tmp_path / "interview.txt"
    txt.write_text(
        "[1:00] Participant: First sentence here. Second sentence here. Third one here.\n"
        "[2:00] Interviewer: Thanks for your time.",
        encoding="utf-8",
    )

    assert main_app._read_transcript(txt) == generator_app._read_transcript(txt)


def test_selection_identity_survives_analyze_to_generate(tmp_path):
    """A raw line string selected via the main app's read path must match a line
    the generator's read path produces, and group into a real clip."""
    import app as main_app
    import generator_app

    txt = tmp_path / "interview.txt"
    txt.write_text(
        "[1:00] Participant: First sentence here. Second sentence here. Third one here.\n"
        "[2:00] Interviewer: Thanks for your time.",
        encoding="utf-8",
    )
    app_side = parse_transcript_lines(main_app._read_transcript(txt))
    generator_side = parse_transcript_lines(generator_app._read_transcript(txt))

    selected = {app_side[1]["raw"]}
    matched = [line for line in generator_side if line["raw"] in selected]
    assert len(matched) == 1
    assert matched[0]["text"] == "Second sentence here."

    segments = group_lines_into_segments(generator_side, selected, video_duration=300.0)
    assert len(segments) == 1
    start, end = segments[0]
    assert end > start
    assert end - start <= MAX_CLIP_SECONDS


def test_parse_plain_line_has_no_end():
    lines = parse_transcript_lines("[0:05] Participant: Hello there.")
    assert lines[0]["end_seconds"] is None
    assert lines[0]["seconds"] == 5.0


def test_parse_rich_line_carries_end():
    lines = parse_transcript_lines("[0:05-0:12] Participant: Hello there.")
    assert lines[0]["seconds"] == 5.0
    assert lines[0]["end_seconds"] == 12.0
    assert lines[0]["text"] == "Hello there."
    assert lines[0]["speaker"] == "Participant"


def test_parse_rich_line_with_padded_minutes():
    # Real Forven exports zero-pad: [00:05-00:12]
    lines = parse_transcript_lines("[00:05-00:12] Participant: Hello there.")
    assert lines[0]["seconds"] == 5.0
    assert lines[0]["end_seconds"] == 12.0


def test_parse_malformed_end_is_treated_as_absent():
    # end <= start cannot be real; treat the line as plain rather than emit a
    # zero-length or negative clip.
    assert parse_transcript_lines("[0:12-0:12] P: Hi.")[0]["end_seconds"] is None
    assert parse_transcript_lines("[0:12-0:05] P: Hi.")[0]["end_seconds"] is None


def test_normalize_still_splits_after_regex_change():
    # Guards the group-index shift: normalize_transcript uses the same regex.
    out = normalize_transcript("[0:00] Participant: First sentence. Second sentence.")
    assert out.count("Participant:") == 2
    assert "First sentence." in out and "Second sentence." in out


def test_normalize_handles_rich_format_lines_without_corruption():
    """A rich line must survive normalization with speaker and text intact.

    Reachable in production: a MIXED file (some lines with ends, some without)
    is plain tier, so it IS normalized — and its rich lines pass through here.
    A group-index regression does not crash on rich input, it silently reads
    the end timestamp as the speaker and the speaker as the text.
    """
    out = normalize_transcript(
        "[0:00-0:20] Participant: First sentence. Second sentence.\n"
        "[1:00] Interviewer: Next?"
    )
    lines = parse_transcript_lines(out)
    respondent = [l for l in lines if not l["is_interviewer"]]
    assert len(respondent) == 2, "multi-sentence rich turn should split in two"
    for line in respondent:
        assert line["speaker"] == "Participant"
    assert respondent[0]["text"] == "First sentence."
    assert respondent[1]["text"] == "Second sentence."

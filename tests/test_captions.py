from captions import build_webvtt, collect_caption_lines, WEBVTT_MIME


def _seg(start, end, lines):
    return {"start_sec": start, "end_sec": end, "caption_lines": lines}


def test_mime_constant():
    assert WEBVTT_MIME == "text/vtt"


def test_single_segment_single_line_offsets_after_title_card():
    # Clip 0:10–0:16 (6s). Title card = 5s. A line at source 0:10 (clip start)
    # lands at reel t = 5.0; its cue runs to the clip end (5.0 + 6.0 = 11.0).
    vtt = build_webvtt([_seg(10.0, 16.0, [{"text": "Hello there", "seconds": 10.0}])],
                       title_card_duration=5.0)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:05.000 --> 00:00:11.000\nHello there" in vtt


def test_line_offset_within_clip():
    # Line at source 12.0 in a clip starting at 10.0 -> 2s into the clip ->
    # reel t = 5.0 + 2.0 = 7.0.
    vtt = build_webvtt([_seg(10.0, 16.0, [{"text": "Later line", "seconds": 12.0}])])
    assert "00:00:07.000 --> 00:00:11.000\nLater line" in vtt


def test_consecutive_lines_end_at_next_line_start():
    seg = _seg(10.0, 16.0, [
        {"text": "First", "seconds": 10.0},
        {"text": "Second", "seconds": 13.0},
    ])
    vtt = build_webvtt([seg])
    assert "00:00:05.000 --> 00:00:08.000\nFirst" in vtt        # ends at next line
    assert "00:00:08.000 --> 00:00:11.000\nSecond" in vtt       # ends at clip end


def test_second_segment_starts_after_first_segments_title_and_clip():
    # Seg1: title 5 + clip 6 = ends at reel 11. Seg2 title starts at 11,
    # clip starts at 16. A line at seg2 clip start -> reel 16.
    segs = [
        _seg(10.0, 16.0, [{"text": "one", "seconds": 10.0}]),
        _seg(30.0, 34.0, [{"text": "two", "seconds": 30.0}]),
    ]
    vtt = build_webvtt(segs)
    assert "00:00:16.000 --> 00:00:20.000\ntwo" in vtt


def test_cue_clamped_to_clip_end():
    # A line whose source time sits past end_sec still clamps into the clip.
    seg = _seg(10.0, 12.0, [{"text": "edge", "seconds": 11.9}])
    vtt = build_webvtt([seg])
    # clip 2s -> ends at reel 7.0; start 5.0 + 1.9 = 6.9, end clamps to 7.0
    assert "00:00:06.900 --> 00:00:07.000\nedge" in vtt


def test_no_lines_returns_none():
    assert build_webvtt([_seg(10.0, 16.0, [])]) is None
    assert build_webvtt([]) is None


def test_blank_text_lines_skipped():
    seg = _seg(10.0, 16.0, [{"text": "   ", "seconds": 10.0}])
    assert build_webvtt([seg]) is None


def test_collect_caption_lines_selected_respondent_in_range():
    all_lines = [
        {"raw": "a", "text": "picked", "seconds": 10.0, "is_interviewer": False},
        {"raw": "b", "text": "not picked", "seconds": 11.0, "is_interviewer": False},
        {"raw": "c", "text": "interviewer", "seconds": 12.0, "is_interviewer": True},
        {"raw": "d", "text": "out of range", "seconds": 99.0, "is_interviewer": False},
    ]
    selected = {"a", "c", "d"}
    got = collect_caption_lines(all_lines, selected, 10.0, 16.0)
    assert got == [{"text": "picked", "seconds": 10.0}]

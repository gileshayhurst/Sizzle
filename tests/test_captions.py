from captions import build_webvtt, collect_caption_lines, WEBVTT_MIME, _chunk_line


def _seg(start, end, lines):
    return {"start_sec": start, "end_sec": end, "caption_lines": lines}


def _vlen(cue):
    """Visible chars in a cue (excludes the joining newline)."""
    return len(cue) - cue.count("\n")


# A real ~140-char participant turn used to exercise chunking.
LONG = ("When we give him kibble, it doesn't do anything for me, but when we're "
        "able to give him some food from the kitchen, that makes me happy.")


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


def test_chunk_line_short_text_single_unchanged_cue():
    assert _chunk_line("Hello there") == ["Hello there"]


def test_chunk_line_empty_or_whitespace_returns_empty():
    assert _chunk_line("") == []
    assert _chunk_line("   ") == []


def test_chunk_line_long_text_yields_multiple_two_line_cues():
    cues = _chunk_line(LONG)
    assert len(cues) > 1
    for c in cues:
        assert _vlen(c) <= 84                 # at most two 42-char lines
        lines = c.split("\n")
        assert len(lines) <= 2
        for ln in lines:
            assert len(ln) <= 42


def test_chunk_line_long_word_kept_whole():
    word = "x" * 60                            # single word longer than a line
    assert _chunk_line(word) == [word]         # not split mid-word


def test_chunk_line_reassembles_all_words_in_order():
    words = " ".join(c.replace("\n", " ") for c in _chunk_line(LONG)).split()
    assert words == LONG.split()


def test_build_webvtt_chunks_long_line_into_multiple_cues():
    seg = _seg(0.0, 20.0, [{"text": LONG, "seconds": 0.0}])
    vtt = build_webvtt([seg], title_card_duration=5.0)
    assert vtt.count("-->") == len(_chunk_line(LONG))
    assert vtt.count("-->") > 1
    # First cue starts at the clip start (reel t = title 5s).
    assert "00:00:05.000 -->" in vtt


def test_build_webvtt_caps_cue_at_max_6s():
    # One short chunk, huge trailing window -> capped at 6s, not stretched.
    seg = _seg(0.0, 300.0, [{"text": "brief", "seconds": 0.0}])
    vtt = build_webvtt([seg], title_card_duration=5.0)
    assert "00:00:05.000 --> 00:00:11.000\nbrief" in vtt


def test_build_webvtt_chunks_partition_window_contiguously():
    # Two chunks of one line partition [5.0, 5.0+window] in order without overlap.
    seg = _seg(0.0, 8.0, [{"text": LONG, "seconds": 0.0}])
    vtt = build_webvtt([seg], title_card_duration=5.0)
    # Parse cue start/end pairs.
    times = []
    for block in vtt.split("\n\n"):
        for ln in block.split("\n"):
            if "-->" in ln:
                a, b = ln.split("-->")
                times.append((a.strip(), b.strip()))
    # Each cue's end is >= its start; consecutive cues are ordered.
    assert all(a <= b for a, b in times)
    starts = [a for a, _ in times]
    assert starts == sorted(starts)


def test_collect_caption_lines_selected_respondent_in_range():
    all_lines = [
        {"raw": "a", "text": "picked", "seconds": 10.0, "is_interviewer": False},
        {"raw": "b", "text": "not picked", "seconds": 11.0, "is_interviewer": False},
        {"raw": "c", "text": "interviewer", "seconds": 12.0, "is_interviewer": True},
        {"raw": "d", "text": "out of range", "seconds": 99.0, "is_interviewer": False},
    ]
    selected = {"a", "c", "d"}
    got = collect_caption_lines(all_lines, selected, 10.0, 16.0)
    assert got == [{"text": "picked", "seconds": 10.0, "end_seconds": None}]


def test_collect_caption_lines_carries_end_seconds():
    from shared import parse_transcript_lines
    from captions import collect_caption_lines
    lines = parse_transcript_lines("[0:05-0:09] Participant: A short answer.")
    out = collect_caption_lines(lines, {lines[0]["raw"]}, 0.0, 60.0)
    assert out[0]["end_seconds"] == 9.0


def test_rich_cue_ends_at_the_sentence_end_not_the_next_line():
    from captions import build_webvtt
    segments = [{
        "start_sec": 0.0,
        "end_sec": 30.0,
        "caption_lines": [
            {"text": "First answer.", "seconds": 0.0, "end_seconds": 4.0},
            {"text": "Second answer.", "seconds": 20.0, "end_seconds": 24.0},
        ],
    }]
    vtt = build_webvtt(segments, title_card_duration=0.0)
    # The first cue must end at 4s (its real end), NOT at 20s (the next line's
    # start), which is what proportional windowing produced.
    assert "00:00:00.000 --> 00:00:04.000" in vtt


def test_plain_cue_still_runs_to_the_next_line():
    from captions import build_webvtt
    segments = [{
        "start_sec": 0.0,
        "end_sec": 30.0,
        "caption_lines": [
            {"text": "First answer.", "seconds": 0.0},
            {"text": "Second answer.", "seconds": 20.0},
        ],
    }]
    vtt = build_webvtt(segments, title_card_duration=0.0)
    assert "00:00:00.000 --> 00:00:06.000" in vtt  # capped by MAX_CUE_SEC

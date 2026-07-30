from encoder.core.emit import measured, rich, stats


def sentence(text, start, end, role="Participant", anchor="exact", confidence=1.0):
    return {"role": role, "text": text, "start": start, "end": end,
            "anchor": anchor, "confidence": confidence}


def test_rich_formats_a_single_line():
    assert rich([sentence("He's a Corgi mix.", 13.4, 15.6)]) == \
        "[0:13-0:16] Participant: He's a Corgi mix."


def test_rich_start_truncates_and_end_rounds_up():
    """Truncating an end clips the speaker's final word -- ends must round UP."""
    assert rich([sentence("Yeah.", 5.9, 6.1)]) == "[0:05-0:07] Participant: Yeah."


def test_rich_end_is_exact_when_already_whole():
    assert rich([sentence("Yeah.", 4.0, 6.0)]) == "[0:04-0:06] Participant: Yeah."


def test_rich_clamps_overlap_with_next_line():
    """Whole-second rounding can push an end past the next start; clamp it."""
    result = rich([
        sentence("He's a Corgi mix.", 13.4, 15.6),
        sentence("We got him as a rescue.", 15.2, 22.1),
    ])
    assert result.splitlines()[0] == "[0:13-0:15] Participant: He's a Corgi mix."


def test_rich_keeps_overlap_when_clamping_would_collapse_the_line():
    """Never emit a zero- or negative-length line to satisfy monotonicity."""
    result = rich([
        sentence("Yeah.", 10.2, 10.9),
        sentence("Right.", 10.4, 11.5),
    ])
    assert result.splitlines()[0] == "[0:10-0:11] Participant: Yeah."


def test_rich_minutes_format():
    assert rich([sentence("Okay.", 125.0, 128.0)]) == "[2:05-2:08] Participant: Okay."


def test_rich_preserves_role():
    assert rich([sentence("Hello.", 0.0, 1.0, role="Interviewer")]) == \
        "[0:00-0:01] Interviewer: Hello."


def test_rich_empty():
    assert rich([]) == ""


def test_stats_counts_anchor_kinds():
    result = stats([
        sentence("a", 0, 1),
        sentence("b", 1, 2, anchor="partial", confidence=0.5),
        sentence("c", 2, 3, anchor="none", confidence=0.0),
    ], 0.9612)
    assert result == {
        "sentences": 3, "emitted": 1, "dropped": 2,
        "exact": 1, "partial": 1, "unanchored": 1, "match_rate": 0.9612,
    }


# ── measured: never emit a timing we did not measure ──────────────────────────

def test_measured_keeps_only_fully_anchored_sentences():
    """An interpolated span is the very thing D1 rejected; it must not ship."""
    aligned = [
        sentence("real", 0, 1),
        sentence("guessed", 1, 2, anchor="none", confidence=0.0),
        sentence("half", 2, 3, anchor="partial", confidence=0.4),
    ]
    assert [s["text"] for s in measured(aligned)] == ["real"]


def test_measured_of_nothing_anchored_is_empty():
    aligned = [sentence("a", 0, 1, anchor="none"), sentence("b", 1, 2, anchor="none")]
    assert measured(aligned) == []


def test_rich_of_measured_lines_only():
    aligned = [
        sentence("Kept.", 4.0, 6.0),
        sentence("Invented.", 6.0, 9.0, anchor="none"),
        sentence("Also kept.", 10.0, 12.0),
    ]
    assert rich(measured(aligned)).splitlines() == [
        "[0:04-0:06] Participant: Kept.",
        "[0:10-0:12] Participant: Also kept.",
    ]

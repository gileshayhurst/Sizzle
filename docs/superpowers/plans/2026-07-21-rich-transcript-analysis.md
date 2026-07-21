# Rich-transcript analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make analysis of rich transcripts produce measurably tighter, more succinct clip selections by teaching the anchor-expander, overlap-aware line matcher, and Claude prompt to understand and exploit exact per-sentence timestamps.

**Architecture:** Three changes to existing modules: `shared.py` gains `expand_anchors` (C1) and `lines_in_range` (B); `claude_client.py` gains a tier-aware system-prompt clause (A); `app.py` wires them together. All downstream consumers (`group_lines_into_segments`, `captions.py`, `generator_app`) are unchanged because the changes are fully absorbed at the `read_transcript` / line-matching layer.

**Tech Stack:** Python 3.11, pytest, existing `shared.py` / `claude_client.py` / `app.py`.

---

### Storage note: no localStorage bump needed

The spec mentioned bumping `sizzle_sel_v2_` → `sizzle_sel_v3_`. That bump already landed in commit `64e398d`. `expand_anchors` is a no-op on all currently-existing transcripts (plain tier and sentence-level rich), so no stale keys will be created by this work. A v4 bump would only be needed when anchored transcripts first appear in production — do it then, when the format ships.

---

### Task 1: C1 — `expand_anchors` in `shared.py`

**Files:**
- Modify: `shared.py` (add `_INLINE_ANCHOR_RE`, `expand_anchors`, update `read_transcript`)
- Test: `tests/test_shared.py` (append to existing file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
# ---------------------------------------------------------------------------
# expand_anchors
# ---------------------------------------------------------------------------

from shared import expand_anchors


def test_expand_anchors_plain_line_passthrough():
    """Lines with no outer end timestamp are returned unchanged."""
    raw = "[0:14] Participant: Hello world."
    assert expand_anchors(raw) == raw


def test_expand_anchors_rich_no_inline_anchors_passthrough():
    """Rich line with no inline anchors is returned unchanged."""
    raw = "[0:14-0:19] Participant: The service was absolutely exceptional."
    assert expand_anchors(raw) == raw


def test_expand_anchors_three_anchors():
    """A 3-anchor turn expands into 3 correctly-bounded rich lines."""
    raw = "[0:04-0:20] Participant: text about thing. [0:09] More detail here. [0:14] Final point."
    result = expand_anchors(raw)
    lines = result.splitlines()
    assert len(lines) == 3
    assert lines[0] == "[0:04-0:09] Participant: text about thing."
    assert lines[1] == "[0:09-0:14] Participant: More detail here."
    assert lines[2] == "[0:14-0:20] Participant: Final point."


def test_expand_anchors_empty_leading_chunk_absorbed():
    """Anchor at position 0 creates empty first chunk; next chunk absorbs its span."""
    raw = "[0:04-0:20] Participant: [0:09] More detail here. [0:14] Final point."
    result = expand_anchors(raw)
    lines = result.splitlines()
    assert len(lines) == 2
    # "More detail here." absorbs [0:04, 0:09] span, starts at 0:04
    assert lines[0] == "[0:04-0:14] Participant: More detail here."
    assert lines[1] == "[0:14-0:20] Participant: Final point."


def test_expand_anchors_empty_trailing_chunk_absorbed():
    """Trailing anchor with no text; previous chunk absorbs through to line_end."""
    raw = "[0:04-0:20] Participant: text about thing. [0:09] More detail here. [0:14]"
    result = expand_anchors(raw)
    lines = result.splitlines()
    assert len(lines) == 2
    assert lines[0] == "[0:04-0:09] Participant: text about thing."
    # "More detail here." absorbs trailing span, ends at 0:20
    assert lines[1] == "[0:09-0:20] Participant: More detail here."


def test_expand_anchors_anchor_outside_window_falls_back():
    """Anchor outside [line_start, line_end] keeps the whole turn unchanged."""
    # 0:25 is outside [0:04, 0:20]
    raw = "[0:04-0:20] Participant: text [0:25] more."
    assert expand_anchors(raw) == raw


def test_expand_anchors_non_monotonic_falls_back():
    """Non-monotonic anchors keep the whole turn unchanged."""
    raw = "[0:04-0:20] Participant: text [0:14] more [0:09] still."
    assert expand_anchors(raw) == raw


def test_expand_anchors_idempotent():
    """expand_anchors(expand_anchors(x)) == expand_anchors(x)."""
    raw = "[0:04-0:20] Participant: text [0:09] more [0:14] still."
    once = expand_anchors(raw)
    assert expand_anchors(once) == once


def test_expand_anchors_multi_line_passthrough_intact():
    """Multi-line input: only anchored lines change; others pass through."""
    raw = "[0:00] Interviewer: How was it?\n[0:04-0:20] Participant: Good. [0:09] Very good."
    result = expand_anchors(raw)
    lines = result.splitlines()
    assert lines[0] == "[0:00] Interviewer: How was it?"
    assert lines[1] == "[0:04-0:09] Participant: Good."
    assert lines[2] == "[0:09-0:20] Participant: Very good."
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "expand_anchors" -v
```

Expected: all `expand_anchors` tests FAIL with `ImportError: cannot import name 'expand_anchors'`

- [ ] **Step 3: Implement `expand_anchors` in `shared.py`**

Add after the `_SENTENCE_SPLIT_RE` definition (around line 19) in `shared.py`:

```python
# Inline anchor inside an already-parsed rich line: [M:SS] embedded in text.
# Used by expand_anchors to split anchored turn lines into sentence-level rich lines.
_INLINE_ANCHOR_RE = _re.compile(r'\[(\d+:\d{2})\]')
```

Add `expand_anchors` just before the `normalize_transcript` function:

```python
def expand_anchors(raw_text: str) -> str:
    """Split anchored turn lines into consecutive sentence-level rich lines.

    An anchored line like:
        [0:04-0:20] Participant: text [0:09] more [0:14] still more

    becomes:
        [0:04-0:09] Participant: text
        [0:09-0:14] Participant: more
        [0:14-0:20] Participant: still more

    Lines with no inline anchors (including plain-tier lines and sentence-level
    rich lines) are returned unchanged, making the function idempotent.

    Malformed anchors (outside the line window, non-monotonic) cause the whole
    line to be returned unchanged — never fabricate a boundary.
    """
    out: list[str] = []
    for raw in raw_text.splitlines():
        stripped = raw.strip()
        m = _LINE_RE.match(stripped)
        # Only expand lines that have a valid outer end timestamp (rich lines).
        if not m or not m.group(2):
            out.append(raw)
            continue

        start_ts, end_ts = m.group(1), m.group(2)
        speaker, text = m.group(3).strip(), m.group(4)
        line_start = parse_timestamp_to_seconds(start_ts)
        line_end = parse_timestamp_to_seconds(end_ts)

        # Split text on inline anchors. re.split with a capturing group
        # interleaves captured timestamps: ["a ", "0:09", " b ", "0:14", " c"]
        parts = _INLINE_ANCHOR_RE.split(text)
        text_chunks = parts[0::2]   # ["a ", " b ", " c"]
        anchor_strs = parts[1::2]   # ["0:09", "0:14"]

        if not anchor_strs:
            out.append(raw)
            continue

        anchor_secs = [parse_timestamp_to_seconds(a) for a in anchor_strs]

        # Validate: strictly increasing, each within [line_start, line_end).
        valid = (
            anchor_secs[0] >= line_start
            and anchor_secs[-1] < line_end
            and all(anchor_secs[i] < anchor_secs[i + 1] for i in range(len(anchor_secs) - 1))
        )
        if not valid:
            out.append(raw)
            continue

        # boundaries[i] is the start of text_chunks[i]; boundaries[-1] is line_end.
        boundaries = [line_start] + anchor_secs + [line_end]

        last_non_empty = max(
            (i for i, c in enumerate(text_chunks) if c.strip()), default=None
        )
        if last_non_empty is None:
            out.append(raw)
            continue

        # Build result lines. running_start tracks the accumulated start for
        # the current non-empty chunk — empty chunks don't advance it, so the
        # next non-empty chunk absorbs the empty span (spec: "neighbour absorbs").
        running_start = line_start
        result_lines: list[str] = []
        for i, chunk_text in enumerate(text_chunks):
            chunk_stripped = chunk_text.strip()
            # Last non-empty chunk always ends at line_end (absorbs trailing empties).
            chunk_end = line_end if i == last_non_empty else boundaries[i + 1]
            if chunk_stripped:
                s_ts = _seconds_to_timestamp(running_start)
                e_ts = _seconds_to_timestamp(chunk_end)
                result_lines.append(f"[{s_ts}-{e_ts}] {speaker}: {chunk_stripped}")
                running_start = boundaries[i + 1]
            # Empty chunk: running_start stays, next non-empty absorbs the span.

        out.extend(result_lines if result_lines else [raw])

    return "\n".join(out)
```

- [ ] **Step 4: Update `read_transcript` to call `expand_anchors` for rich tier**

In `shared.py`, find `read_transcript` (currently ends `return normalize_transcript(text)`). Replace the function body:

```python
def read_transcript(txt_path: str | _Path) -> str:
    """Read a transcript sidecar and return it ready for parsing.

    Every transcript read in both services goes through here so no code path
    can accidentally work with un-normalized turn-level lines -- the `raw`
    strings are the selection identity shared across services, so they must
    match everywhere. (Same invariant as filter_generated_reels: if you add a
    new code path that reads a .txt, call this.) The file on disk is never
    modified; it is client data.

    Routes on tier:
      rich  -> expand_anchors: expands any inline anchors into sentence-level
               rich lines; sentence-level rich lines pass through unchanged.
      plain -> normalize_transcript: splits turn-level lines by sentence with
               interpolated timestamps, as before.
    """
    text = _Path(txt_path).read_text(encoding="utf-8")
    if transcript_tier(parse_transcript_lines(text)) == "rich":
        return expand_anchors(text)
    return normalize_transcript(text)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "expand_anchors" -v
```

Expected: all 9 `expand_anchors` tests PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS (expand_anchors is a no-op on plain and sentence-level rich, so existing tests are unaffected)

- [ ] **Step 7: Commit**

```
git add shared.py tests/test_shared.py
git commit -m "feat(shared): add expand_anchors for anchored turn-level transcripts (C1)"
```

---

### Task 2: B — `lines_in_range` in `shared.py`

**Files:**
- Modify: `shared.py` (add `MIN_LINE_OVERLAP_RATIO`, `lines_in_range`)
- Test: `tests/test_shared.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
# ---------------------------------------------------------------------------
# lines_in_range
# ---------------------------------------------------------------------------

from shared import lines_in_range


def _make_line(seconds, end_seconds=None, is_interviewer=False):
    ts = f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
    end_ts = (f"{int(end_seconds) // 60}:{int(end_seconds) % 60:02d}"
              if end_seconds is not None else None)
    raw = (f"[{ts}-{end_ts}] Speaker: text" if end_ts
           else f"[{ts}] Speaker: text")
    return {
        "raw": raw,
        "seconds": float(seconds),
        "end_seconds": float(end_seconds) if end_seconds is not None else None,
        "is_interviewer": is_interviewer,
    }


def test_lines_in_range_plain_includes_start_in_range():
    """Plain tier: line start inside [start-0.5, end+0.5] is included."""
    lines = [_make_line(10)]  # no end_seconds → plain tier
    result = lines_in_range(lines, 9.6, 20.0)
    assert len(result) == 1


def test_lines_in_range_plain_excludes_start_outside_range():
    """Plain tier: line start well outside the range is excluded."""
    lines = [_make_line(5)]
    result = lines_in_range(lines, 10.0, 20.0)
    assert len(result) == 0


def test_lines_in_range_plain_excludes_interviewer():
    """Interviewer lines are always excluded regardless of tier."""
    lines = [_make_line(10, is_interviewer=True)]
    result = lines_in_range(lines, 9.0, 20.0)
    assert len(result) == 0


def test_lines_in_range_rich_long_line_grazing_range_excluded():
    """Rich tier: a 34s line whose speech overlaps the range by <50% is excluded."""
    # Line: [0:24-0:58], Claude range: [0:24-0:26]. Overlap = 2s of 34s = 5.9% < 50%.
    lines = [_make_line(24, end_seconds=58)]
    result = lines_in_range(lines, 24.0, 26.0)
    assert len(result) == 0


def test_lines_in_range_rich_mostly_inside_included():
    """Rich tier: a line starting just before the range but 95% inside is included."""
    # Line: [0:13-0:19], Claude range: [0:14-0:20]. Overlap = 5s of 6s = 83% > 50%.
    lines = [_make_line(13, end_seconds=19)]
    result = lines_in_range(lines, 14.0, 20.0)
    assert len(result) == 1


def test_lines_in_range_rich_exactly_half_overlap_excluded():
    """Rich tier: exactly 50% overlap is excluded (threshold is strictly greater than)."""
    # Line: [0:10-0:20], Claude range: [0:15-0:25]. Overlap = 5s of 10s = exactly 50%.
    lines = [_make_line(10, end_seconds=20)]
    result = lines_in_range(lines, 15.0, 25.0)
    assert len(result) == 0


def test_lines_in_range_rich_fallback_for_missing_end():
    """Rich file but a line missing end_seconds falls back to plain predicate."""
    # Mix: one well-formed rich line, one missing end_seconds (e.g. interviewer with no end)
    rich_line = _make_line(10, end_seconds=20)
    no_end_line = _make_line(12, end_seconds=None)  # missing end → plain fallback
    # plain predicate: 12 is inside [11.5, 20.5] → included
    lines = [rich_line, no_end_line]
    result = lines_in_range(lines, 12.0, 20.0)
    # no_end_line at 12s: plain predicate 12 >= 12-0.5=11.5 ✓ and 12 <= 20+0.5 ✓ → included
    assert no_end_line in result


def test_lines_in_range_plain_tier_unchanged_regression():
    """Plain predicate behaviour is exactly the same as the old inline comprehension."""
    lines = [
        _make_line(9),   # inside (9 >= 8.5)
        _make_line(20),  # inside (20 <= 20.5)
        _make_line(5),   # outside
        _make_line(25),  # outside
    ]
    result = lines_in_range(lines, 9.0, 20.0)
    assert set(r["seconds"] for r in result) == {9.0, 20.0}
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "lines_in_range" -v
```

Expected: all `lines_in_range` tests FAIL with `ImportError: cannot import name 'lines_in_range'`

- [ ] **Step 3: Implement `lines_in_range` in `shared.py`**

Add after the `transcript_tier` function:

```python
# Lines whose speech overlaps Claude's returned range by less than this fraction
# of the line's own duration are excluded in rich tier. 0.5 means the majority
# of a line's speech must fall inside the range to be selected.
# ponytail: 0.5 is a calibrated starting point — tune against real rich transcripts
# if clips are too short (raise it) or too long (lower it).
MIN_LINE_OVERLAP_RATIO = 0.5


def lines_in_range(
    all_lines: list[dict], start_sec: float, end_sec: float
) -> list[dict]:
    """Return respondent lines whose speech falls within Claude's returned range.

    Plain tier: a line is included when its start falls within
    [start_sec - 0.5, end_sec + 0.5]. This is the existing predicate, unchanged.

    Rich tier: a line is included when its speech interval
    [seconds, end_seconds] overlaps Claude's range by more than
    MIN_LINE_OVERLAP_RATIO of the line's own duration. This excludes a 34s line
    that merely grazes the range at its first second, and includes a line that
    starts just before the range but lies almost entirely inside it.

    A line with end_seconds=None inside a rich file falls back to the plain
    predicate (possible on interviewer lines whose ends are rarely present).

    Interviewer lines are always excluded.
    """
    tier = transcript_tier(all_lines)
    result = []
    for line in all_lines:
        if line.get("is_interviewer"):
            continue
        if tier == "rich" and line.get("end_seconds") is not None:
            line_dur = line["end_seconds"] - line["seconds"]
            if line_dur <= 0:
                continue
            overlap = max(0.0, min(line["end_seconds"], end_sec) - max(line["seconds"], start_sec))
            if overlap / line_dur > MIN_LINE_OVERLAP_RATIO:
                result.append(line)
        else:
            if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                result.append(line)
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "lines_in_range" -v
```

Expected: all 8 `lines_in_range` tests PASS

- [ ] **Step 5: Run the full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```
git add shared.py tests/test_shared.py
git commit -m "feat(shared): add lines_in_range for overlap-aware transcript line matching (B)"
```

---

### Task 3: Wire `lines_in_range` into `app.py`

**Files:**
- Modify: `app.py` (update imports, replace inline match predicate in `_analyze_one`)
- Test: `tests/test_app.py` (append a regression test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_analyze_uses_lines_in_range_not_inline_predicate(tmp_path):
    """_run_analyze must not use the old inline start-only predicate; it must
    call lines_in_range from shared so plain-tier results are identical to before."""
    import app as app_module
    # A plain-tier transcript: one line at 0:10
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:10] Participant: The food was amazing.", encoding="utf-8")

    with (
        patch("app.scan_videos", return_value=[tmp_path / "vid.mp4"]),
        patch("app.query_claude", return_value="0:10-0:15|8"),
        patch("app._filter_generated_reels", side_effect=lambda v: v),
        patch("storage.load_library", return_value=[]),
    ):
        result = app_module._run_analyze(str(tmp_path), "food quality")

    assert "vid.mp4" in result.get("segments", {})
    segs = result["segments"]["vid.mp4"]
    assert len(segs) == 1
    assert "[0:10] Participant: The food was amazing." in segs[0]["lines"]
```

- [ ] **Step 2: Run the test to verify it fails**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_analyze_uses_lines_in_range_not_inline_predicate -v
```

Expected: FAIL (function not yet wired)

- [ ] **Step 3: Update `app.py` imports**

Find the `from shared import (` block (around line 38) and add `lines_in_range`:

```python
from shared import (
    read_transcript as _read_transcript,
    parse_transcript_lines as _parse_transcript_lines,
    filter_generated_reels as _filter_generated_reels,
    group_lines_into_segments as _group_lines_into_segments,
    lines_in_range as _lines_in_range,
)
```

- [ ] **Step 4: Replace the inline match predicate in `_analyze_one`**

In `app.py`, find the inline list comprehension inside `_analyze_one` (around line 254):

```python
            seg_line_dicts = [
                line for line in all_lines
                if not line.get("is_interviewer")  # analyze never auto-selects the interviewer
                and start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5
            ]
```

Replace with:

```python
            seg_line_dicts = _lines_in_range(all_lines, start_sec, end_sec)
```

Also update the comment on the line below it (the one starting `# The create-screen length estimate`) — change `"Claude's end timestamp is only the *start* of the last line"` to `"Claude's range is used to match lines; the clip duration comes from group_lines_into_segments"`:

```python
            # The create-screen length estimate must match the clip the generator
            # will actually cut. The clip duration comes from the same shared
            # grouping the generator uses (shared.group_lines_into_segments).
```

- [ ] **Step 5: Run the new test**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_analyze_uses_lines_in_range_not_inline_predicate -v
```

Expected: PASS

- [ ] **Step 6: Run the full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat(app): wire lines_in_range into _run_analyze, replacing inline start-only predicate"
```

---

### Task 4: A — Tier-aware Claude prompt

**Files:**
- Modify: `claude_client.py` (add `_RICH_PROMPT_CLAUSE`, update `query_claude` signature)
- Modify: `app.py` (add `transcript_tier` import, detect tier, pass to `query_claude`)
- Test: `tests/test_claude_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_client.py`:

```python
def test_rich_tier_appends_rich_clause_to_system_prompt():
    """query_claude with tier='rich' sends a system prompt that mentions end timestamps."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("transcript", "prompt", tier="rich")
        system = mock_client.messages.create.call_args.kwargs["system"]
    assert "end timestamp" in system.lower() or "start and end" in system.lower()


def test_plain_tier_system_prompt_unchanged():
    """query_claude with default tier='plain' sends the same system prompt as before."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("transcript", "prompt")
        system_plain = mock_client.messages.create.call_args.kwargs["system"]
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("transcript", "prompt", tier="plain")
        system_explicit_plain = mock_client.messages.create.call_args.kwargs["system"]
    assert system_plain == system_explicit_plain


def test_rich_system_prompt_is_superset_of_plain():
    """Rich system prompt contains all of the plain prompt's instructions."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p")
        plain_system = mock_client.messages.create.call_args.kwargs["system"]
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p", tier="rich")
        rich_system = mock_client.messages.create.call_args.kwargs["system"]
    assert plain_system in rich_system
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_claude_client.py -k "rich_tier or plain_tier or superset" -v
```

Expected: FAIL (`query_claude() got an unexpected keyword argument 'tier'`)

- [ ] **Step 3: Update `claude_client.py`**

Replace the entire file:

```python
import anthropic

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify the most compelling short moments where the speaker directly and substantively addresses the prompt topic, and rate how compelling each one is.

Return ONLY one segment per line in the format: M:SS-M:SS|N
where N is an integer from 1 to 10 rating how compelling the evidence is.
If no relevant segments exist, return exactly: none

Score rubric:
- 9-10: direct, vivid, quotable evidence — the strongest possible moment on the topic.
- 7-8: clearly on-topic and substantive.
- 5-6: relevant but ordinary.
- 1-4: passing mention.

Rules:
- Scan the entire transcript and return EVERY genuinely relevant segment, each with its score. Do not limit the count. Do not return passing mentions dressed up as strong evidence — score them low instead.
- The subject of each segment must be the primary item named in the prompt — not something served alongside it, contextually adjacent to it, or containing it as a minor ingredient. For example, if the prompt is about fish, exclude miso soup segments even at a sushi restaurant, even if the broth contains fish stock. Before selecting a segment ask: "Is the speaker directly evaluating the exact subject the prompt names?" If the answer is no, skip it.
- Each range must be a single, tight, self-contained statement — the "money quote" — not a whole on-topic stretch. Aim for roughly 5–12 seconds. When a speaker stays on-topic for a long span, do NOT return the entire span; return only the most compelling sentence or two within it. Prefer several short, punchy ranges over one long one.
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
- If the prompt asks for positive opinions, only return segments where the speaker's reaction is clearly positive or enthusiastic. Skip neutral mentions, passing references, and negative opinions even if the topic word appears.
- Only use timestamps that appear verbatim in the transcript
- The transcript may label speakers (e.g. "Interviewer:", "Agent:", "Participant:"). Only return ranges spoken by the respondent/participant. Never return a range where the interviewer, agent, or moderator is speaking, even if the topic word appears in their question.
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or extra punctuation — just the scored segments, one per line, or the word none"""

# Appended to _SYSTEM_PROMPT when the transcript is rich-tier (every respondent line
# carries a real [M:SS-M:SS] end timestamp). Tells Claude it can use end timestamps
# as range endpoints and should prefer tight single-sentence ranges over padded spans.
_RICH_PROMPT_CLAUSE = """

This transcript uses the rich format: each line carries both a start and an end timestamp: [M:SS-M:SS] Speaker: text.
The end timestamp is the speaker's real stop time — it is exact, not estimated.
Rules for rich transcripts:
- Both start and end timestamps on each line are verbatim and may be used in your returned ranges.
- Prefer to begin a range at a line's start timestamp and end it at that line's end timestamp.
- Do not pad the range past the last word that directly contributes to the topic.
- A single sentence is the ideal range. Return it as [line_start]-[line_end] for that sentence."""


def query_claude(transcript: str, prompt: str, tier: str = "plain") -> str:
    system = _SYSTEM_PROMPT if tier != "rich" else _SYSTEM_PROMPT + _RICH_PROMPT_CLAUSE
    message = _client.messages.create(
        model="claude-opus-4-8",
        max_tokens=256,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        # Stable prefix: cached across repeated analyzes of the
                        # same folder (additive analyze re-sends this verbatim).
                        "type": "text",
                        "text": f"Transcript:\n{transcript}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        # Varying suffix: must stay after the breakpoint.
                        "type": "text",
                        "text": f"\n\nPrompt: {prompt}",
                    },
                ],
            }
        ]
    )
    return message.content[0].text
```

- [ ] **Step 4: Run the new tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_claude_client.py -v
```

Expected: all tests PASS (including the 3 new ones and all existing ones)

- [ ] **Step 5: Wire tier detection into `app.py`**

Add `transcript_tier as _transcript_tier` to the `from shared import (` block:

```python
from shared import (
    read_transcript as _read_transcript,
    parse_transcript_lines as _parse_transcript_lines,
    filter_generated_reels as _filter_generated_reels,
    group_lines_into_segments as _group_lines_into_segments,
    lines_in_range as _lines_in_range,
    transcript_tier as _transcript_tier,
)
```

In `_analyze_one`, after `all_lines = _parse_transcript_lines(transcript)`, add the tier detection and pass it to `query_claude`. Find:

```python
        transcript = _read_transcript(txt_path)
        all_lines = _parse_transcript_lines(transcript)

        try:
            response = query_claude(transcript, prompt)
```

Replace with:

```python
        transcript = _read_transcript(txt_path)
        all_lines = _parse_transcript_lines(transcript)
        tier = _transcript_tier(all_lines)

        try:
            response = query_claude(transcript, prompt, tier=tier)
```

- [ ] **Step 6: Run the full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```
git add claude_client.py app.py tests/test_claude_client.py
git commit -m "feat(analysis): tier-aware Claude prompt for rich transcripts (A)"
```

---

### Task 5: Outcome measurement and cross-service determinism tests

**Files:**
- Test: `tests/test_shared.py` (append)

These tests are the falsifiability gate for the entire feature: if they fail, the premise of the change is wrong.

- [ ] **Step 1: Append the tests**

```python
# ---------------------------------------------------------------------------
# Outcome measurement and cross-service determinism
# ---------------------------------------------------------------------------


def test_rich_analysis_yields_shorter_clips_than_plain_for_same_content():
    """The core claim: rich-tier matching produces less total clip duration than
    plain-tier for the same content and the same Claude range.

    Uses lines_in_range + group_lines_into_segments directly — no Claude call.
    """
    from shared import lines_in_range, group_lines_into_segments, normalize_transcript

    # Rich: three sentence-level lines; only the middle one is relevant.
    rich_transcript = (
        "[0:00-0:13] Participant: I don't have strong feelings either way.\n"
        "[0:14-0:19] Participant: The service was absolutely exceptional.\n"
        "[0:20-0:30] Participant: Everything else was pretty standard."
    )
    # Plain: same content as one 30-second turn.
    plain_transcript = (
        "[0:00] Participant: I don't have strong feelings either way. "
        "The service was absolutely exceptional. "
        "Everything else was pretty standard."
    )

    # Simulate what Claude would return for "service quality": the money quote.
    claude_start, claude_end = 14.0, 19.0

    rich_lines = parse_transcript_lines(rich_transcript)
    plain_lines = parse_transcript_lines(normalize_transcript(plain_transcript))

    rich_matched = lines_in_range(rich_lines, claude_start, claude_end)
    plain_matched = lines_in_range(plain_lines, claude_start, claude_end)

    rich_segs = group_lines_into_segments(rich_lines, {l["raw"] for l in rich_matched})
    plain_segs = group_lines_into_segments(plain_lines, {l["raw"] for l in plain_matched})

    rich_dur = sum(e - s for s, e in rich_segs)
    plain_dur = sum(e - s for s, e in plain_segs)

    assert rich_dur < plain_dur, (
        f"Rich ({rich_dur:.1f}s) should produce shorter clips than plain ({plain_dur:.1f}s)"
    )
    # Rich clips the exact sentence: 0:19 - 0:14 = 5s.
    import pytest
    assert rich_dur == pytest.approx(5.0, abs=0.1)


def test_anchored_transcript_expand_anchors_is_idempotent_cross_service():
    """expand_anchors must be idempotent — the cross-service safety property.

    Both app.py and generator_app.py call read_transcript independently.
    If expand_anchors(expand_anchors(x)) != expand_anchors(x), the two
    services would produce different raw strings and selections would stop
    matching silently.
    """
    from shared import expand_anchors, parse_transcript_lines, transcript_tier

    anchored = (
        "[0:04-0:20] Participant: text about thing. "
        "[0:09] More detail here. [0:14] Final point."
    )

    first_pass = expand_anchors(anchored)
    second_pass = expand_anchors(first_pass)

    assert first_pass == second_pass, (
        "expand_anchors is not idempotent — cross-service raw identity will break"
    )

    # The expanded output must be classified rich (all lines have end timestamps).
    lines = parse_transcript_lines(first_pass)
    assert transcript_tier(lines) == "rich"

    # Raw strings must be stable across two independent calls (simulates two services).
    raws_a = [l["raw"] for l in lines]
    raws_b = [l["raw"] for l in parse_transcript_lines(expand_anchors(anchored))]
    assert raws_a == raws_b
```

- [ ] **Step 2: Run the tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "outcome or cross_service" -v
```

Expected: both tests PASS

- [ ] **Step 3: Run the full test suite one final time**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```
git add tests/test_shared.py
git commit -m "test: outcome measurement and cross-service determinism for rich transcript analysis"
```

# Speaker-Aware Clips + Empty-Clip Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exclude AI-interviewer turns from generated sizzle reels (with manual-selection override) and eliminate the empty title-card→title-card artifact via a minimum clip duration.

**Architecture:** The input transcript already labels speakers (`Interviewer:` / `Participant:`); today the parser discards the label. We capture it in `shared.py`, centralize interviewer-synonym detection there, filter interviewer lines out of `/analyze`, tighten the Claude prompt, and surface a de-emphasized-but-clickable cue in the frontend. Separately, `_group_lines_into_segments` gains a minimum-duration floor so degenerate ~0s segments are extended or dropped (never emitted as a lone title card).

**Tech Stack:** Python 3 / Flask, pytest, vanilla JS + CSS (no framework).

**Spec:** [docs/superpowers/specs/2026-07-07-interviewer-exclusion-and-empty-clip-fix-design.md](../specs/2026-07-07-interviewer-exclusion-and-empty-clip-fix-design.md)

**Test command (PowerShell):** `.\venv\Scripts\python.exe -m pytest tests/ -v`

---

## Task 1: Capture speaker + interviewer detection in `shared.py`

**Files:**
- Modify: `shared.py` (lines 7 `_LINE_RE`, 10-33 `parse_transcript_lines`)
- Test: `tests/test_shared.py`

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_shared.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -v -k "speaker or interviewer"`
Expected: FAIL — `KeyError: 'speaker'` / `ImportError: cannot import name 'is_interviewer_label'`.

- [ ] **Step 3: Add the constant and helper, and capture the speaker**

In `shared.py`, replace the `_LINE_RE` definition (line 7):

```python
_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+(\w[\w ]*?):\s*(.*)')
```

Immediately below `_LINE_RE`, add:

```python
# Speaker labels that identify the AI interview agent (case-insensitive,
# whitespace-normalized). Anything NOT in this set is treated as the
# respondent, so detection fails safe toward keeping content.
INTERVIEWER_LABELS = {
    "interviewer", "ai", "ai agent", "ai interviewer",
    "agent", "moderator", "bot", "assistant", "host",
}


def is_interviewer_label(speaker: str) -> bool:
    """True if the speaker label denotes the AI interviewer/agent."""
    normalized = " ".join(speaker.split()).lower()
    return normalized in INTERVIEWER_LABELS
```

Then update the parse loop body inside `parse_transcript_lines` (the `ts, text = ...` block). Replace:

```python
        ts, text = m.group(1), m.group(2)
        seconds = parse_timestamp_to_seconds(ts)
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "text": text,
            "seconds": seconds,
            "minute_bucket": int(seconds) // 60,
        })
```

with:

```python
        ts, speaker, text = m.group(1), m.group(2).strip(), m.group(3)
        seconds = parse_timestamp_to_seconds(ts)
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "speaker": speaker,
            "is_interviewer": is_interviewer_label(speaker),
            "text": text,
            "seconds": seconds,
            "minute_bucket": int(seconds) // 60,
        })
```

- [ ] **Step 4: Run the full shared test file to verify pass + no regressions**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -v`
Expected: PASS (all, including the pre-existing `test_parse_single_line` etc. — the added dict keys don't break them).

- [ ] **Step 5: Commit**

```powershell
git add shared.py tests/test_shared.py
git commit -m "feat: capture speaker label and interviewer flag in transcript parsing"
```

---

## Task 2: Exclude interviewer lines from `/analyze`

**Files:**
- Modify: `app.py` (inside `_run_analyze._analyze_one`, the `matched` loop at lines 241-249)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_app.py`:

```python
def test_run_analyze_excludes_interviewer_lines(tmp_path):
    from app import _run_analyze
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    txt = tmp_path / "v.txt"
    txt.write_text(
        "[0:10] Interviewer: Have you heard of Freshpet?\n"
        "[0:14] Participant: Yes I love Freshpet.\n",
        encoding="utf-8",
    )
    with patch("app.scan_videos", return_value=[video]), \
         patch("app._filter_generated_reels", side_effect=lambda paths: paths), \
         patch("app.query_claude", return_value="0:10-0:14"):
        result = _run_analyze(str(tmp_path), "Freshpet")

    matched = result["highlights"]["v.mp4"]
    assert "[0:14] Participant: Yes I love Freshpet." in matched
    assert all("Interviewer" not in line for line in matched)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_run_analyze_excludes_interviewer_lines -v`
Expected: FAIL — the interviewer line at 0:10 falls inside the 0:10-0:14 range and is currently included in `matched`.

- [ ] **Step 3: Skip interviewer lines when collecting matches**

In `app.py`, in the `matched` loop inside `_analyze_one`, replace:

```python
            for line in all_lines:
                if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                    if line["raw"] not in matched:
                        matched.append(line["raw"])
```

with:

```python
            for line in all_lines:
                if line.get("is_interviewer"):
                    continue  # analyze never auto-selects the interviewer
                if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                    if line["raw"] not in matched:
                        matched.append(line["raw"])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_run_analyze_excludes_interviewer_lines -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app.py tests/test_app.py
git commit -m "feat: exclude interviewer turns from /analyze results"
```

---

## Task 3: Tighten the Claude system prompt

**Files:**
- Modify: `claude_client.py` (`_SYSTEM_PROMPT`, lines 5-18)
- Test: `tests/test_claude_client.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_claude_client.py`:

```python
def test_system_prompt_instructs_respondent_only():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p")
        system = mock_client.messages.create.call_args.kwargs["system"]
    assert "interviewer" in system.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_claude_client.py::test_system_prompt_instructs_respondent_only -v`
Expected: FAIL — the current prompt never mentions "interviewer".

- [ ] **Step 3: Add the respondent-only rule**

In `claude_client.py`, inside `_SYSTEM_PROMPT`, add this bullet immediately after the
`- Only use timestamps that appear verbatim in the transcript` line:

```
- The transcript may label speakers (e.g. "Interviewer:", "Agent:", "Participant:"). Only return ranges spoken by the respondent/participant. Never return a range where the interviewer, agent, or moderator is speaking, even if the topic word appears in their question.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_claude_client.py -v`
Expected: PASS (all, including the pre-existing prompt tests).

- [ ] **Step 5: Commit**

```powershell
git add claude_client.py tests/test_claude_client.py
git commit -m "feat: instruct Claude to select respondent turns only"
```

---

## Task 4: Minimum clip duration in `_group_lines_into_segments`

**Files:**
- Modify: `generator_app.py` (add `MIN_CLIP_SECONDS` constant + logic in `_group_lines_into_segments`, lines 94-113)
- Test: `tests/test_generator_app.py`

- [ ] **Step 1: Write the failing tests**

Add after `test_group_lines_into_segments_falls_back_to_plus_ten_without_duration` in `tests/test_generator_app.py`:

```python
def test_group_lines_into_segments_extends_short_segment_to_minimum():
    from generator_app import _group_lines_into_segments, MIN_CLIP_SECONDS
    lines = [
        {"raw": "a", "seconds": 10.0},
        {"raw": "b", "seconds": 10.0},  # same-timestamp boundary -> ~0s segment
    ]
    result = _group_lines_into_segments(lines, {"a"})
    assert result == [(10.0, 10.0 + MIN_CLIP_SECONDS)]


def test_group_lines_into_segments_drops_segment_that_cannot_reach_minimum():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 29.5},
        {"raw": "b", "seconds": 30.0},
    ]
    # 'a' is the trailing selected run; video ends at 30.0 so the widest
    # possible clip is 0.5s < MIN_CLIP_SECONDS -> drop it entirely (no title card).
    result = _group_lines_into_segments(lines, {"a"}, video_duration=30.0)
    assert result == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -v -k "minimum or cannot_reach"`
Expected: FAIL — `ImportError: cannot import name 'MIN_CLIP_SECONDS'` and degenerate `(10.0, 10.0)` / `(29.5, 30.0)` segments returned.

- [ ] **Step 3: Add the constant and the floor logic**

In `generator_app.py`, immediately above the `def _group_lines_into_segments(` definition (line 94), add:

```python
# A clip shorter than this is imperceptible and produces a title-card->title-card
# artifact with no visible video between. Segments are extended to this floor, or
# dropped (title card included) when the source can't provide it.
MIN_CLIP_SECONDS = 1.5
```

Then replace the entire body of `_group_lines_into_segments` with:

```python
def _group_lines_into_segments(
    all_lines: list, selected_raws: set, video_duration: float | None = None
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Segments shorter than MIN_CLIP_SECONDS are extended to that floor (clamped to
    the video duration when known), or dropped entirely if the source can't reach
    it — so a lone title card with no clip is never emitted.
    """
    def _finalize(start: float, end: float):
        if end - start < MIN_CLIP_SECONDS:
            extended = start + MIN_CLIP_SECONDS
            if video_duration is not None:
                extended = min(extended, video_duration)
            end = extended
        if end - start < MIN_CLIP_SECONDS:
            return None  # can't reach the floor (hit video end) -> drop
        return (start, end)

    segments = []
    current = []

    for line in all_lines:
        if line["raw"] in selected_raws:
            current.append(line)
        else:
            if current:
                seg = _finalize(current[0]["seconds"], line["seconds"])
                if seg is not None:
                    segments.append(seg)
                current = []

    if current:
        end = video_duration if video_duration is not None else current[-1]["seconds"] + 10.0
        seg = _finalize(current[0]["seconds"], end)
        if seg is not None:
            segments.append(seg)

    return segments
```

- [ ] **Step 4: Run the segment tests to verify pass + no regressions**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -v -k "group_lines"`
Expected: PASS — the two new tests plus all six pre-existing `group_lines` tests (their segments are all ≥ 1.5s, so they are unchanged).

- [ ] **Step 5: Commit**

```powershell
git add generator_app.py tests/test_generator_app.py
git commit -m "fix: enforce minimum clip duration so no empty title-card clip is emitted"
```

---

## Task 5: Frontend cue for interviewer lines

**Files:**
- Modify: `static/app.js` (checkbox render block ~763-782, highlight render block ~856-876)
- Modify: `static/style.css` (append interviewer styles)

No JS unit-test harness exists in this repo; verify via the preview tools (browser) after the edits.

- [ ] **Step 1: Add the interviewer class + tag in the checkbox render block**

In `static/app.js`, in the checkbox `group.lines.forEach(line => { ... })` block, replace:

```javascript
      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;
```

with:

```javascript
      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;
      if (line.is_interviewer) {
        lineEl.classList.add('interviewer');
        const tag = document.createElement('span');
        tag.className = 'speaker-tag';
        tag.textContent = 'Interviewer';
        text.insertBefore(tag, text.firstChild);
      }
```

- [ ] **Step 2: Add the interviewer class + tag in the highlight render block**

In `static/app.js`, in the highlight `fileObj.lines.forEach(line => { ... })` block, replace:

```javascript
    const text = document.createElement('div');
    text.className = 'line-text-hl';
    text.textContent = line.text;
```

with:

```javascript
    const text = document.createElement('div');
    text.className = 'line-text-hl';
    text.textContent = line.text;
    if (line.is_interviewer) {
      lineEl.classList.add('interviewer');
      const tag = document.createElement('span');
      tag.className = 'speaker-tag';
      tag.textContent = 'Interviewer';
      text.insertBefore(tag, text.firstChild);
    }
```

- [ ] **Step 3: Add the styles**

Append to `static/style.css` (uses existing Bright Studio tokens; interviewer lines stay
clickable so the manual-selection override still works):

```css
/* Interviewer turns: de-emphasized but still selectable (manual override). */
.transcript-line-cb.interviewer .line-text-cb,
.transcript-line-hl.interviewer .line-text-hl {
  color: var(--muted);
  font-style: italic;
}

.speaker-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 1px 7px;
  border-radius: var(--radius-pill);
  background: var(--canvas);
  color: var(--muted);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  font-style: normal;
  vertical-align: middle;
}
```

- [ ] **Step 4: Verify in the browser**

Start the app and load a folder whose transcript has `Interviewer:` lines, then confirm:
- Interviewer lines show a muted italic style with an "Interviewer" pill.
- Running Analyze selects only Participant lines (no interviewer lines highlighted).
- An interviewer line can still be clicked/brushed to select it manually (override).

Use the preview tools: `preview_start`, then `preview_snapshot` / `preview_inspect` on a
`.transcript-line-cb.interviewer` element to confirm the class and `color: var(--muted)`
are applied, and `preview_screenshot` for the visual.

- [ ] **Step 5: Commit**

```powershell
git add static/app.js static/style.css
git commit -m "feat: de-emphasize interviewer turns in the transcript with a speaker tag"
```

---

## Final verification

- [ ] Run the whole suite: `.\venv\Scripts\python.exe -m pytest tests/ -v`
  Expected: all pass.
- [ ] Confirm the git log shows the five task commits.

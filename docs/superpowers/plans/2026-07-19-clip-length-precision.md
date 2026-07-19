# Clip-Length Precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated reel clips land at ~5–15 seconds instead of 30–60 seconds, by splitting turn-level transcript lines into sentence-level lines with interpolated timestamps, plus a hard 22-second clip cap.

**Architecture:** A new pure function `normalize_transcript()` in `shared.py` splits each `[M:SS] Speaker: text` turn containing multiple sentences into one line per sentence, interpolating each sentence's timestamp by word-count proportion across the turn's estimated speech window and biasing starts ~1s early. A thin `read_transcript()` helper is the single choke point through which all three transcript reads in the codebase go, so both Flask services derive byte-identical lines (critical — raw-line strings are the selection identity across analyze → select → generate). A `MAX_CLIP_SECONDS = 22.0` cap in the existing `group_lines_into_segments()` catches unpunctuated monologues.

**Tech Stack:** Python 3, Flask, pytest. No new dependencies. Pure string/arithmetic processing — no Whisper, no ffmpeg, works identically in local and cloud mode.

**Spec:** `docs/superpowers/specs/2026-07-19-clip-length-precision-design.md`

---

## Background for the implementer

You are working in a two-service Flask app that turns market-research
interview videos into "sizzle reels". A transcript is a sidecar `.txt` file
next to each video, in this format:

```
[00:00] Interviewer: Hello Participant is it a good time to speak?
[01:29] Participant: Um, so we do just a canned wet dog food. We'll do that. I also put supplements in every one.
```

These files are **client data exported from the Forven platform** — one line
per whole speaker turn, often 30–60 seconds long. **Never modify them on
disk.** All normalization happens on read.

The `raw` string of a transcript line (e.g. `"[1:29] Participant: Um, so we do just a canned wet dog food."`)
is the **identity key** used for selection throughout the app: the frontend
sends selected raw strings to the generator, and `localStorage` persists them.
This is why `normalize_transcript()` must be perfectly deterministic — the
main app (port 5000) and the generator service (port 5001) each read the same
`.txt` independently and must produce byte-identical lines.

**Run all commands from PowerShell** (ffmpeg is not on the bash PATH):

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `shared.py` | Modify | Add `normalize_transcript()`, `read_transcript()`, `MAX_CLIP_SECONDS`, `START_BIAS_SECONDS`, `_seconds_to_timestamp()`. Add cap to `group_lines_into_segments._finalize()`. |
| `app.py` | Modify (2 sites) | Use `read_transcript()` in `_run_analyze` (line ~239) and `/transcripts` (line ~805). |
| `generator_app.py` | Modify (1 site) | Use `read_transcript()` in `_build_segment_list` (line ~236). |
| `tests/test_shared.py` | Modify | Unit tests for `normalize_transcript`, `read_transcript`, and the clip cap. |
| `CLAUDE.md` | Modify | Document the new normalization choke point. |

`shared.py` is currently ~154 lines and cohesive (transcript parsing +
segment grouping + reel filtering). Adding normalization keeps related
transcript logic together; no split needed.

---

## Task 1: `normalize_transcript()` — sentence splitting with interpolated timestamps

**Files:**
- Modify: `shared.py`
- Test: `tests/test_shared.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k normalize -v
```

Expected: FAIL — `ImportError: cannot import name 'normalize_transcript' from 'shared'`

- [ ] **Step 3: Implement `normalize_transcript` in `shared.py`**

Add near the top of `shared.py`, after the existing `_LINE_RE` definition:

```python
# Sentence boundary: terminal punctuation followed by whitespace. Matches the
# convention transcriber._split_into_sentences uses for Whisper output.
_SENTENCE_SPLIT_RE = _re.compile(r'(?<=[.!?])\s+')

# Interpolated sentence starts are pulled this many seconds earlier. Forven
# turn windows include pauses, so a proportional estimate skews late — and late
# is the harmful direction (it clips the first word). Erring early costs at
# most a beat of lead-in, which the 0.4s clip fade-in softens.
START_BIAS_SECONDS = 1.0
```

Add these functions after `parse_transcript_lines` (they must sit below
`SPEAKING_RATE` / `TAIL_BUFFER`, so place them after those constants — see
Step 4's note if you hit a NameError):

```python
def _seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def normalize_transcript(raw_text: str) -> str:
    """Split multi-sentence turn lines into one line per sentence.

    Production transcripts are Forven platform exports: one line per whole
    speaker turn, often 30-60s. That is the granularity ceiling on clip
    length, because both Claude's returned ranges and manual selection can
    only address whole lines. This splits each turn into sentences and
    interpolates a timestamp for each by word-count proportion across the
    turn's *estimated speech window* (not the raw turn window, which includes
    trailing dead air and would skew every estimate late).

    Must stay deterministic: the main app and generator service each normalize
    the same .txt independently, and the resulting `raw` line strings are the
    selection identity shared between them.

    Lines that don't parse, single-sentence lines, and unpunctuated turns pass
    through byte-identical, so the function is idempotent.
    """
    lines = raw_text.splitlines()
    parsed: list[tuple[str, float, str, str] | None] = []
    for raw in lines:
        m = _LINE_RE.match(raw.strip())
        if m:
            ts, speaker, text = m.group(1), m.group(2).strip(), m.group(3)
            parsed.append((raw, parse_timestamp_to_seconds(ts), speaker, text))
        else:
            parsed.append(None)

    # Start of the next parseable line, used to bound each turn's window.
    next_starts: list[float | None] = [None] * len(parsed)
    upcoming: float | None = None
    for i in range(len(parsed) - 1, -1, -1):
        next_starts[i] = upcoming
        if parsed[i] is not None:
            upcoming = parsed[i][1]

    out: list[str] = []
    for idx, (entry, next_start) in enumerate(zip(parsed, next_starts)):
        if entry is None:
            out.append(lines[idx])
            continue
        raw, start, speaker, text = entry
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s]
        if len(sentences) < 2:
            out.append(raw)
            continue

        total_words = sum(len(s.split()) for s in sentences)
        if total_words == 0:
            out.append(raw)
            continue

        # Estimated end of speech in this turn, capped by the next line's
        # start. Interpolating over this (rather than to next_start) keeps
        # trailing silence from stretching every sentence estimate later.
        speech_end = start + total_words / SPEAKING_RATE + TAIL_BUFFER
        window_end = speech_end if next_start is None else min(next_start, speech_end)
        span = max(0.0, window_end - start)

        words_before = 0
        for sentence in sentences:
            offset = (words_before / total_words) * span
            ts_sec = max(start, start + offset - START_BIAS_SECONDS)
            out.append(f"[{_seconds_to_timestamp(ts_sec)}] {speaker}: {sentence}")
            words_before += len(sentence.split())

    return "\n".join(out)
```

The function body is exactly four parts: parse every line → compute
`next_starts` by walking backwards → the single `for idx, ...` loop →
`return "\n".join(out)`.

**Two expected behaviours — do not "fix" them:**

1. **Timestamp format.** Split lines are emitted as `[1:29]` (no zero-padded
   minutes) while a passed-through Forven line keeps its original `[01:29]`.
   Both parse identically via `_LINE_RE` (`\d+:\d{2}`), and `transcriber.py`
   already emits the unpadded form, so the codebase handles both. Idempotency
   is unaffected — a split line re-parses as single-sentence and passes
   through byte-identical.
2. **Staccato speech collapses to the same second.** `"One. Two. Three."` at
   `[1:00]` all round to `[1:00]`. Harmless: they stay contiguous so they
   group into one segment, and the `MIN_CLIP_SECONDS` floor gives that
   segment a watchable length. If two *identical* sentences in one turn also
   land on the same second (e.g. `"Yes. Yes."`), the two raw strings collide;
   they select and group together, which is the desired outcome anyway.

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k normalize -v
```

Expected: PASS (14 tests).

If you get `NameError: name 'SPEAKING_RATE' is not defined`, the functions were
placed above the constants — move `normalize_transcript` below the
`SPEAKING_RATE` / `TAIL_BUFFER` definitions.

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: all tests pass (296 existing + 14 new).

- [ ] **Step 6: Commit**

```powershell
git add shared.py tests/test_shared.py
git commit -m "feat(transcripts): sentence-level normalization with interpolated timestamps

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: `MAX_CLIP_SECONDS` hard cap

**Files:**
- Modify: `shared.py` (`group_lines_into_segments._finalize`)
- Test: `tests/test_shared.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
from shared import MAX_CLIP_SECONDS, group_lines_into_segments


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
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k cap -v
```

Expected: FAIL — `ImportError: cannot import name 'MAX_CLIP_SECONDS'`

- [ ] **Step 3: Implement the cap**

In `shared.py`, add below the `TAIL_BUFFER` constant:

```python
# Hard ceiling on any single clip. Sentence normalization gets most clips to
# 5-15s; this is the safety net for turns with no terminal punctuation (which
# pass through unsplit) and for over-wide ranges returned by analyze. Set above
# the target range so it only fires on pathological cases -- normal clips still
# end on a natural sentence boundary.
MAX_CLIP_SECONDS = 22.0
```

In `group_lines_into_segments._finalize`, add the cap immediately after the
existing dead-air trim and before the `MIN_CLIP_SECONDS` floor:

```python
    def _finalize(start: float, end: float, last_line: dict):
        # Cap trailing dead air before applying the floor. `end` is the next
        # line's start; clip instead at the last selected line's estimated
        # speech end. min() means this can only ever shorten a clip. Falls back
        # to `end` when there's no text to estimate from (fail toward keeping
        # content).
        words = len(last_line.get("text", "").split())
        if words:
            speech_end = last_line["seconds"] + words / SPEAKING_RATE + TAIL_BUFFER
            end = min(end, speech_end)
        end = min(end, start + MAX_CLIP_SECONDS)
        if end - start < MIN_CLIP_SECONDS:
            extended = start + MIN_CLIP_SECONDS
            if video_duration is not None:
                extended = min(extended, video_duration)
            end = extended
        if end - start < MIN_CLIP_SECONDS:
            return None  # can't reach the floor (hit video end) -> drop
        return (start, end)
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the full suite**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: all pass. If an existing generator or app test asserted a clip
longer than 22s, update that test's expectation to the capped value — the cap
is intended behaviour. Do not raise `MAX_CLIP_SECONDS` to make a test pass.

- [ ] **Step 6: Commit**

```powershell
git add shared.py tests/test_shared.py
git commit -m "feat(reels): hard 22s cap on single clip length

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: `read_transcript()` choke point and wiring the three call sites

**Files:**
- Modify: `shared.py`
- Modify: `app.py` (2 sites)
- Modify: `generator_app.py` (1 site)
- Test: `tests/test_shared.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shared.py`:

```python
from shared import read_transcript


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
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k read_transcript -v
```

Expected: FAIL — `ImportError: cannot import name 'read_transcript'`

- [ ] **Step 3: Implement `read_transcript` in `shared.py`**

Add directly below `normalize_transcript`:

```python
def read_transcript(txt_path) -> str:
    """Read a transcript sidecar and return it sentence-normalized.

    Every transcript read in both services goes through here so no code path
    can accidentally work with un-normalized turn-level lines -- the `raw`
    strings are the selection identity shared across services, so they must
    match everywhere. (Same invariant as filter_generated_reels: if you add a
    new code path that reads a .txt, call this.) The file on disk is never
    modified; it is client data.
    """
    return normalize_transcript(_Path(txt_path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k read_transcript -v
```

Expected: PASS.

- [ ] **Step 5: Wire `app.py` — `_run_analyze` (around line 235-240)**

Change the import block at `app.py:38`:

```python
from shared import (
    parse_transcript_lines as _parse_transcript_lines,
```

to include the new helper (keep every existing name in this import — check the
current file and preserve them all):

```python
from shared import (
    read_transcript as _read_transcript,
    parse_transcript_lines as _parse_transcript_lines,
```

Then replace the read in `_run_analyze`:

```python
        transcript = txt_path.read_text(encoding="utf-8")
        all_lines = _parse_transcript_lines(transcript)
```

with:

```python
        transcript = _read_transcript(txt_path)
        all_lines = _parse_transcript_lines(transcript)
```

This one change fixes both halves of analyze: the text sent to Claude now
contains sentence-level timestamps (making the prompt's existing "roughly 5–12
seconds" instruction achievable), and the lines matched against Claude's
returned ranges are the same sentence-level lines.

- [ ] **Step 6: Wire `app.py` — `/transcripts` (around line 801-805)**

Replace:

```python
            lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
```

with:

```python
            lines = _parse_transcript_lines(_read_transcript(txt_path))
```

- [ ] **Step 7: Wire `generator_app.py` — `_build_segment_list` (around line 236)**

Change the import at `generator_app.py:40`:

```python
from shared import parse_transcript_lines as _parse_transcript_lines, filter_generated_reels as _filter_generated_reels
```

to:

```python
from shared import parse_transcript_lines as _parse_transcript_lines, filter_generated_reels as _filter_generated_reels, read_transcript as _read_transcript
```

Then replace:

```python
        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
```

with:

```python
        all_lines = _parse_transcript_lines(_read_transcript(txt_path))
```

This covers both the `/generate` and `/plan` paths (both call
`_build_segment_list`), so captions inherit sentence granularity too.

- [ ] **Step 8: Verify no un-normalized transcript reads remain**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q
Select-String -Path app.py,generator_app.py -Pattern "_parse_transcript_lines\("
```

Expected: full suite passes. Every `_parse_transcript_lines(` hit in both
files must take `_read_transcript(...)` as its argument — no direct
`.read_text(` inside a `_parse_transcript_lines(` call.

If a test fails because it asserts on turn-level line text, update the
expectation to the sentence-level line — that is the intended change.

- [ ] **Step 9: Commit**

```powershell
git add shared.py app.py generator_app.py tests/test_shared.py
git commit -m "feat(transcripts): normalize on read at all three transcript entry points

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: Cross-service consistency test

**Files:**
- Test: `tests/test_shared.py`

The single most dangerous failure mode of this change is the two services
disagreeing about line text — selections made in the main app would then match
nothing in the generator, silently producing an empty reel. Lock it down.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shared.py`:

```python
def test_selection_identity_survives_analyze_to_generate(tmp_path):
    """The raw line strings the main app selects must match what the generator
    reads. Both services normalize the same .txt independently."""
    txt = tmp_path / "interview.txt"
    txt.write_text(
        "[1:00] Participant: First sentence here. Second sentence here. Third one here.\n"
        "[2:00] Interviewer: Thanks.",
        encoding="utf-8",
    )
    app_side = parse_transcript_lines(read_transcript(txt))
    generator_side = parse_transcript_lines(read_transcript(txt))

    selected = {app_side[1]["raw"]}
    matched = [line for line in generator_side if line["raw"] in selected]
    assert len(matched) == 1
    assert matched[0]["text"] == "Second sentence here."

    segments = group_lines_into_segments(generator_side, selected, video_duration=300.0)
    assert len(segments) == 1
    start, end = segments[0]
    assert end > start
    assert end - start <= MAX_CLIP_SECONDS
```

- [ ] **Step 2: Run the test**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k selection_identity -v
```

Expected: PASS immediately (Tasks 1–3 already provide the behaviour). This is
a regression guard, not new functionality. If it fails, `normalize_transcript`
is not deterministic — fix that before continuing.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_shared.py
git commit -m "test: guard cross-service selection identity after normalization

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: Real-data verification

**Files:** none modified — this is a measurement step.

- [ ] **Step 1: Measure clip lengths on the real Forven transcripts**

```powershell
.\venv\Scripts\python.exe -c @'
from pathlib import Path
from shared import read_transcript, parse_transcript_lines, group_lines_into_segments

folder = Path(r"C:\Users\giles\Downloads\FORVEN VIDEOS")
for txt in sorted(folder.glob("forven-interview-*.txt")):
    before = parse_transcript_lines(txt.read_text(encoding="utf-8"))
    after = parse_transcript_lines(read_transcript(txt))
    resp = [l for l in after if not l["is_interviewer"]]
    segs = group_lines_into_segments(after, {l["raw"] for l in resp}, video_duration=600.0)
    durs = [round(e - s, 1) for s, e in segs]
    print(f"{txt.name[:28]}: lines {len(before)} -> {len(after)} | clip secs {durs}")
'@
```

Expected: line counts roughly triple or quadruple, and no clip duration
exceeds 22.0. Record the output in the commit message or report it back.

- [ ] **Step 2: Sanity-check a normalized transcript by eye**

```powershell
.\venv\Scripts\python.exe -c @'
from pathlib import Path
from shared import read_transcript
p = Path(r"C:\Users\giles\Downloads\FORVEN VIDEOS\forven-interview-9dd1344c-01b0-4dae-b0ac-bfe4d78ab0c3.txt")
print(read_transcript(p))
'@
```

Verify by eye: timestamps increase monotonically, never exceed the following
original turn's timestamp, speaker labels are intact on every line, and
sentences are not split mid-word.

- [ ] **Step 3: Run the full suite one final time**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass.

---

## Task 6: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the normalization choke point**

In the `### Shared lower-level modules` section, replace the `**shared.py**`
bullet with:

```markdown
- **`shared.py`** — `parse_transcript_lines(raw_text)`: parses `[M:SS] Speaker: text` lines into dicts with `raw`, `timestamp`, `text`, `seconds`, `minute_bucket`. `normalize_transcript(raw_text)`: splits multi-sentence turn lines into one line per sentence, interpolating each sentence's timestamp by word-count proportion across the turn's estimated speech window (`words / SPEAKING_RATE + TAIL_BUFFER`, capped by the next line's start) and biasing starts `START_BIAS_SECONDS` (1.0s) early so residual error clips lead-in rather than the first word. Production transcripts are Forven exports with one line per whole 30-60s turn, which was the granularity ceiling that kept clips long. Pure, deterministic, and idempotent — both services normalize the same `.txt` independently and the resulting `raw` strings are the cross-service selection identity, so it must stay deterministic. `read_transcript(txt_path)` is the read-and-normalize helper. Used by both `app.py` and `generator_app.py`.
```

- [ ] **Step 2: Add the invariant to Key Behaviours**

In the `## Key Behaviours` section, add after the `_filter_generated_reels`
bullet:

```markdown
- **Every transcript read goes through `shared.read_transcript`** (`app._run_analyze`, `app./transcripts`, `generator_app._build_segment_list`). It normalizes turn-level lines into sentence-level lines on read; the `.txt` on disk is client data and is never rewritten. If you add a code path that reads a `.txt`, use `read_transcript` — a path that reads raw text directly will produce line strings that don't match the other service's, silently breaking selection matching.
- **Clip length is bounded at both ends** by `shared.group_lines_into_segments`: `MIN_CLIP_SECONDS` (1.5s) floor and `MAX_CLIP_SECONDS` (22s) ceiling. The ceiling is a safety net for unpunctuated turns (which `normalize_transcript` passes through unsplit) — normal clips end on a natural sentence boundary well under it.
```

- [ ] **Step 3: Commit**

```powershell
git add CLAUDE.md
git commit -m "docs: document transcript normalization choke point and clip bounds

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Out of scope

- Whisper word-level alignment (the precision upgrade path if ±2s proves
  visible — it would slot in behind `normalize_transcript`'s interface).
- Changes to the Claude system prompt in `claude_client.py` — the existing
  "roughly 5–12 seconds" instruction becomes achievable once the transcript
  offers sentence-level timestamps.
- Rewriting or mutating client `.txt` files.
- Frontend changes. The UI is already line-based and inherits sentence
  granularity automatically. Note that saved `localStorage` selections
  (`sizzle_sel_<folder>`) reference old turn-level strings and will silently
  reset once — this is expected and harmless.

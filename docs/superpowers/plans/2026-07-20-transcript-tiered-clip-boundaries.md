# Tiered Clip Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive clip ends and caption cue times from real transcript timestamps when present, and remove word-count estimation entirely.

**Architecture:** The transcript line format gains an optional end time (`[M:SS-M:SS]`). A pure `transcript_tier()` helper classifies each file as `rich` (every respondent line has a valid end) or `plain`. Rich files bypass sentence normalization and use real ends for clips and captions; plain files fall back to the next line's start with no estimation. All timing constants used for *prediction* are deleted; only safety nets (`MIN_CLIP_SECONDS`, `MAX_CLIP_SECONDS`) and plain-tier *granularity* constants (`SPEAKING_RATE`, `START_BIAS_SECONDS`, used solely inside `normalize_transcript`) survive.

**Tech Stack:** Python 3 / Flask / pytest, vanilla JS (no framework), Node for a new minimal JS test runner.

**Spec:** `docs/superpowers/specs/2026-07-20-transcript-tiered-clip-boundaries-design.md` (commit `662fdd3`)

---

## Precision note (read before Task 7)

`shared._seconds_to_timestamp` and `transcriber._seconds_to_timestamp` both truncate via `int()`. For a **start** that is safe — it lands earlier, giving lead-in. For an **end** it is not: a sentence whose speech ends at 12.9s written as `[0:12]` clips 0.9s of audio, reintroducing the exact defect this work removes.

**Rule: ends round UP, starts round DOWN.** Task 7 adds `_seconds_to_timestamp_ceil` for this.

This applies to Forven's exporter too. Whole-second ends lose up to 1s of speech. When specifying the format to Forven, ask for ends rounded up, or sub-second precision. This is outside this plan's code but must be communicated.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `shared.py` | Line parsing, tier detection, normalization, segment grouping | Modify |
| `captions.py` | WebVTT cue timing | Modify |
| `transcriber.py` | Whisper → transcript text | Modify |
| `static/app.js` | Selection persistence key | Modify |
| `generator_app.py` | Plan payload field name in skip log | Modify (1 line, via `static/reel-encoder.js`) |
| `static/reel-encoder.js` | Skip log message | Modify |
| `tests/fixtures/rich_tier.txt` | Rich transcript fixture | Create |
| `tests/fixtures/plain_tier.txt` | Plain transcript fixture | Create |
| `tests/js/run.mjs` | Minimal JS test runner | Create |
| `tests/js/selection_key.test.mjs` | Guards the v3 bump | Create |

---

## Task 1: Minimal JS test runner

No JS test infrastructure exists. Task 8 changes a `localStorage` key whose failure mode is silent (selections vanish, reels come out empty). That must not ship untested.

**Files:**
- Create: `tests/js/run.mjs`
- Create: `tests/js/sanity.test.mjs`

- [ ] **Step 1: Write the runner**

Create `tests/js/run.mjs`:

```javascript
// Minimal test runner: finds *.test.mjs beside this file, runs each exported
// test, reports pass/fail. No framework, no dependencies — node only.
import { readdirSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const dir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
const files = readdirSync(dir).filter(f => f.endsWith('.test.mjs'));

let passed = 0;
const failures = [];

for (const file of files) {
  const mod = await import(pathToFileURL(path.join(dir, file)).href);
  for (const [name, fn] of Object.entries(mod)) {
    if (typeof fn !== 'function') continue;
    try {
      await fn();
      passed++;
      console.log(`  ok   ${file} :: ${name}`);
    } catch (err) {
      failures.push({ file, name, err });
      console.log(`  FAIL ${file} :: ${name}`);
    }
  }
}

console.log(`\n${passed} passed, ${failures.length} failed`);
for (const f of failures) {
  console.log(`\n--- ${f.file} :: ${f.name} ---\n${f.err && f.err.stack || f.err}`);
}
process.exit(failures.length ? 1 : 0);
```

- [ ] **Step 2: Write a sanity test that fails**

Create `tests/js/sanity.test.mjs`:

```javascript
import assert from 'node:assert';

export function test_runner_reports_failures() {
  assert.strictEqual(1, 2, 'deliberate failure to prove the runner reports it');
}
```

- [ ] **Step 3: Run it and confirm the failure is reported**

Run: `node tests/js/run.mjs`
Expected: `FAIL sanity.test.mjs :: test_runner_reports_failures`, `0 passed, 1 failed`, exit code 1.

- [ ] **Step 4: Flip the sanity test to passing**

Replace the body of `tests/js/sanity.test.mjs`:

```javascript
import assert from 'node:assert';

export function test_runner_reports_passes() {
  assert.strictEqual(1, 1);
}
```

- [ ] **Step 5: Run it and confirm it passes**

Run: `node tests/js/run.mjs`
Expected: `1 passed, 0 failed`, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add tests/js/run.mjs tests/js/sanity.test.mjs
git commit -m "test: minimal node test runner for the JS layer

No JS test infrastructure existed, so localStorage migrations and the
priority-selection maths have shipped unguarded. This is a dependency-free
runner: discover *.test.mjs, run exported functions, report failures."
```

---

## Task 2: Parse an optional end timestamp

**Files:**
- Modify: `shared.py:7` (`_LINE_RE`), `shared.py:37-62` (`parse_transcript_lines`), `shared.py:146-149` (group indices in `normalize_transcript`)
- Test: `tests/test_shared.py`

**Critical:** adding a capture group shifts every group index. `_LINE_RE` is used in **two** places — `parse_transcript_lines` and `normalize_transcript`. Both must be updated or normalization silently reads the wrong fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "end or padded or malformed or after_regex" -v`
Expected: FAIL with `KeyError: 'end_seconds'`.

- [ ] **Step 3: Update the regex**

In `shared.py`, replace line 7:

```python
# Optional end timestamp: [M:SS] (plain) or [M:SS-M:SS] (rich). Groups are
# 1=start, 2=end-or-None, 3=speaker, 4=text — NOTE the shift, this regex is
# also used by normalize_transcript.
_LINE_RE = _re.compile(r'^\[(\d+:\d{2})(?:-(\d+:\d{2}))?\]\s+(\w[\w ]*?):\s*(.*)')
```

- [ ] **Step 4: Update `parse_transcript_lines`**

In `shared.py`, replace the body of the loop in `parse_transcript_lines` (currently lines 48-61):

```python
        m = _LINE_RE.match(raw)
        if not m:
            continue
        ts, end_ts = m.group(1), m.group(2)
        speaker, text = m.group(3).strip(), m.group(4)
        seconds = parse_timestamp_to_seconds(ts)
        # An end that is not strictly after the start cannot be real; treat it
        # as absent, which demotes the whole file to the plain tier.
        end_seconds = None
        if end_ts:
            candidate = parse_timestamp_to_seconds(end_ts)
            if candidate > seconds:
                end_seconds = candidate
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "speaker": speaker,
            "is_interviewer": is_interviewer_label(speaker),
            "text": text,
            "seconds": seconds,
            "end_seconds": end_seconds,
            "minute_bucket": int(seconds) // 60,
        })
```

Also update the docstring's key list to mention `end_seconds`.

- [ ] **Step 5: Update `normalize_transcript`'s group indices**

In `shared.py`, in `normalize_transcript`, replace (currently lines 146-149):

```python
        m = _LINE_RE.match(raw.strip())
        if m:
            # Groups 3/4 (not 2/3): _LINE_RE gained an optional end group.
            ts, speaker, text = m.group(1), m.group(3).strip(), m.group(4)
            parsed.append((parse_timestamp_to_seconds(ts), speaker, text))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -v`
Expected: PASS, including all pre-existing tests.

- [ ] **Step 7: Commit**

```bash
git add shared.py tests/test_shared.py
git commit -m "feat(transcripts): parse an optional end timestamp

[M:SS-M:SS] is a superset of [M:SS]; existing files parse unchanged. An end
that is not strictly after its start is treated as absent, which demotes the
file to the plain tier rather than emitting a zero-length clip."
```

---

## Task 3: Tier detection

**Files:**
- Modify: `shared.py` (add `transcript_tier` after `parse_transcript_lines`)
- Test: `tests/test_shared.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
def test_tier_rich_when_every_respondent_line_has_an_end():
    from shared import transcript_tier
    lines = parse_transcript_lines(
        "[0:00-0:04] Participant: One.\n"
        "[0:05-0:09] Participant: Two."
    )
    assert transcript_tier(lines) == "rich"


def test_tier_ignores_interviewer_lines():
    # Clip ends come from the last selected respondent line, and captions
    # exclude the interviewer, so interviewer ends are not required.
    from shared import transcript_tier
    lines = parse_transcript_lines(
        "[0:00] Interviewer: A question?\n"
        "[0:05-0:09] Participant: An answer."
    )
    assert transcript_tier(lines) == "rich"


def test_tier_plain_when_any_respondent_line_lacks_an_end():
    from shared import transcript_tier
    lines = parse_transcript_lines(
        "[0:00-0:04] Participant: One.\n"
        "[0:05] Participant: Two."
    )
    assert transcript_tier(lines) == "plain"


def test_tier_plain_when_an_end_is_malformed():
    from shared import transcript_tier
    lines = parse_transcript_lines(
        "[0:00-0:04] Participant: One.\n"
        "[0:05-0:05] Participant: Two."
    )
    assert transcript_tier(lines) == "plain"


def test_tier_plain_for_empty_or_interviewer_only():
    from shared import transcript_tier
    assert transcript_tier([]) == "plain"
    assert transcript_tier(parse_transcript_lines("[0:00] Interviewer: Hi?")) == "plain"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k tier -v`
Expected: FAIL with `ImportError: cannot import name 'transcript_tier'`.

- [ ] **Step 3: Implement**

In `shared.py`, add after `parse_transcript_lines`:

```python
def transcript_tier(lines: list[dict]) -> str:
    """Classify parsed lines as "rich" (real end times) or "plain".

    Rich only if EVERY respondent line carries a valid end_seconds. Interviewer
    lines are exempt: clip ends come from the last selected respondent line and
    captions exclude the interviewer.

    Strict all-or-nothing is deliberate. A per-line fallback would mix exact and
    estimated boundaries inside a single reel, producing inconsistent output
    with an invisible cause.

    Must stay pure and deterministic: app.py and generator_app.py classify the
    same .txt independently and must always agree.
    """
    respondent = [l for l in lines if not l.get("is_interviewer")]
    if not respondent:
        return "plain"
    if all(l.get("end_seconds") is not None for l in respondent):
        return "rich"
    return "plain"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k tier -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Extend the cross-service determinism guard**

`0fdc2b5` added `test_both_services_read_transcripts_identically` and
`test_selection_identity_survives_analyze_to_generate` to `tests/test_shared.py`.
Both services now also classify tier independently, so they must agree on that
too — a disagreement would mean one service normalizes and the other doesn't,
producing `raw` strings that never match.

Append to `tests/test_shared.py`:

```python
def test_both_services_agree_on_tier(tmp_path):
    """app.py and generator_app.py classify the same file independently.

    If they disagreed, one would normalize and the other would not, and their
    `raw` strings — the selection identity — would never match.
    """
    import app as app_module
    import generator_app as gen_module

    txt = tmp_path / "interview.txt"
    txt.write_text(
        "[00:00-00:04] Participant: One sentence here.\n"
        "[00:05-00:09] Participant: Another sentence here.",
        encoding="utf-8",
    )
    assert app_module._read_transcript(txt) == gen_module._read_transcript(txt)


def test_tier_is_stable_across_repeated_reads(tmp_path):
    """Tier detection must be pure: same bytes, same answer, every time."""
    from shared import transcript_tier
    txt = tmp_path / "interview.txt"
    txt.write_text("[00:00-00:04] Participant: One.", encoding="utf-8")
    first = transcript_tier(parse_transcript_lines(txt.read_text(encoding="utf-8")))
    for _ in range(3):
        again = transcript_tier(parse_transcript_lines(txt.read_text(encoding="utf-8")))
        assert again == first == "rich"
```

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "agree_on_tier or stable_across" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add shared.py tests/test_shared.py
git commit -m "feat(transcripts): strict all-or-nothing tier detection

A file is rich only if every respondent line has a valid end. Mixing exact and
estimated boundaries within one reel would be inconsistent in a way no log
would reveal."
```

---

## Task 4: Route `read_transcript` by tier

**Files:**
- Modify: `shared.py:191-201` (`read_transcript`)
- Create: `tests/fixtures/rich_tier.txt`, `tests/fixtures/plain_tier.txt`
- Test: `tests/test_shared.py`

- [ ] **Step 1: Create the fixtures**

Create `tests/fixtures/rich_tier.txt`:

```
[00:00-00:03] Interviewer: To start, tell me about your dog.
[00:04-00:11] Participant: He's a Corgi mix. We got him as a rescue.
[00:12-00:19] Participant: So he's anywhere from ten to thirteen years old.
[00:20-00:24] Interviewer: How would you describe his role at home?
[00:25-00:31] Participant: He's really lazy. He doesn't do much at all.
```

Create `tests/fixtures/plain_tier.txt`:

```
[00:00] Interviewer: To start, tell me about your dog.
[00:04] Participant: He's a Corgi mix. We got him as a rescue.
[00:12] Participant: So he's anywhere from ten to thirteen years old.
[00:20] Interviewer: How would you describe his role at home?
[00:25] Participant: He's really lazy. He doesn't do much at all.
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_shared.py`:

```python
FIXTURES = Path(__file__).parent / "fixtures"


def test_read_transcript_returns_rich_file_unchanged():
    # Rich files must bypass normalization entirely — asserting byte-identity
    # (not just idempotence) proves the bypass rather than a coincidence.
    path = FIXTURES / "rich_tier.txt"
    assert read_transcript(path) == path.read_text(encoding="utf-8")


def test_read_transcript_still_normalizes_plain_file():
    out = read_transcript(FIXTURES / "plain_tier.txt")
    # "He's a Corgi mix. We got him as a rescue." is two sentences and must be
    # split into two lines by normalization.
    assert "Corgi mix." in out
    assert out.count("Participant:") > 3
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k read_transcript -v`
Expected: `test_read_transcript_returns_rich_file_unchanged` FAILS (normalization rewrites `[00:04-00:11]` to `[0:04]` and strips the trailing newline).

- [ ] **Step 4: Implement**

In `shared.py`, replace `read_transcript`'s body (line 201):

```python
    text = _Path(txt_path).read_text(encoding="utf-8")
    if transcript_tier(parse_transcript_lines(text)) == "rich":
        # Already sentence-level with real times. Normalizing would overwrite
        # true timestamps with interpolated ones.
        return text
    return normalize_transcript(text)
```

Update the docstring to state the tier routing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add shared.py tests/fixtures/rich_tier.txt tests/fixtures/plain_tier.txt tests/test_shared.py
git commit -m "feat(transcripts): bypass normalization for rich transcripts

Rich files are already sentence-level with real times; interpolating over them
would replace ground truth with estimates."
```

---

## Task 5: Clip boundaries — use real ends, delete the estimate

**Files:**
- Modify: `shared.py:74-82` (delete `CLIP_TAIL_RATE`, `TAIL_BUFFER` usage note), `shared.py:204-242` (`group_lines_into_segments`)
- Modify: `tests/test_generator_app.py` (remove two obsolete tests, update two duration expectations)
- Modify: `tests/test_shared.py` (remove one obsolete test)
- Modify: `tests/test_app.py` (update two expectations)

**This is the load-bearing task.** Test in Step 1 fails if any estimate ever creeps back in.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shared.py`:

```python
def test_rich_clip_end_is_exactly_the_transcript_end():
    lines = parse_transcript_lines(
        "[0:00-0:08] Participant: A full answer here.\n"
        "[0:30] Interviewer: Next question?"
    )
    sel = {lines[0]["raw"]}
    assert group_lines_into_segments(lines, sel) == [(0.0, 8.0)]


def test_rich_clip_end_ignores_word_count():
    # Same timestamps, wildly different word counts -> identical boundary.
    short = parse_transcript_lines(
        "[0:00-0:08] Participant: Short.\n[0:30] Interviewer: Q?")
    long = parse_transcript_lines(
        "[0:00-0:08] Participant: " + " ".join(["word"] * 80) +
        ".\n[0:30] Interviewer: Q?")
    a = group_lines_into_segments(short, {short[0]["raw"]})
    b = group_lines_into_segments(long, {long[0]["raw"]})
    assert a == b == [(0.0, 8.0)]


def test_plain_clip_end_is_the_next_line_start():
    lines = parse_transcript_lines(
        "[0:00] Participant: A full answer here.\n"
        "[0:30] Interviewer: Next question?"
    )
    assert group_lines_into_segments(lines, {lines[0]["raw"]}) == [(0.0, 30.0)]


def test_plain_clip_end_ignores_word_count():
    # The defining property of the rework: word count no longer moves any
    # boundary. Varying ONLY the word count must not change the end.
    def end_for(text):
        lines = parse_transcript_lines(
            f"[0:00] Participant: {text}\n[0:30] Interviewer: Q?")
        return group_lines_into_segments(lines, {lines[0]["raw"]})[0][1]

    assert end_for("Short.") == end_for(" ".join(["word"] * 80) + ".") == 30.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "rich_clip or plain_clip" -v`
Expected: FAIL — the current estimate shortens every one of these ends.

- [ ] **Step 3: Delete the tail-estimate constant**

In `shared.py`, delete the entire `CLIP_TAIL_RATE` block (the comment and assignment added for the clip tail). Leave `SPEAKING_RATE` and `TAIL_BUFFER` in place — `normalize_transcript` still uses both at line 177 — but replace their comment with:

```python
# Used ONLY by normalize_transcript, to size the interpolation window when
# splitting a turn into sentences (plain tier only). These no longer influence
# any clip end: that is real transcript data in the rich tier and the next
# line's start in the plain tier.
SPEAKING_RATE = 2.0    # words/sec
TAIL_BUFFER = 1.0      # seconds
```

- [ ] **Step 4: Rewrite `_finalize`**

In `shared.py`, replace `group_lines_into_segments` down to the end of `_finalize`:

```python
def group_lines_into_segments(
    all_lines: list, selected_raws: set, video_duration: float | None = None
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Rich transcripts end each clip at the last selected line's real end time.
    Plain transcripts end at the start of the first line after the run — the
    only end-adjacent timestamp such a file contains. No word-count estimation
    in either path.

    MAX_CLIP_SECONDS still applies in BOTH tiers and can truncate a genuinely
    long run mid-sentence. That is a reel-pacing decision, not a timing guess:
    a single 40s+ clip is too long for a highlight reel. It is the only
    remaining path by which a clip can end mid-sentence.

    Pure logic shared by the generator (real clip ranges) and the main app's
    analyze (create-screen length estimate), so both compute identical durations.
    """
    rich = transcript_tier(all_lines) == "rich"

    def _finalize(start: float, end: float, last_line: dict):
        # Rich: the transcript states when this speaker stopped. Trust it.
        # Plain: `end` is already the next line's start, the only real signal.
        if rich and last_line.get("end_seconds") is not None:
            end = last_line["end_seconds"]
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

The rest of the function (the loop building `segments`) is unchanged **except** the trailing-run fallback below.

- [ ] **Step 5: Remove the `+10.0` invention**

In `shared.py`, in the trailing-run branch of `group_lines_into_segments`, replace:

```python
    if current:
        end = video_duration if video_duration is not None else current[-1]["seconds"] + 10.0
        seg = _finalize(current[0]["seconds"], end, current[-1])
```

with:

```python
    if current:
        # No next line. Rich tier gets a real end from _finalize. Plain tier
        # uses the video duration when known; when it is not (cloud planning),
        # MAX_CLIP_SECONDS bounds it here and the browser encoder clamps the
        # range to the real media length via computeDuration().
        end = video_duration if video_duration is not None else float("inf")
        seg = _finalize(current[0]["seconds"], end, current[-1])
```

**Consequence to understand before continuing.** With no next line and no known
video duration, the clip now runs to the full `MAX_CLIP_SECONDS` ceiling (40s)
rather than the old invented 10s. This is the loosest case in the whole design.
It only fires when a selection reaches the last line of a transcript *and* the
duration is unknown (cloud planning). In cloud mode the browser encoder then
clamps to the real media length. Accepted, but it is why Step 5b matters.

- [ ] **Step 5b: Give analyze the full line context**

Without this, EVERY analyze candidate takes the trailing-run path above and is
estimated at 40s, because `app.py` groups each candidate over its own lines in
isolation — so there is never a "next line" to bound it.

In `app.py:267`, replace:

```python
            grouped = _group_lines_into_segments(seg_line_dicts, set(lines))
```

with:

```python
            # Group over the FULL line list, not just this candidate's lines.
            # The clip end is the first unselected line after the run, which
            # only exists with full context — in isolation every candidate
            # would hit the trailing-run path and be estimated at the ceiling.
            # This also matches how generator_app groups (all_lines), keeping
            # the create-screen estimate aligned with the real cut.
            grouped = _group_lines_into_segments(all_lines, set(lines))
```

- [ ] **Step 5c: Add a regression test for the analyze estimate**

Append to `tests/test_app.py`:

```python
def test_analyze_estimate_is_bounded_by_the_next_line(client, tmp_path):
    """A candidate's estimate must be bounded by the next unselected line.

    Grouping a candidate in isolation leaves no next line, so every estimate
    would run to the MAX_CLIP_SECONDS ceiling and the length slider would show
    every candidate as 40s.
    """
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:00] Speaker: First point here.\n"
        "[0:10] Speaker: Second point here.\n"
        "[0:20] Speaker: Third point here.\n"
        "[0:30] Speaker: Fourth point here.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:00-0:00|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "x"})
    seg = resp.get_json()["segments"]["vid.mp4"][0]
    from shared import MAX_CLIP_SECONDS
    assert seg["duration_seconds"] == 10.0, (
        f"expected the clip to end at the next line (0:10); got "
        f"{seg['duration_seconds']}s"
    )
    assert seg["duration_seconds"] < MAX_CLIP_SECONDS
```

- [ ] **Step 6: Run the new tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_shared.py -k "rich_clip or plain_clip" -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Delete tests that guard the removed mechanism**

Delete these three tests entirely — they assert behaviour that no longer exists:

- `tests/test_shared.py::test_clip_tail_buffer_survives_the_start_bias`
- `tests/test_generator_app.py::test_group_lines_into_segments_caps_trailing_dead_air`
- `tests/test_generator_app.py::test_group_lines_into_segments_long_line_keeps_full_speech`

- [ ] **Step 8: Update duration expectations**

In `tests/test_generator_app.py`, both `test_duration_seconds_is_sum_of_clip_durations` and `test_duration_seconds_excludes_failed_clip` compute `one_word_clip` from the deleted constants. In each, replace the `one_word_clip` computation and its `import` with a literal derived from the new behaviour, and update the assertion.

For `test_duration_seconds_is_sum_of_clip_durations`: a single plain line `[0:05] Speaker: Hello.`, no following line, and `get_video_duration` returns `None` (the fixture `vid.mp4` is an empty `touch()`ed file, so ffprobe fails). The clip therefore runs to the ceiling: `5.0 → 45.0` = 40s.

```python
    # Plain tier, single line, no next line, unknown video duration -> the clip
    # runs to the MAX_CLIP_SECONDS ceiling. A title card would have added ~5s
    # on top, which is what this test actually guards.
    assert result["duration_seconds"] == 40, (
        f"duration_seconds={result['duration_seconds']}; expected 40 (clip content only)"
    )
```

For `test_duration_seconds_excludes_failed_clip`: the surviving clip is `[0:05] Speaker: First.` bounded by the next line `[0:15]`, so 10.0s. (The second clip starts at `0:25`, runs to the ceiling, and is the one made to fail.)

```python
    # Only segment 1 survives: 0:05 -> next line 0:15 = 10s.
    assert result["duration_seconds"] == 10, (
        f"duration_seconds={result['duration_seconds']}; "
        "failed clip must not contribute to duration"
    )
```

In `tests/test_app.py`, both estimate tests encode the old arithmetic.

For `test_analyze_returns_segments_with_scores` — two lines at `0:05` and `0:15`, both selected, and no third line in the transcript to bound the run, so it reaches the ceiling from `5.0`:

```python
    # Plain tier: nothing follows 0:15 in this transcript and the video duration
    # is unknown, so the clip runs to the MAX_CLIP_SECONDS ceiling from 0:05.
    # No word-count estimation is involved.
    assert seg["duration_seconds"] == 40.0
    assert seg["start_seconds"] == 5.0 and seg["end_seconds"] == 45.0
```

For `test_analyze_estimate_matches_generator_when_candidates_merge` — with Step 5b's full-context grouping the two candidates are no longer symmetric: candidate 1 is bounded by the next line (`0:00 → 0:30` = 30s), candidate 2 reaches the ceiling (`0:30 → 0:70` = 40s), summing to 70s against a merged cut of 40s. The per-segment `20.0` assertion was an artifact of the old symmetry and must go; the total is what the test exists to guard:

```python
    from shared import MAX_CLIP_SECONDS
    total = sum(s["duration_seconds"] for s in segs)
    assert total == pytest.approx(MAX_CLIP_SECONDS), (
        f"estimate {total}s over-promises; generator cuts {MAX_CLIP_SECONDS}s"
    )
    # Scaled proportionally from 30s and 40s; the split is uneven, so assert the
    # relationship rather than a per-segment constant.
    assert segs[0]["duration_seconds"] < segs[1]["duration_seconds"]
    for s in segs:
        assert s["end_seconds"] == pytest.approx(s["start_seconds"] + s["duration_seconds"])
```

- [ ] **Step 9: Run the full Python suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass. Runtime ~3-5 minutes.

If `test_analyze_estimate_matches_generator_when_candidates_merge` fails, read the actual values from the failure message and update the expectations, keeping the assertion's *intent* (summed estimate must equal what the generator cuts).

- [ ] **Step 10: Commit**

```bash
git add shared.py tests/test_shared.py tests/test_app.py tests/test_generator_app.py
git commit -m "feat(reels): clip ends from real timestamps, not word counts

Rich transcripts end a clip at the last selected line's real end. Plain
transcripts end at the next line's start. Deletes CLIP_TAIL_RATE and the
speech_end estimate, and the last-line '+10.0' invention.

SPEAKING_RATE and TAIL_BUFFER survive only inside normalize_transcript, where
they size the sentence-splitting window on the plain tier; they no longer
influence any clip end.

Removes three tests that guarded the deleted estimate."
```

---

## Task 6: Caption cue times from real ends

**Files:**
- Modify: `captions.py:54-66` (`collect_caption_lines`), `captions.py:98-126` (window + chunk timing)
- Test: `tests/test_captions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_captions.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_captions.py -k "carries_end or rich_cue or plain_cue" -v`
Expected: first two FAIL (`KeyError`/wrong timing); the third should already pass.

- [ ] **Step 3: Carry `end_seconds` through `collect_caption_lines`**

In `captions.py`, replace the dict built in `collect_caption_lines`:

```python
        {
            "text": line["text"],
            "seconds": line["seconds"],
            "end_seconds": line.get("end_seconds"),
        }
```

- [ ] **Step 4: Use the real end for the cue window**

In `captions.py`, in `build_webvtt`, replace the window calculation:

```python
            # Rich: the sentence's own end. Plain: the next selected line's
            # start, or the clip end.
            win_start = clip_start + (line["seconds"] - seg["start_sec"])
            if line.get("end_seconds") is not None:
                win_end = clip_start + (line["end_seconds"] - seg["start_sec"])
            elif i + 1 < len(lines):
                win_end = clip_start + (lines[i + 1]["seconds"] - seg["start_sec"])
            else:
                win_end = clip_end
```

- [ ] **Step 5: Skip `MAX_CUE_SEC` when the end is real**

In `captions.py`, replace the cue-end line inside the chunk loop:

```python
                nominal_end = win_start + window * (acc / total)
                # MAX_CUE_SEC exists to stop a caption lingering when the window
                # was guessed. With a real end the window is exact, so the cap
                # would only truncate a correct cue.
                if line.get("end_seconds") is not None:
                    cue_end = nominal_end
                else:
                    cue_end = min(nominal_end, cue_start + MAX_CUE_SEC)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_captions.py -v`
Expected: PASS, including pre-existing caption tests.

- [ ] **Step 7: Commit**

```bash
git add captions.py tests/test_captions.py
git commit -m "feat(captions): time cues from real sentence ends

Cue windows were partitioned by character count across a whole turn, so timing
drifted. With real ends a cue lasts exactly as long as its sentence, and
MAX_CUE_SEC (a guard against guessed windows) no longer applies."
```

---

## Task 7: Transcriber emits the rich format

**Files:**
- Modify: `transcriber.py:1-5` (add ceil formatter), `transcriber.py:8-39` (`_split_into_sentences`), `transcriber.py:42-47` (`_segment_to_dict`), `transcriber.py:50-61` (`transcribe_video`)
- Test: `tests/test_transcriber.py`

**Ends round UP** (see the precision note at the top). Truncating an end clips audio.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transcriber.py`:

```python
def test_split_into_sentences_returns_start_and_end():
    from transcriber import _split_into_sentences
    seg = {
        "start": 0.0, "text": "One. Two.",
        "words": [
            {"word": "One.", "start": 0.0, "end": 1.5},
            {"word": " Two.", "start": 2.0, "end": 3.5},
        ],
    }
    assert _split_into_sentences(seg) == [(0.0, 1.5, "One."), (2.0, 3.5, "Two.")]


def test_transcribe_emits_rich_lines_with_ceiled_ends():
    from transcriber import transcribe_video

    class FakeWord:
        def __init__(self, word, start, end):
            self.word, self.start, self.end = word, start, end

    class FakeSegment:
        start, end, text = 0.0, 3.9, "Hello there."
        words = [FakeWord("Hello", 0.2, 1.0), FakeWord(" there.", 1.1, 3.9)]

    class FakeModel:
        def transcribe(self, path, **kw):
            return [FakeSegment()], None

    out = transcribe_video("ignored.mp4", model=FakeModel())
    # start floors to 0:00, end CEILS to 0:04 — never truncate an end or the
    # last word is clipped.
    assert out == "[0:00-0:04] Speaker: Hello there."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_transcriber.py -k "start_and_end or ceiled" -v`
Expected: FAIL — tuples are 2-long and output has no end.

- [ ] **Step 3: Add the ceiling formatter**

In `transcriber.py`, add after `_seconds_to_timestamp`:

```python
def _seconds_to_timestamp_ceil(seconds: float) -> str:
    """Round UP to the next whole second.

    Used for END timestamps only. Truncating an end moves it earlier, which
    clips the speaker's final word — the defect this format exists to remove.
    Starts still truncate (earlier is safe lead-in).
    """
    import math
    total = int(math.ceil(seconds - 1e-9))
    return f"{total // 60}:{total % 60:02d}"
```

- [ ] **Step 4: Return ends from `_split_into_sentences`**

In `transcriber.py`, replace `_split_into_sentences` body:

```python
    words = segment.get("words", [])
    if not words:
        return [(segment["start"], segment.get("end"), segment["text"].strip())]

    sentences: list[tuple[float, float, str]] = []
    sentence_start = words[0]["start"]
    sentence_words: list[str] = []

    for i, word in enumerate(words):
        word_text = word["word"]
        sentence_words.append(word_text)
        if word_text.rstrip().endswith((".", "!", "?")):
            sentence = "".join(sentence_words).strip()
            if sentence:
                sentences.append((sentence_start, word["end"], sentence))
            sentence_start = words[i + 1]["start"] if i + 1 < len(words) else sentence_start
            sentence_words = []

    # Flush any remaining words that didn't end with terminal punctuation
    if sentence_words:
        sentence = "".join(sentence_words).strip()
        if sentence:
            sentences.append((sentence_start, words[-1]["end"], sentence))

    return sentences
```

Update the docstring: each sentence now carries the start of its first word and the end of its last.

- [ ] **Step 5: Carry segment end through `_segment_to_dict`**

In `transcriber.py`, replace the return:

```python
    return {"start": segment.start, "end": segment.end,
            "text": segment.text, "words": words}
```

- [ ] **Step 6: Emit the rich line**

In `transcriber.py`, replace the loop in `transcribe_video`:

```python
    for segment in segments:
        seg_dict = _segment_to_dict(segment)
        for start, end, text in _split_into_sentences(seg_dict):
            ts = _seconds_to_timestamp(start)
            if end is None:
                lines.append(f"[{ts}] Speaker: {text}")
            else:
                lines.append(f"[{ts}-{_seconds_to_timestamp_ceil(end)}] Speaker: {text}")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_transcriber.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add transcriber.py tests/test_transcriber.py
git commit -m "feat(transcripts): emit real end times from Whisper

Whisper already computed a word-level end for every word and the format threw
it away. Ends round UP: truncating an end moves it earlier and clips the last
word. Existing cached .txt files stay plain until re-transcribed."
```

---

## Task 8: Bump the selection key to v3

**Files:**
- Modify: `static/app.js:357`, `static/app.js:409`, `static/app.js:792`
- Create: `tests/js/selection_key.test.mjs`

Re-exporting a transcript changes every `raw` string, so v2 entries would restore, render nothing, and ship dead strings to the generator.

- [ ] **Step 1: Write the failing test**

Create `tests/js/selection_key.test.mjs`:

```javascript
import { readFileSync } from 'node:fs';
import assert from 'node:assert';

const src = readFileSync('static/app.js', 'utf8');

export function test_selection_key_is_v3() {
  assert.ok(src.includes('sizzle_sel_v3_'),
    'selection key must be v3: rich transcripts change every raw line string');
}

export function test_no_v2_key_remains() {
  assert.ok(!src.includes('sizzle_sel_v2_'),
    'a leftover v2 key would restore stale selections that match no rendered line');
}

export function test_every_selection_key_site_uses_the_same_version() {
  const versions = new Set([...src.matchAll(/sizzle_sel_(v\d+)_/g)].map(m => m[1]));
  assert.strictEqual(versions.size, 1,
    `all selection key sites must agree, found: ${[...versions].join(', ')}`);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/js/run.mjs`
Expected: `test_selection_key_is_v3` and `test_no_v2_key_remains` FAIL.

- [ ] **Step 3: Bump all three sites**

In `static/app.js`, replace `sizzle_sel_v2_` with `sizzle_sel_v3_` at lines 357, 409, and 792. All three must change together.

- [ ] **Step 4: Run to verify it passes**

Run: `node tests/js/run.mjs`
Expected: `4 passed, 0 failed` (3 selection-key tests + the sanity test).

- [ ] **Step 5: Commit**

```bash
git add static/app.js tests/js/selection_key.test.mjs
git commit -m "fix(ui): bump selection key to v3 for rich transcript line strings

A rich transcript's raw line is [M:SS-M:SS] Speaker: text, so re-exporting a
transcript invalidates every stored selection. Stale entries would restore into
state.checked, render nothing, then fail generation with 'No segments found in
selections' — the same failure the v2 bump fixed."
```

---

## Task 9: Fix the skipped-clip log

**Files:**
- Modify: `static/reel-encoder.js` (the skip log message)

`/plan` serialises the field as `video` (`generator_app.py:897`), but the log reads `seg.video_name`, printing `undefined` and hiding which file was affected.

- [ ] **Step 1: Verify the field name**

Run: `grep -n '"video"' generator_app.py`
Expected: line 897 shows `"video": seg["video_name"],` — the browser receives `video`, not `video_name`.

- [ ] **Step 2: Fix the message**

In `static/reel-encoder.js`, in the clip-skip branch, replace:

```javascript
          log(`⚠ Clip ${i + 1} skipped — ${seg.video_name} is shorter than ${seg.start_sec.toFixed(1)}s`);
```

with:

```javascript
          // /plan serialises this field as `video` (generator_app.py), not
          // `video_name` — reading the latter printed "undefined".
          log(`⚠ Clip ${i + 1} skipped — ${seg.video} is shorter than ${seg.start_sec.toFixed(1)}s`);
```

- [ ] **Step 3: Verify no other `video_name` reads remain in the browser code**

Run: `grep -n 'video_name' static/reel-encoder.js`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add static/reel-encoder.js
git commit -m "fix(reels): name the file in the skipped-clip log

/plan serialises video_name as 'video', so the warning printed 'undefined is
shorter than 555.0s' and hid which source was affected."
```

---

## Task 10: Full verification

- [ ] **Step 1: Run the Python suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass, no failures. ~3-5 minutes.

- [ ] **Step 2: Run the JS suite**

Run: `node tests/js/run.mjs`
Expected: `4 passed, 0 failed`.

- [ ] **Step 3: Confirm no estimation constant reaches a clip end**

Run: `grep -n 'CLIP_TAIL_RATE' shared.py generator_app.py app.py`
Expected: no output — the constant is gone.

Run: `grep -n 'SPEAKING_RATE\|TAIL_BUFFER' shared.py`
Expected: matches only in the constants block and inside `normalize_transcript`. Any match inside `group_lines_into_segments` is a regression.

- [ ] **Step 4: Generate a reel from a plain-tier folder**

Start the app per CLAUDE.md, load a folder of existing (plain) transcripts, analyze, and generate. Confirm the reel builds and clips end at the next line's start. Expect somewhat looser endings than before — that is the documented interim.

- [ ] **Step 5: Generate a reel from a rich-tier folder**

Delete a test video's cached `.txt`, re-transcribe it (which now emits the rich format), and generate. Confirm clip ends land on the sentence end and captions track the speech.

- [ ] **Step 6: Commit any fixes, then push**

```bash
git push origin master
```

---

## Out of scope

- Transcripts referencing timestamps beyond their video's duration (source-side data problem; current skip behaviour retained).
- Word-level timings.
- Force-alignment against audio.
- UI signalling for mixed-tier folders.

# Priority-Scored Analysis + Reel-Length Slider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/analyze` score every relevant segment by compellingness, and add a reel-length slider that starts at a short "optimal" cut and adds/removes whole segments in priority order as the user drags it.

**Architecture:** Claude returns scored segments (`M:SS-M:SS|N`) in one pass. The backend maps ranges to transcript lines and returns a per-file `segments` list (plus the legacy `highlights` union for compatibility). All ranking, the optimal cut, the slider range, and slider→selection math run client-side over a flat candidate pool as pure JS functions. Generation is untouched — priority governs inclusion only.

**Tech Stack:** Python 3 / Flask (backend), vanilla JS (no framework), pytest. Reference spec: `docs/superpowers/specs/2026-07-10-priority-time-slider-design.md`.

---

## File Structure

- `claude_client.py` — scored system prompt + response format (modify `_SYSTEM_PROMPT`).
- `timestamp_parser.py` — add `parse_scored_timestamps`; keep `parse_timestamps`.
- `app.py` — `_run_analyze` returns `segments` (+ retained `highlights`).
- `static/app.js` — pool build, priority/optimal math (pure fns), slider wiring, add-analyze merge, persistence.
- `templates/index.html` — `#reel-length-row` markup.
- `static/style.css` — slider styling with DESIGN.md tokens.
- `tests/test_app.py` — parser + `/analyze` tests.

**Testing note (from CLAUDE.md):** run tests with `.\venv\Scripts\python.exe -m pytest tests/ -v`. ffmpeg-dependent code needs PowerShell PATH, but these tests don't invoke ffmpeg.

---

## Task 1: Score-aware timestamp parser

**Files:**
- Modify: `timestamp_parser.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py` (near the other unit tests):

```python
def test_parse_scored_timestamps_with_scores():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("0:05-0:20|9\n1:00-1:10|7") == [
        ("0:05-0:20", 9), ("1:00-1:10", 7)
    ]


def test_parse_scored_timestamps_missing_score_defaults_to_5():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("0:05-0:20") == [("0:05-0:20", 5)]


def test_parse_scored_timestamps_garbled_score_defaults_and_clamps():
    from timestamp_parser import parse_scored_timestamps
    # non-integer -> default 5; out-of-range -> clamp to 1..10
    assert parse_scored_timestamps("0:05-0:20|foo") == [("0:05-0:20", 5)]
    assert parse_scored_timestamps("0:05-0:20|99") == [("0:05-0:20", 10)]
    assert parse_scored_timestamps("0:05-0:20|0") == [("0:05-0:20", 1)]


def test_parse_scored_timestamps_none():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("none") is None


def test_parse_scored_timestamps_commas_and_whitespace():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("0:05-0:20|8, 1:00-1:10|6") == [
        ("0:05-0:20", 8), ("1:00-1:10", 6)
    ]


def test_parse_timestamps_still_returns_ranges_only():
    from timestamp_parser import parse_timestamps
    assert parse_timestamps("0:05-0:20|9\n1:00-1:10|7") == ["0:05-0:20", "1:00-1:10"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k parse_scored -v`
Expected: FAIL with `ImportError: cannot import name 'parse_scored_timestamps'`.

- [ ] **Step 3: Implement the scored parser**

Replace the contents of `timestamp_parser.py` with:

```python
import re

_RANGE_RE = re.compile(r'(\d+:\d{2}-\d+:\d{2})(?:\s*\|\s*([^\s,]+))?')


def parse_scored_timestamps(response: str) -> list[tuple[str, int]] | None:
    """Parse Claude's scored segment response.

    Each segment is 'M:SS-M:SS' optionally followed by '|N' (N = 1..10).
    Missing or non-integer score defaults to 5; out-of-range scores clamp to
    1..10. Returns None when the response is exactly 'none' (case-insensitive)
    or contains no ranges.
    """
    response = response.strip()
    if response.lower() == "none":
        return None
    result: list[tuple[str, int]] = []
    for rng, raw_score in _RANGE_RE.findall(response):
        score = 5
        if raw_score:
            try:
                score = max(1, min(10, int(raw_score)))
            except ValueError:
                score = 5
        result.append((rng, score))
    return result or None


def parse_timestamps(response: str) -> list[str] | None:
    """Backward-compatible: return just the ranges, dropping any scores."""
    scored = parse_scored_timestamps(response)
    if scored is None:
        return None
    return [rng for rng, _ in scored]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k "parse_scored or parse_timestamps" -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add timestamp_parser.py tests/test_app.py
git commit -m "feat: score-aware timestamp parser"
```

---

## Task 2: Scored system prompt

**Files:**
- Modify: `claude_client.py:5-19` (`_SYSTEM_PROMPT`)

No test — this is prompt text (behavioral, not unit-testable). Verified indirectly by Task 3's response-shape tests using a mocked Claude response.

- [ ] **Step 1: Update the system prompt**

In `claude_client.py`, replace the `_SYSTEM_PROMPT` string. Change the output format and the "return only 2–4" rule; keep every other rule verbatim. New value:

```python
_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify every timestamp range where the speaker directly and substantively addresses the prompt topic, and rate how compelling each one is.

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
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
- If the prompt asks for positive opinions, only return segments where the speaker's reaction is clearly positive or enthusiastic. Skip neutral mentions, passing references, and negative opinions even if the topic word appears.
- Only use timestamps that appear verbatim in the transcript
- The transcript may label speakers (e.g. "Interviewer:", "Agent:", "Participant:"). Only return ranges spoken by the respondent/participant. Never return a range where the interviewer, agent, or moderator is speaking, even if the topic word appears in their question.
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or extra punctuation — just the scored segments, one per line, or the word none"""
```

- [ ] **Step 2: Sanity check import still works**

Run: `.\venv\Scripts\python.exe -c "import claude_client; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add claude_client.py
git commit -m "feat: prompt Claude to score every relevant segment"
```

---

## Task 3: `_run_analyze` returns scored segments

**Files:**
- Modify: `app.py:212-273` (`_run_analyze`) and `app.py:33` (import)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_analyze_returns_segments_with_scores(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[0:15] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:05-0:20|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    data = resp.get_json()
    segs = data["segments"]["vid.mp4"]
    assert len(segs) == 1
    seg = segs[0]
    assert seg["score"] == 9
    assert seg["start"] == "0:05" and seg["end"] == "0:20"
    assert seg["duration_seconds"] == 15.0
    assert len(seg["lines"]) == 2  # both lines fall within 0:05-0:20


def test_analyze_highlights_is_union_of_segment_lines(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[0:15] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:05-0:20|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    data = resp.get_json()
    seg_lines = [l for s in data["segments"]["vid.mp4"] for l in s["lines"]]
    assert set(data["highlights"]["vid.mp4"]) == set(seg_lines)


def test_analyze_drops_interviewer_only_segment(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Interviewer: What did you think of the cod?\n"
        "[0:15] Speaker: The cod was superb.",
        encoding="utf-8",
    )
    # First range is only the interviewer line -> dropped; second maps to respondent.
    with patch("app.query_claude", return_value="0:05-0:09|8\n0:15-0:20|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "cod"})
    segs = resp.get_json()["segments"]["vid.mp4"]
    assert len(segs) == 1
    assert segs[0]["start"] == "0:15"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k "returns_segments or union_of_segment or interviewer_only" -v`
Expected: FAIL with `KeyError: 'segments'`.

- [ ] **Step 3: Update the import**

In `app.py`, change line 33 from:

```python
from timestamp_parser import parse_timestamps
```

to:

```python
from timestamp_parser import parse_scored_timestamps
```

- [ ] **Step 4: Rewrite `_run_analyze`**

Replace the body of `_run_analyze` (`app.py:212-273`) with:

```python
def _run_analyze(folder: str, prompt: str) -> dict:
    """Call Claude on every transcript in folder. Returns per-video scored
    segments plus a legacy `highlights` union of the matched lines."""
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        return {"error": str(exc)}
    video_paths = _filter_generated_reels(video_paths)

    def _analyze_one(vp: Path) -> tuple[str, list[dict], str | None]:
        """Analyze a single video. Returns (name, segments, error).

        Runs the (slow) Claude call plus timestamp matching for one video so the
        whole folder can be processed concurrently — a folder of many long videos
        analyzed serially takes long enough for the hosting proxy to time out and
        return an HTML error page the frontend can't parse as JSON.
        """
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists() or txt_path.stat().st_size == 0:
            return vp.name, [], None

        transcript = txt_path.read_text(encoding="utf-8")
        all_lines = _parse_transcript_lines(transcript)

        try:
            response = query_claude(transcript, prompt)
            scored = parse_scored_timestamps(response) or []
        except Exception as exc:
            return vp.name, [], f"{vp.name}: {exc}"

        segments: list[dict] = []
        for seg, score in scored:
            start_str, end_str = seg.split("-", 1)
            start_sec = parse_timestamp_to_seconds(start_str)
            end_sec = parse_timestamp_to_seconds(end_str)
            lines: list[str] = []
            for line in all_lines:
                if line.get("is_interviewer"):
                    continue  # analyze never auto-selects the interviewer
                if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                    if line["raw"] not in lines:
                        lines.append(line["raw"])
            if not lines:
                continue  # segment mapped to no respondent lines — drop it
            segments.append({
                "start": start_str,
                "end": end_str,
                "start_seconds": start_sec,
                "end_seconds": end_sec,
                "duration_seconds": max(0.0, end_sec - start_sec),
                "score": score,
                "lines": lines,
            })

        segments.sort(key=lambda s: s["start_seconds"])
        return vp.name, segments, None

    segments_by_file: dict[str, list[dict]] = {}
    highlights: dict[str, list[str]] = {}
    errors: list[str] = []

    # Run the per-video Claude calls concurrently. Wall time collapses from the
    # sum of every call to roughly the slowest single call, keeping the request
    # under the hosting proxy's timeout.
    max_workers = min(8, len(video_paths)) or 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_analyze_one, video_paths))

    for name, segments, error in results:
        segments_by_file[name] = segments
        # Legacy union: preserves the existing `highlights` contract for any
        # caller/test that still reads it.
        union: list[str] = []
        for seg in segments:
            for raw in seg["lines"]:
                if raw not in union:
                    union.append(raw)
        highlights[name] = union
        if error:
            errors.append(error)

    if len(errors) == len(video_paths) and not any(highlights.values()):
        return {"error": "; ".join(errors)}

    return {"segments": segments_by_file, "highlights": highlights}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k "analyze" -v`
Expected: PASS — including the pre-existing `test_analyze_returns_highlights` (still valid).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: /analyze returns scored segments plus legacy highlights"
```

---

## Task 4: Frontend selection math (pure functions)

**Files:**
- Modify: `static/app.js` (add a self-contained block near the top of the file, after the `state` object at `static/app.js:21`)

No JS test framework in the repo; these functions are pure and specified precisely. Verify by the manual reasoning checks in Step 2.

- [ ] **Step 1: Add the pure functions**

Insert immediately after the `state` object (after `static/app.js:21`):

```javascript
// ─── Priority selection model ───────────────────────────────────────────────
// A "candidate" is one scored segment: {file, score, duration_seconds,
// start_seconds, lines:[raw...]}. All math below is pure (no DOM, no network).

const OPTIMAL_MIN_SCORE = 8;      // quality bar for the optimal cut
const OPTIMAL_SOFT_CAP_SECONDS = 180;  // ~3 min soft cap on the optimal cut

// Flatten the /analyze `segments` payload into one candidate array.
// fileOrder is the array of filenames in state.files order (for tie-breaking).
function buildCandidatePool(segmentsByFile, fileOrder) {
  const pool = [];
  fileOrder.forEach(file => {
    (segmentsByFile[file] || []).forEach(seg => {
      pool.push({
        file,
        score: seg.score,
        duration_seconds: seg.duration_seconds,
        start_seconds: seg.start_seconds,
        lines: seg.lines,
      });
    });
  });
  return pool;
}

// Deterministic priority order: score desc, duration asc, file order, start asc.
function sortByPriority(pool, fileOrder) {
  const fileIndex = f => {
    const i = fileOrder.indexOf(f);
    return i === -1 ? fileOrder.length : i;
  };
  return [...pool].sort((a, b) =>
    b.score - a.score ||
    a.duration_seconds - b.duration_seconds ||
    fileIndex(a.file) - fileIndex(b.file) ||
    a.start_seconds - b.start_seconds
  );
}

// Cumulative durations along the priority order — the slider's snap points.
// Returns [d1, d1+d2, ...] (length === ordered.length).
function cumulativeDurations(ordered) {
  const sums = [];
  let total = 0;
  ordered.forEach(c => { total += c.duration_seconds; sums.push(total); });
  return sums;
}

// The optimal cut: all candidates scoring >= OPTIMAL_MIN_SCORE, falling back to
// the highest score present; trimmed to the soft cap but never below 1 segment.
// Returns the optimal duration in seconds (a valid snap point).
function optimalDuration(ordered) {
  if (ordered.length === 0) return 0;
  let qualifying = ordered.filter(c => c.score >= OPTIMAL_MIN_SCORE);
  if (qualifying.length === 0) {
    const top = ordered[0].score;  // ordered is score-desc, so [0] is highest
    qualifying = ordered.filter(c => c.score === top);
  }
  // qualifying is a prefix of `ordered` by construction (highest-priority items).
  // Trim from the end until under the soft cap, keeping at least one.
  let dur = qualifying.reduce((s, c) => s + c.duration_seconds, 0);
  while (qualifying.length > 1 && dur > OPTIMAL_SOFT_CAP_SECONDS) {
    dur -= qualifying[qualifying.length - 1].duration_seconds;
    qualifying = qualifying.slice(0, -1);
  }
  return dur;
}

// Given a target duration, return the priority PREFIX whose cumulative duration
// is the largest snap point <= target, but always >= 1 segment.
function prefixForDuration(ordered, targetSeconds) {
  if (ordered.length === 0) return [];
  const sums = cumulativeDurations(ordered);
  let k = 1;  // always at least one segment
  for (let i = 0; i < sums.length; i++) {
    if (sums[i] <= targetSeconds + 1e-6) k = i + 1;
  }
  return ordered.slice(0, k);
}
```

- [ ] **Step 2: Manual verification (reason through, no code to run)**

Confirm by hand against the spec:
- `sortByPriority` on `[{score:7,dur:3},{score:9,dur:5},{score:9,dur:2}]` → scores `9,9,7`; within the 9s, the 2s before the 5s. ✔ (score desc, then duration asc)
- `optimalDuration` with scores `[9,8,6,5]` durations `[2,3,4,4]` and no cap breach → qualifying = the two `>=8` (prefix), = `5`. ✔
- `optimalDuration` with all scores `4`, durations `[2,3]` → fallback to highest-present (both 4s) = `5`. ✔
- `prefixForDuration(ordered, target)` never returns `[]` when `ordered` is non-empty, and never exceeds `target` except the forced first segment. ✔

- [ ] **Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat: priority selection math for reel-length slider"
```

---

## Task 5: Slider markup and styling

**Files:**
- Modify: `templates/index.html:141-146` (after `#analyze-add-row`)
- Modify: `static/style.css` (append a slider block)

- [ ] **Step 1: Add the slider markup**

In `templates/index.html`, immediately after the `#analyze-add-row` div (closes at line 146), insert:

```html
<div id="reel-length-row" class="reel-length-row hidden">
  <div class="reel-length-status">
    <span id="reel-length-label">Reel length</span>
  </div>
  <div class="reel-slider-wrap">
    <input id="reel-slider" type="range" class="reel-slider"
           min="0" max="0" step="0.1" value="0"
           aria-label="Reel length in seconds">
    <div id="reel-optimal-marker" class="reel-optimal-marker" aria-hidden="true"></div>
  </div>
  <div class="reel-slider-ends">
    <span id="reel-slider-min">0:00</span>
    <span id="reel-slider-max">0:00</span>
  </div>
</div>
```

- [ ] **Step 2: Add the styles**

Append to `static/style.css`. Use existing DESIGN.md tokens — reuse the same accent variable the current Analyze button uses. First confirm the token name:

Run: `.\venv\Scripts\python.exe -c "import re,io; print([l.strip() for l in open('static/style.css',encoding='utf-8') if '--' in l and ('amber' in l.lower() or 'accent' in l.lower())][:10])"`
Expected: prints the amber/accent CSS variable name(s). Use that variable below in place of `var(--accent)` if it differs.

```css
/* Reel-length slider (priority-scored analysis) */
.reel-length-row {
  padding: 8px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.reel-length-status {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted, #8a8578);
}
.reel-length-status.custom { font-style: italic; }
.reel-slider-wrap { position: relative; }
.reel-slider {
  width: 100%;
  accent-color: var(--accent, #E8A33D);
}
.reel-optimal-marker {
  position: absolute;
  top: -3px;
  width: 0; height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 7px solid var(--accent, #E8A33D);
  transform: translateX(-5px);
  pointer-events: none;
}
.reel-slider-ends {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-muted, #999);
}
```

- [ ] **Step 3: Commit**

```bash
git add templates/index.html static/style.css
git commit -m "feat: reel-length slider markup and styles"
```

---

## Task 6: Wire the slider to analysis and selection

**Files:**
- Modify: `static/app.js` — `runAnalyze` (`static/app.js:622-669`), add slider helpers, add `state` fields.

- [ ] **Step 1: Add slider state fields**

In the `state` object (`static/app.js:5-21`), add after `lastPrompt`:

```javascript
  pool: [],           // flat candidate array (buildCandidatePool output)
  poolOrdered: [],    // pool sorted into priority order
  sliderCustom: false,// true once the selection diverges from a priority prefix
```

- [ ] **Step 2: Add helpers to format time and apply a prefix**

Add near the other selection helpers (after the pure functions from Task 4):

```javascript
function _fmtSeconds(s) {
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
}

// Replace the current selection with the given candidate list's lines,
// applied to BOTH checked and highlighted (mirrors runAnalyze).
function _applyCandidatesToSelection(candidates) {
  state.files.forEach(f => {
    state.checked[f.name] = new Set();
    state.highlighted[f.name] = new Set();
  });
  candidates.forEach(c => {
    c.lines.forEach(l => {
      state.checked[c.file].add(l);
      state.highlighted[c.file].add(l);
    });
  });
}

// Rebuild the slider UI from state.poolOrdered. selectDuration: the duration to
// select at (defaults to optimal). Returns nothing; mutates selection + DOM.
function _refreshSlider(selectDuration) {
  const row = $('reel-length-row');
  const ordered = state.poolOrdered;
  if (ordered.length < 2) { row.classList.add('hidden'); return; }
  row.classList.remove('hidden');

  const sums = cumulativeDurations(ordered);
  const minD = sums[0];
  const maxD = sums[sums.length - 1];
  const optD = optimalDuration(ordered);
  const target = selectDuration == null ? optD : selectDuration;

  const slider = $('reel-slider');
  slider.min = minD;
  slider.max = maxD;
  slider.value = target;

  // optimal marker position as % of the min..max span
  const pct = maxD > minD ? ((optD - minD) / (maxD - minD)) * 100 : 0;
  $('reel-optimal-marker').style.left = `calc(${pct}% )`;
  $('reel-slider-min').textContent = _fmtSeconds(minD);
  $('reel-slider-max').textContent = _fmtSeconds(maxD);

  _applySliderSelection(target);
}

// Apply the priority prefix for `target` seconds and update label + counts.
function _applySliderSelection(target) {
  const ordered = state.poolOrdered;
  const prefix = prefixForDuration(ordered, target);
  _applyCandidatesToSelection(prefix);
  state.sliderCustom = false;

  const selDur = prefix.reduce((s, c) => s + c.duration_seconds, 0);
  $('reel-length-label').textContent =
    `Reel length · ${_fmtSeconds(selDur)} · ${prefix.length} of ${ordered.length} segments`;
  $('reel-length-status')?.classList?.remove('custom');
  const status = document.querySelector('.reel-length-status');
  if (status) status.classList.remove('custom');

  if (state.activeFile) renderTranscript(state.activeFile);
  state.files.forEach(f => refreshBadge(f.name));
  updateGenerateBtn();
  _saveSelections();
  _savePool();
}

// Called when the user manually edits lines — mark the slider "custom".
function markSliderCustom() {
  if (state.poolOrdered.length < 2) return;
  state.sliderCustom = true;
  const status = document.querySelector('.reel-length-status');
  if (status) status.classList.add('custom');
  $('reel-length-label').textContent = 'Custom selection · drag slider to reset';
  _savePool();
}
```

- [ ] **Step 3: Wire the slider input event**

Add near the other top-level `addEventListener` calls (e.g. after `static/app.js:583`):

```javascript
$('reel-slider').addEventListener('input', e => {
  const sums = cumulativeDurations(state.poolOrdered);
  // snap raw value to the nearest cumulative snap point
  const raw = parseFloat(e.target.value);
  let snapped = sums[0];
  for (const s of sums) { if (Math.abs(s - raw) < Math.abs(snapped - raw)) snapped = s; }
  e.target.value = snapped;
  _applySliderSelection(snapped);
});
```

- [ ] **Step 4: Build the pool in `runAnalyze`**

In `runAnalyze` (`static/app.js:646-659`), replace the block that applies `data.highlights` with pool construction. Change:

```javascript
    state.lastPrompt = prompt;

    // Apply returned highlights to BOTH sets so mode-switching preserves analysis
    state.files.forEach(f => {
      const lines = data.highlights[f.name] || [];
      state.checked[f.name] = new Set(lines);
      state.highlighted[f.name] = new Set(lines);
    });

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();
    $('analyze-add-row').classList.remove('hidden');
```

to:

```javascript
    state.lastPrompt = prompt;

    // Build the candidate pool from scored segments and select the optimal cut.
    state.pool = buildCandidatePool(data.segments || {}, state.files.map(f => f.name));
    state.poolOrdered = sortByPriority(state.pool, state.files.map(f => f.name));

    if (state.poolOrdered.length >= 2) {
      _refreshSlider();  // selects optimal, renders, saves
    } else {
      // 0 or 1 candidate: no slider — fall back to selecting whatever we have.
      _applyCandidatesToSelection(state.poolOrdered);
      $('reel-length-row').classList.add('hidden');
      if (state.activeFile) renderTranscript(state.activeFile);
      state.files.forEach(f => refreshBadge(f.name));
      updateGenerateBtn();
      _saveSelections();
    }
    $('analyze-add-row').classList.remove('hidden');
```

- [ ] **Step 5: Manual verification in the browser**

Run: `.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"` (PowerShell), open http://localhost:5000, load a folder with cached transcripts, and run Analyze. Confirm: the slider appears, the label reads "Reel length · M:SS · K of N segments", dragging changes the selection and snaps, and the ◆ marker sits at the optimal position.

- [ ] **Step 6: Commit**

```bash
git add static/app.js
git commit -m "feat: wire reel-length slider to analysis and selection"
```

---

## Task 7: Mark slider custom on manual edits

**Files:**
- Modify: `static/app.js` — the checkbox-toggle and highlight-commit paths.

- [ ] **Step 1: Find the manual-edit call sites**

Run: `.\venv\Scripts\python.exe -c "import re;[print(i+1, l.rstrip()) for i,l in enumerate(open('static/app.js',encoding='utf-8')) if '_saveSelections()' in l]"`
Expected: prints the line numbers where selection changes are persisted (checkbox toggle, highlight brush commit, add-analyze, slider). These are the manual-edit sites (excluding the analyze/slider ones already handled).

- [ ] **Step 2: Call `markSliderCustom()` on manual edits**

At each manual line-edit handler (the checkbox click toggle and the highlight-mode mouseup/commit — NOT `runAnalyze`, `_applySliderSelection`, or `runAddAnalyze`), add a call to `markSliderCustom()` right before its `_saveSelections()`. Example for the checkbox toggle handler:

```javascript
  // ...after toggling the line in state.checked / state.highlighted...
  markSliderCustom();
  _saveSelections();
```

Use the line numbers from Step 1 to place the call at each genuine manual-edit site.

- [ ] **Step 3: Manual verification**

In the running app: analyze, then click a transcript line. Confirm the status flips to "Custom selection · drag slider to reset" and italicizes. Then drag the slider — confirm it returns to "Reel length · …" and re-selects the priority prefix.

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: flag slider as custom when user edits lines manually"
```

---

## Task 8: Additive analyze merges into the pool

**Files:**
- Modify: `static/app.js` — `runAddAnalyze` (`static/app.js:671-711`)

- [ ] **Step 1: Add a pool-merge helper**

Add near the pure functions:

```javascript
// Merge new scored segments into the pool. Overlapping ranges in the same file
// dedupe keeping the higher score. Returns the merged flat pool.
function mergeIntoPool(existingPool, segmentsByFile, fileOrder) {
  const merged = [...existingPool];
  const overlaps = (a, b) =>
    a.file === b.file &&
    a.start_seconds < b.end_seconds && b.start_seconds < a.end_seconds;
  fileOrder.forEach(file => {
    (segmentsByFile[file] || []).forEach(seg => {
      const cand = {
        file, score: seg.score,
        duration_seconds: seg.duration_seconds,
        start_seconds: seg.start_seconds,
        end_seconds: seg.start_seconds + seg.duration_seconds,
        lines: seg.lines,
      };
      const hit = merged.find(m => overlaps(
        { ...m, end_seconds: m.start_seconds + m.duration_seconds }, cand));
      if (hit) {
        if (cand.score > hit.score) Object.assign(hit, cand);
      } else {
        merged.push(cand);
      }
    });
  });
  return merged;
}
```

Note: candidates from Task 4's `buildCandidatePool` lack `end_seconds`; the overlap check reconstructs it from `start_seconds + duration_seconds`, so no change to `buildCandidatePool` is needed.

- [ ] **Step 2: Rewrite the success block of `runAddAnalyze`**

In `runAddAnalyze`, replace the union block (`static/app.js:689-701`):

```javascript
    // Union — add new lines without removing existing selections
    state.files.forEach(f => {
      const lines = data.highlights[f.name] || [];
      if (!state.checked[f.name])     state.checked[f.name]     = new Set();
      if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
      lines.forEach(l => state.checked[f.name].add(l));
      lines.forEach(l => state.highlighted[f.name].add(l));
    });

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();
```

with:

```javascript
    const fileOrder = state.files.map(f => f.name);
    // Merge new candidates into the pool; re-rank; widen the slider range.
    state.pool = mergeIntoPool(state.pool, data.segments || {}, fileOrder);
    state.poolOrdered = sortByPriority(state.pool, fileOrder);

    // Union the new qualifying (score >= OPTIMAL_MIN_SCORE) segments' lines into
    // the current selection, preserving the "add" intent.
    state.files.forEach(f => {
      if (!state.checked[f.name])     state.checked[f.name]     = new Set();
      if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
    });
    (Object.entries(data.segments || {})).forEach(([file, segs]) => {
      segs.filter(s => s.score >= OPTIMAL_MIN_SCORE).forEach(s => {
        s.lines.forEach(l => {
          state.checked[file].add(l);
          state.highlighted[file].add(l);
        });
      });
    });

    // Selection is generally no longer a clean prefix -> custom state.
    if (state.poolOrdered.length >= 2) {
      $('reel-length-row').classList.remove('hidden');
      const sums = cumulativeDurations(state.poolOrdered);
      const slider = $('reel-slider');
      slider.min = sums[0];
      slider.max = sums[sums.length - 1];
      const optD = optimalDuration(state.poolOrdered);
      const pct = sums[sums.length - 1] > sums[0]
        ? ((optD - sums[0]) / (sums[sums.length - 1] - sums[0])) * 100 : 0;
      $('reel-optimal-marker').style.left = `calc(${pct}% )`;
      $('reel-slider-min').textContent = _fmtSeconds(sums[0]);
      $('reel-slider-max').textContent = _fmtSeconds(sums[sums.length - 1]);
      markSliderCustom();
    }

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();
    _savePool();
```

- [ ] **Step 3: Manual verification**

In the running app: analyze one prompt, then use "+ Add to selection" with a second prompt. Confirm the slider range widens (max time increases if new segments were added), the status shows the custom state, and dragging the slider re-ranks across both prompts' segments.

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: additive analyze merges scored segments into the pool"
```

---

## Task 9: Persist the pool across reloads

**Files:**
- Modify: `static/app.js` — add `_savePool`/`_restorePool`, call from load and `_clearSelections`.

- [ ] **Step 1: Add save/restore helpers**

Add near `_saveSelections` (`static/app.js:154`):

```javascript
function _savePool() {
  if (!state.folder) return;
  try {
    localStorage.setItem('sizzle_pool_' + state.folder, JSON.stringify({
      pool: state.pool,
      sliderValue: parseFloat($('reel-slider')?.value || '0'),
      custom: state.sliderCustom,
    }));
  } catch (_) {}
}

function _restorePool() {
  if (!state.folder) return;
  try {
    const raw = localStorage.getItem('sizzle_pool_' + state.folder);
    if (!raw) return;
    const saved = JSON.parse(raw);
    const fileNames = new Set(state.files.map(f => f.name));
    state.pool = (saved.pool || []).filter(c => fileNames.has(c.file));
    state.poolOrdered = sortByPriority(state.pool, state.files.map(f => f.name));
    if (state.poolOrdered.length >= 2) {
      if (saved.custom) {
        // rebuild slider chrome without overwriting the restored selection
        _refreshSliderChromeOnly(saved.sliderValue);
        markSliderCustom();
      } else {
        _refreshSlider(saved.sliderValue);
      }
    }
  } catch (_) {}
}

// Update slider min/max/marker/value WITHOUT changing the current selection.
function _refreshSliderChromeOnly(value) {
  const ordered = state.poolOrdered;
  if (ordered.length < 2) return;
  $('reel-length-row').classList.remove('hidden');
  const sums = cumulativeDurations(ordered);
  const slider = $('reel-slider');
  slider.min = sums[0];
  slider.max = sums[sums.length - 1];
  slider.value = value || sums[0];
  const optD = optimalDuration(ordered);
  const pct = sums[sums.length - 1] > sums[0]
    ? ((optD - sums[0]) / (sums[sums.length - 1] - sums[0])) * 100 : 0;
  $('reel-optimal-marker').style.left = `calc(${pct}% )`;
  $('reel-slider-min').textContent = _fmtSeconds(sums[0]);
  $('reel-slider-max').textContent = _fmtSeconds(sums[sums.length - 1]);
}
```

- [ ] **Step 2: Restore on folder load**

At the end of the selection-restore block (`static/app.js:518`, just before the closing of that function), add:

```javascript
  _restorePool();
```

- [ ] **Step 3: Clear pool on generation success**

In `_clearSelections` (`static/app.js:174-186`), add before the `analyze-add-row` hide:

```javascript
  if (state.folder) {
    try { localStorage.removeItem('sizzle_pool_' + state.folder); } catch (_) {}
  }
  state.pool = [];
  state.poolOrdered = [];
  state.sliderCustom = false;
  $('reel-length-row')?.classList.add('hidden');
```

- [ ] **Step 4: Manual verification**

In the running app: analyze, then reload the page. Confirm the slider reappears with the same range and position, and the selection persists. Generate a reel; confirm the slider disappears and the pool key is gone (`localStorage.getItem('sizzle_pool_<folder>')` is null in devtools).

- [ ] **Step 5: Commit**

```bash
git add static/app.js
git commit -m "feat: persist candidate pool and slider position across reloads"
```

---

## Task 10: Full test run and final verification

- [ ] **Step 1: Run the whole Python suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass (including pre-existing `test_analyze_returns_highlights`).

- [ ] **Step 2: End-to-end browser check**

With the app running: load a folder, analyze, verify slider defaults to a short optimal cut (≈ ≤ 3 min), drag to both extremes, add a second prompt, reload, then generate. Confirm no console errors at each step (browser devtools console).

- [ ] **Step 3: Final commit if any fixups were needed**

```bash
git add -A
git commit -m "chore: verification fixups for reel-length slider"
```

---

## Self-Review Notes

- **Spec §1 (scored analysis):** Tasks 1–3. ✔
- **Spec §2 (priority/optimal math):** Task 4 (pure fns), used by Tasks 6/8/9. ✔
- **Spec §3 (slider UI, Option A):** Tasks 5–6. ✔
- **Spec §4 (additive analyze):** Task 8. ✔
- **Spec §5 (persistence `sizzle_pool_<folder>`):** Task 9. ✔
- **Spec §6 (error handling):** default-5 (Task 1), zero-line drop (Task 3), `<2` candidates hides slider (Task 6). ✔
- **Spec §7 (testing):** Tasks 1, 3, and Task 10 full run. ✔
- **Type consistency:** candidate shape `{file, score, duration_seconds, start_seconds, lines}` is consistent across `buildCandidatePool`, `sortByPriority`, `prefixForDuration`, `_applyCandidatesToSelection`; `mergeIntoPool` adds a transient `end_seconds` reconstructed from `start_seconds + duration_seconds`. Function names (`_refreshSlider`, `_applySliderSelection`, `markSliderCustom`, `_savePool`, `_restorePool`) are referenced consistently.

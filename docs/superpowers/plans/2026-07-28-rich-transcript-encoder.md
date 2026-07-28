# Rich Transcript Encoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `encoder` package that turns a video plus its plain Forven transcript into a rich `[M:SS-M:SS]` transcript, which `shared.py` already consumes as rich tier.

**Architecture:** A pure Python core (`forven` → `reconcile` → `emit`) with exactly one implementation of the alignment algorithm, wrapped by three entrypoints: a CLI, a Flask service with two endpoints, and (in a later plan) a browser client. The ASR is an *anchor* source, not a transcript source — Forven supplies every word that reaches the output. The package imports nothing from `app.py`, `generator_app.py`, or `shared.py`; its only coupling is the transcript file format.

**Tech Stack:** Python 3.11, pytest, Flask, faster-whisper (`decode_audio` reads `.webm`/`.mp4` directly — no ffmpeg dependency in this package).

**Spec:** `docs/superpowers/specs/2026-07-28-rich-transcript-encoder-design.md`

**Status: COMPLETE** (2026-07-28, commits `130c335`..`f5ac077`). All 10 tasks executed; 424 tests
pass. Task 10 validation encoded 11 real interviews — ten at 90–100% word match, one at 51.4% which
turned out to be the known truncated recording (`4e7ccf39`), confirming match rate doubles as a
truncation alarm. Checkboxes are left unticked as the plan-as-written record.

**Scope:** This plan covers spec build-order steps 1–2 (core, CLI, service). Steps 3–4 (browser ASR via transformers.js, cloud upload wiring) are a separate plan.

---

## File Structure

| File | Responsibility |
|---|---|
| `encoder/__init__.py` | Package marker |
| `encoder/core/forven.py` | The only place that knows Forven's input format: parse turns, split sentences |
| `encoder/core/reconcile.py` | Align Forven tokens ↔ ASR words → per-sentence times + confidence |
| `encoder/core/emit.py` | Render rich lines; rounding rules; monotonicity clamp; stats |
| `encoder/core/__init__.py` | `encode()` orchestrator — the public API |
| `encoder/asr/local.py` | faster-whisper → word stream (fallback path + CLI) |
| `encoder/cli.py`, `encoder/__main__.py` | `python -m encoder <folder>` |
| `encoder/service.py` | Flask app, two endpoints |
| `encoder/requirements.txt`, `encoder/Dockerfile` | Self-contained deploy |
| `tests/test_encoder_*.py` | Flat, matching existing repo convention |

---

### Task 1: `forven.py` — parse turns and split sentences

**Files:**
- Create: `encoder/__init__.py`, `encoder/core/__init__.py` (empty for now), `encoder/core/forven.py`
- Test: `tests/test_encoder_forven.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_encoder_forven.py`:

```python
from encoder.core.forven import parse, sentences


def test_parse_single_turn():
    result = parse("[02:01] Participant: We try to get him groomed.")
    assert result == [
        {"start": 121.0, "role": "Participant", "text": "We try to get him groomed."}
    ]


def test_parse_skips_unparseable_lines():
    assert parse("not a line\n\n[00:03] Interviewer: Hello.") == [
        {"start": 3.0, "role": "Interviewer", "text": "Hello."}
    ]


def test_parse_handles_unpadded_minutes():
    assert parse("[4:23] Participant: No.")[0]["start"] == 263.0


def test_sentences_splits_on_terminal_punctuation():
    assert sentences("He's a Corgi mix. We got him as a rescue.") == [
        "He's a Corgi mix.",
        "We got him as a rescue.",
    ]


def test_sentences_does_not_split_after_abbreviation():
    assert sentences("We eat around 4:00 or 5:00 p.m. Then he sleeps.") == [
        "We eat around 4:00 or 5:00 p.m. Then he sleeps."
    ]


def test_sentences_does_not_split_on_comma():
    text = "Um, one is a German shepherd mix and the other is a pit bull lab mix."
    assert sentences(text) == [text]


def test_sentences_keeps_short_answer_whole():
    assert sentences("Yeah.") == ["Yeah."]


def test_sentences_keeps_disfluency_prefix_attached():
    assert sentences("Um, we got him as a rescue.") == ["Um, we got him as a rescue."]


def test_sentences_does_not_split_mid_number():
    assert sentences("He is 10.5 years old.") == ["He is 10.5 years old."]


def test_sentences_empty_text():
    assert sentences("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_forven.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'encoder'`

- [ ] **Step 3: Write the implementation**

Create `encoder/__init__.py` and `encoder/core/__init__.py` as empty files. Create `encoder/core/forven.py`:

```python
"""Parse Forven plain transcripts into turns and sentences.

Forven exports one line per whole speaker turn:
    [MM:SS] Participant: Sentence one. Sentence two.

This module is the only place that knows the input format, so a change to
Forven's export is a change to this file alone.
"""
import re

# [MM:SS] Role: text. Forven zero-pads the minute; accept any width.
_LINE_RE = re.compile(r"^\[(\d+):(\d{2})\]\s+(\w[\w ]*?):\s*(.*)")

# Terminal punctuation, whitespace, then a character that can open a sentence.
# Requiring the following character stops "10.5" and "e.g. this" from splitting.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"“‘])')

# A candidate ENDING in one of these is an abbreviation rather than a sentence
# boundary, so it absorbs the following chunk. The trailing single-letter arm
# covers initials ("J. Smith"). Deliberately excludes "no." and "fig." -- both
# are common sentence endings, and a false merge costs a whole boundary.
_ABBREV_RE = re.compile(
    r"(?:\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc)|\b[ap]\.m|\b[A-Za-z])\.$",
    re.IGNORECASE,
)


def parse(text: str) -> list[dict]:
    """Parse a plain Forven transcript into turns.

    Returns [{"start": float, "role": str, "text": str}]. Unparseable lines are
    skipped, matching how shared.parse_transcript_lines treats bad input.
    """
    turns = []
    for raw in text.splitlines():
        match = _LINE_RE.match(raw.strip())
        if not match:
            continue
        turns.append({
            "start": float(int(match.group(1)) * 60 + int(match.group(2))),
            "role": match.group(3).strip(),
            "text": match.group(4).strip(),
        })
    return turns


def sentences(text: str) -> list[str]:
    """Split a turn's text into sentences.

    Splits on terminal punctuation followed by whitespace and a sentence-opening
    character; never on commas, and never after an abbreviation. Disfluency
    prefixes ("Um,") stay attached and short answers ("Yeah.") remain their own
    sentence -- both are clippable units a reel may need.
    """
    out: list[str] = []
    held = ""
    for chunk in _SENTENCE_RE.split(text.strip()):
        candidate = f"{held} {chunk}".strip() if held else chunk
        if _ABBREV_RE.search(candidate):
            held = candidate
            continue
        if candidate:
            out.append(candidate)
        held = ""
    if held:
        out.append(held)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_forven.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/__init__.py encoder/core/__init__.py encoder/core/forven.py tests/test_encoder_forven.py
git commit -m "feat(encoder): parse Forven turns and split sentences"
```

---

### Task 2: `reconcile.py` — align ASR words onto Forven sentences

**Files:**
- Create: `encoder/core/reconcile.py`
- Test: `tests/test_encoder_reconcile.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_encoder_reconcile.py`:

```python
from encoder.core.reconcile import align, normalize


def words(*triples):
    return [{"w": w, "s": s, "e": e} for w, s, e in triples]


def test_normalize_strips_punctuation_and_case():
    assert normalize(" Corgi,") == "corgi"
    assert normalize("don't") == "don't"
    assert normalize("...") == ""


def test_align_exact_match_takes_first_and_last_word_times():
    sentences = [{"role": "Participant", "text": "He's a Corgi mix."}]
    stream = words(("He's", 1.0, 1.2), ("a", 1.2, 1.3), ("Corgi", 1.3, 1.8), ("mix.", 1.8, 2.4))
    aligned, match_rate = align(sentences, stream)
    assert aligned[0]["start"] == 1.0
    assert aligned[0]["end"] == 2.4
    assert aligned[0]["anchor"] == "exact"
    assert aligned[0]["confidence"] == 1.0
    assert match_rate == 1.0


def test_align_tolerates_asr_errors_and_still_anchors():
    """One mis-transcribed word must not cost the sentence its boundaries."""
    sentences = [{"role": "Participant", "text": "He's a Corgi mix."}]
    stream = words(("He's", 1.0, 1.2), ("a", 1.2, 1.3), ("Corky", 1.3, 1.8), ("mix.", 1.8, 2.4))
    aligned, match_rate = align(sentences, stream)
    assert aligned[0]["start"] == 1.0
    assert aligned[0]["end"] == 2.4
    assert aligned[0]["anchor"] == "exact"
    assert aligned[0]["confidence"] == 0.75
    assert match_rate == 0.75


def test_align_two_sentences_get_separate_spans():
    sentences = [
        {"role": "Participant", "text": "Yeah."},
        {"role": "Participant", "text": "He sleeps a lot."},
    ]
    stream = words(
        ("Yeah.", 0.5, 1.0),
        ("He", 2.0, 2.2), ("sleeps", 2.2, 2.6), ("a", 2.6, 2.7), ("lot.", 2.7, 3.1),
    )
    aligned, _ = align(sentences, stream)
    assert (aligned[0]["start"], aligned[0]["end"]) == (0.5, 1.0)
    assert (aligned[1]["start"], aligned[1]["end"]) == (2.0, 3.1)


def test_align_unanchored_sentence_falls_back_after_previous_end():
    sentences = [
        {"role": "Participant", "text": "Yeah."},
        {"role": "Participant", "text": "Totally inaudible mumbling here."},
    ]
    stream = words(("Yeah.", 0.5, 1.0))
    aligned, _ = align(sentences, stream)
    assert aligned[1]["anchor"] == "none"
    assert aligned[1]["start"] == 1.0
    assert aligned[1]["end"] > aligned[1]["start"]
    assert aligned[1]["confidence"] == 0.0


def test_align_role_and_text_pass_through_untouched():
    sentences = [{"role": "Interviewer", "text": "Hello there."}]
    aligned, _ = align(sentences, words(("Hello", 0.0, 0.4)))
    assert aligned[0]["role"] == "Interviewer"
    assert aligned[0]["text"] == "Hello there."


def test_align_empty_inputs():
    aligned, match_rate = align([], [])
    assert aligned == []
    assert match_rate == 0.0


def test_align_sentence_with_no_usable_tokens():
    aligned, _ = align([{"role": "Participant", "text": "..."}], words(("Hi", 0.0, 0.5)))
    assert aligned[0]["anchor"] == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'encoder.core.reconcile'`

- [ ] **Step 3: Write the implementation**

Create `encoder/core/reconcile.py`:

```python
"""Map ASR word timings onto Forven's canonical sentences.

The ASR is an ANCHOR source, not a transcript source: Forven supplies every
word that reaches the output, and the ASR only says when those words were
spoken. Measured on a real interview, `tiny` anchors as many sentences as
`base` (47 of 48), because a sentence needs only one matched word to be pinned.
That is why the browser path can ship a small model.
"""
import difflib
import re

# Used ONLY to give an unanchored sentence a plausible duration. Never applied
# to a sentence that has a real anchor.
WORDS_PER_SECOND = 2.5

_STRIP_RE = re.compile(r"[^a-z0-9']")


def normalize(token: str) -> str:
    """Reduce a token to its comparable core: lowercase, alphanumerics, apostrophe."""
    return _STRIP_RE.sub("", token.lower())


def _fallback_duration(text: str) -> float:
    return max(1.0, len(text.split()) / WORDS_PER_SECOND)


def align(sentences: list[dict], words: list[dict]) -> tuple[list[dict], float]:
    """Attach start/end times to each sentence.

    sentences: [{"role": str, "text": str}]
    words:     [{"w": str, "s": float, "e": float}]

    Returns (aligned, match_rate). Each aligned sentence gains "start", "end",
    "confidence" (fraction of its tokens matched) and "anchor", one of:
      exact   -- both boundaries came from matched words
      partial -- one boundary matched, the other derived
      none    -- nothing matched; times are interpolated from the previous end
    """
    tokens: list[str] = []
    spans: list[tuple[int, int]] = []
    for sentence in sentences:
        first = len(tokens)
        tokens.extend(t for t in (normalize(w) for w in sentence["text"].split()) if t)
        spans.append((first, len(tokens) - 1))

    asr_tokens = [normalize(word["w"]) for word in words]

    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    matcher = difflib.SequenceMatcher(a=tokens, b=asr_tokens, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for offset in range(size):
            starts[a + offset] = words[b + offset]["s"]
            ends[a + offset] = words[b + offset]["e"]

    match_rate = len(starts) / len(tokens) if tokens else 0.0

    aligned: list[dict] = []
    previous_end = 0.0
    for sentence, (first, last) in zip(sentences, spans):
        start = next((starts[i] for i in range(first, last + 1) if i in starts), None)
        end = next((ends[i] for i in range(last, first - 1, -1) if i in ends), None)
        matched = sum(1 for i in range(first, last + 1) if i in starts)
        total = max(1, last - first + 1)

        if start is not None and end is not None and end > start:
            anchor = "exact"
        elif start is not None or end is not None:
            anchor = "partial"
            if start is None:
                start = previous_end
            if end is None or end <= start:
                end = start + _fallback_duration(sentence["text"])
        else:
            anchor = "none"
            start = previous_end
            end = start + _fallback_duration(sentence["text"])

        previous_end = end
        aligned.append({
            **sentence,
            "start": start,
            "end": end,
            "confidence": matched / total,
            "anchor": anchor,
        })

    return aligned, match_rate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_reconcile.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/core/reconcile.py tests/test_encoder_reconcile.py
git commit -m "feat(encoder): reconcile ASR word timings onto Forven sentences"
```

---

### Task 3: `emit.py` — rich lines, rounding, monotonicity clamp

**Files:**
- Create: `encoder/core/emit.py`
- Test: `tests/test_encoder_emit.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_encoder_emit.py`:

```python
from encoder.core.emit import rich, stats


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
        "sentences": 3, "exact": 1, "partial": 1, "unanchored": 1, "match_rate": 0.9612,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_emit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'encoder.core.emit'`

- [ ] **Step 3: Write the implementation**

Create `encoder/core/emit.py`:

```python
"""Render aligned sentences as a rich transcript.

Output is the format shared.transcript_tier classifies as "rich":
    [M:SS-M:SS] Role: sentence
"""
import math


def _stamp(total_seconds: int) -> str:
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def rich(aligned: list[dict]) -> str:
    """Render aligned sentences as rich transcript lines.

    Starts truncate and ends round UP. Truncating an end moves it earlier,
    clipping the speaker's final word -- the defect the rich format exists to
    remove. Erring early on a start costs at most a beat of lead-in, which the
    clip fade-in softens.

    Whole-second rounding can push a line's end past the next line's start, so
    an end is clamped to the following line's EMITTED start -- the value a
    reader actually parses. Where the clamp would collapse the line to zero
    length the overlap is kept instead: never clipping the last word matters
    more than strict monotonicity on a sub-second backchannel.
    """
    starts = [max(0, int(s["start"])) for s in aligned]
    lines = []
    for index, item in enumerate(aligned):
        start = starts[index]
        end = max(0, math.ceil(item["end"] - 1e-9))
        if index + 1 < len(aligned):
            clamped = min(end, starts[index + 1])
            if clamped > start:
                end = clamped
        lines.append(f"[{_stamp(start)}-{_stamp(end)}] {item['role']}: {item['text']}")
    return "\n".join(lines)


def stats(aligned: list[dict], match_rate: float) -> dict:
    """Per-file confidence summary, surfaced to the operator rather than stored."""
    return {
        "sentences": len(aligned),
        "exact": sum(1 for s in aligned if s["anchor"] == "exact"),
        "partial": sum(1 for s in aligned if s["anchor"] == "partial"),
        "unanchored": sum(1 for s in aligned if s["anchor"] == "none"),
        "match_rate": round(match_rate, 4),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_emit.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/core/emit.py tests/test_encoder_emit.py
git commit -m "feat(encoder): render rich lines with ceil-end and monotonicity clamp"
```

---

### Task 4: `core.encode()` orchestrator

**Files:**
- Modify: `encoder/core/__init__.py`
- Test: `tests/test_encoder_core.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_encoder_core.py`:

```python
from encoder.core import encode


def test_encode_end_to_end_splits_a_turn_into_rich_sentences():
    transcript = "[00:13] Participant: He's a Corgi mix. We got him as a rescue."
    words = [
        {"w": "He's", "s": 13.4, "e": 13.7},
        {"w": "a", "s": 13.7, "e": 13.9},
        {"w": "Corgi", "s": 13.9, "e": 14.4},
        {"w": "mix.", "s": 14.4, "e": 15.2},
        {"w": "We", "s": 16.0, "e": 16.2},
        {"w": "got", "s": 16.2, "e": 16.5},
        {"w": "him", "s": 16.5, "e": 16.7},
        {"w": "as", "s": 16.7, "e": 16.9},
        {"w": "a", "s": 16.9, "e": 17.0},
        {"w": "rescue.", "s": 17.0, "e": 17.8},
    ]
    result = encode(transcript, words)
    assert result["rich"].splitlines() == [
        "[0:13-0:16] Participant: He's a Corgi mix.",
        "[0:16-0:18] Participant: We got him as a rescue.",
    ]
    assert result["stats"]["sentences"] == 2
    assert result["stats"]["exact"] == 2
    assert result["stats"]["match_rate"] == 1.0


def test_encode_preserves_interviewer_role_for_downstream_exclusion():
    """shared.is_interviewer_label keys on this label, so it must survive."""
    result = encode("[00:00] Interviewer: Hello there.",
                    [{"w": "Hello", "s": 0.0, "e": 0.4}, {"w": "there.", "s": 0.4, "e": 0.9}])
    assert result["rich"] == "[0:00-0:01] Interviewer: Hello there."


def test_encode_empty_transcript():
    result = encode("", [])
    assert result["rich"] == ""
    assert result["stats"]["sentences"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_core.py -v`
Expected: FAIL — `ImportError: cannot import name 'encode'`

- [ ] **Step 3: Write the implementation**

Replace `encoder/core/__init__.py`:

```python
"""Pure transcript-encoding core.

encode() is the public API. Every entrypoint -- CLI, service, and the browser
path -- funnels through it, so the alignment algorithm has exactly one
implementation.
"""
from .emit import rich, stats
from .forven import parse, sentences
from .reconcile import align

__all__ = ["encode", "align", "parse", "rich", "sentences", "stats"]


def encode(transcript_text: str, words: list[dict]) -> dict:
    """Turn a plain Forven transcript plus ASR word timings into a rich transcript.

    Returns {"rich": str, "stats": dict}.
    """
    flat = [
        {"role": turn["role"], "text": sentence}
        for turn in parse(transcript_text)
        for sentence in sentences(turn["text"])
    ]
    aligned, match_rate = align(flat, words)
    return {"rich": rich(aligned), "stats": stats(aligned, match_rate)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_core.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/core/__init__.py tests/test_encoder_core.py
git commit -m "feat(encoder): add encode() orchestrator as the core public API"
```

---

### Task 5: Golden test against the real interview

Pins the measured spike result (48 sentences, 47 exact) so a regression in any core module is visible.

**Files:**
- Create: `tests/fixtures/encoder_forven_interview.txt`, `tests/fixtures/encoder_whisper_words.json`
- Test: `tests/test_encoder_golden.py`

- [ ] **Step 1: Create the fixtures**

Copy the plain transcript and the word stream generated during the design spike:

```bash
cp "FORVEN VIDEOS/forven-interview-14076753-fa84-42c1-8def-6b06014e3633.txt" tests/fixtures/encoder_forven_interview.txt
cp "$SCRATCHPAD/words.json" tests/fixtures/encoder_whisper_words.json
```

To regenerate the word stream from scratch (after Task 6 exists):

```bash
.\venv\Scripts\python.exe -c "import json; from encoder.asr.local import words; json.dump(words(r'FORVEN VIDEOS/forven-interview-14076753-fa84-42c1-8def-6b06014e3633.webm'), open('tests/fixtures/encoder_whisper_words.json','w'))"
```

- [ ] **Step 2: Write the test**

Create `tests/test_encoder_golden.py`:

```python
"""Golden test against a real Forven interview.

Pins the result measured during the design spike: 48 sentences, 47 with both
boundaries taken from matched ASR words. Asserts on shape and counts rather
than exact text so a wording change does not break the suite, while a real
regression in splitting, alignment, or rounding does.
"""
import json
import re
from pathlib import Path

import pytest

from encoder.core import encode

FIXTURES = Path(__file__).parent / "fixtures"
RICH_LINE_RE = re.compile(r"^\[(\d+):(\d{2})-(\d+):(\d{2})\] (\w[\w ]*): .+$")


@pytest.fixture
def result():
    transcript = (FIXTURES / "encoder_forven_interview.txt").read_text(encoding="utf-8-sig")
    words = json.loads((FIXTURES / "encoder_whisper_words.json").read_text(encoding="utf-8"))
    return encode(transcript, words)


def _seconds(minutes, secs):
    return int(minutes) * 60 + int(secs)


def test_golden_sentence_and_anchor_counts(result):
    assert result["stats"]["sentences"] == 48
    assert result["stats"]["exact"] >= 47
    assert result["stats"]["match_rate"] >= 0.90


def test_golden_every_line_is_valid_rich_format(result):
    lines = result["rich"].splitlines()
    assert len(lines) == 48
    for line in lines:
        assert RICH_LINE_RE.match(line), line


def test_golden_every_line_has_positive_duration(result):
    for line in result["rich"].splitlines():
        m = RICH_LINE_RE.match(line)
        assert _seconds(m.group(3), m.group(4)) > _seconds(m.group(1), m.group(2)), line


def test_golden_starts_are_non_decreasing(result):
    starts = [
        _seconds(*RICH_LINE_RE.match(line).group(1, 2))
        for line in result["rich"].splitlines()
    ]
    assert starts == sorted(starts)


def test_golden_both_roles_survive(result):
    roles = {RICH_LINE_RE.match(line).group(5) for line in result["rich"].splitlines()}
    assert roles == {"Interviewer", "Participant"}


def test_golden_output_is_rich_tier_to_the_consuming_app(result):
    """The whole point: shared.py must classify this as rich."""
    from shared import parse_transcript_lines, transcript_tier
    assert transcript_tier(parse_transcript_lines(result["rich"])) == "rich"
```

- [ ] **Step 3: Run the tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_golden.py -v`
Expected: 6 passed

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/encoder_forven_interview.txt tests/fixtures/encoder_whisper_words.json tests/test_encoder_golden.py
git commit -m "test(encoder): golden test pinning 47/48 exact anchors on a real interview"
```

---

### Task 6: `asr/local.py` — faster-whisper word stream

**Files:**
- Create: `encoder/asr/__init__.py` (empty), `encoder/asr/local.py`
- Test: `tests/test_encoder_asr.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_encoder_asr.py`:

```python
from unittest.mock import MagicMock

from encoder.asr.local import words


class FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class FakeSegment:
    def __init__(self, fake_words):
        self.words = fake_words


def test_words_flattens_segments_to_a_word_stream():
    model = MagicMock()
    model.transcribe.return_value = (
        [FakeSegment([FakeWord(" He's", 1.0, 1.2), FakeWord(" a", 1.2, 1.35)]),
         FakeSegment([FakeWord(" Corgi", 1.35, 1.9)])],
        None,
    )
    assert words("video.webm", model=model) == [
        {"w": " He's", "s": 1.0, "e": 1.2},
        {"w": " a", "s": 1.2, "e": 1.35},
        {"w": " Corgi", "s": 1.35, "e": 1.9},
    ]


def test_words_requests_word_timestamps():
    model = MagicMock()
    model.transcribe.return_value = ([], None)
    words("video.webm", model=model)
    assert model.transcribe.call_args.kwargs["word_timestamps"] is True


def test_words_tolerates_a_segment_with_no_words():
    model = MagicMock()
    model.transcribe.return_value = ([FakeSegment(None)], None)
    assert words("video.webm", model=model) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_asr.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'encoder.asr'`

- [ ] **Step 3: Write the implementation**

Create `encoder/asr/__init__.py` (empty) and `encoder/asr/local.py`:

```python
"""faster-whisper word timings -- the fallback path's ASR, and the CLI's.

faster-whisper decodes the source file itself (PyAV), so a 53 MB .webm can be
passed straight in; this package needs no ffmpeg. The model is only an ANCHOR
source, so `tiny` is a legitimate choice when speed matters more than word
match -- it anchors as many sentences as `base`.
"""
import threading

_model = None
_model_size = None
_lock = threading.Lock()

DEFAULT_MODEL_SIZE = "base"


def get_model(size: str = DEFAULT_MODEL_SIZE):
    """Load the Whisper model once, lazily.

    Double-checked lock, mirroring app._get_whisper_model, so importing this
    module costs nothing and the service's primary path never pays the RAM.
    """
    global _model, _model_size
    if _model is not None and _model_size == size:
        return _model
    with _lock:
        if _model is None or _model_size != size:
            from faster_whisper import WhisperModel
            _model = WhisperModel(size, device="cpu", compute_type="int8")
            _model_size = size
    return _model


def words(source, model=None, size: str = DEFAULT_MODEL_SIZE) -> list[dict]:
    """Transcribe `source` and return a flat word stream.

    Returns [{"w": str, "s": float, "e": float}] -- the interface the core
    consumes, identical to what the browser path produces.
    """
    model = model or get_model(size)
    segments, _info = model.transcribe(str(source), word_timestamps=True)
    return [
        {"w": word.word, "s": round(word.start, 3), "e": round(word.end, 3)}
        for segment in segments
        for word in (segment.words or [])
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_asr.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/asr/__init__.py encoder/asr/local.py tests/test_encoder_asr.py
git commit -m "feat(encoder): faster-whisper word-stream adapter"
```

---

### Task 7: CLI

**Files:**
- Create: `encoder/cli.py`, `encoder/__main__.py`
- Test: `tests/test_encoder_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_encoder_cli.py`:

```python
import json
from unittest.mock import patch

from encoder.cli import encode_folder, is_rich

WORDS = [
    {"w": "He's", "s": 13.4, "e": 13.7},
    {"w": "a", "s": 13.7, "e": 13.9},
    {"w": "Corgi", "s": 13.9, "e": 14.4},
    {"w": "mix.", "s": 14.4, "e": 15.2},
]
TRANSCRIPT = "[00:13] Participant: He's a Corgi mix."


def _folder(tmp_path):
    (tmp_path / "interview.mp4").write_bytes(b"fake")
    (tmp_path / "interview.txt").write_text(TRANSCRIPT, encoding="utf-8")
    return tmp_path


def test_is_rich_detects_end_timestamps():
    assert is_rich("[0:13-0:15] Participant: Hello.")
    assert not is_rich("[0:13] Participant: Hello.")


def test_encode_folder_writes_a_rich_sidecar(tmp_path):
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=WORDS):
        results = encode_folder(folder)
    assert (folder / "interview.rich.txt").read_text(encoding="utf-8").startswith(
        "[0:13-0:16] Participant:"
    )
    assert results[0]["stats"]["sentences"] == 1


def test_encode_folder_leaves_the_original_untouched(tmp_path):
    """The .txt on disk is client data -- never rewritten without --in-place."""
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=WORDS):
        encode_folder(folder)
    assert (folder / "interview.txt").read_text(encoding="utf-8") == TRANSCRIPT


def test_encode_folder_in_place_preserves_the_original_as_forven_txt(tmp_path):
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=WORDS):
        encode_folder(folder, in_place=True)
    assert (folder / "interview.forven.txt").read_text(encoding="utf-8") == TRANSCRIPT
    assert is_rich((folder / "interview.txt").read_text(encoding="utf-8"))


def test_encode_folder_skips_videos_with_no_transcript(tmp_path):
    (tmp_path / "orphan.mp4").write_bytes(b"fake")
    with patch("encoder.cli.words", return_value=WORDS) as asr:
        results = encode_folder(tmp_path)
    assert results == []
    asr.assert_not_called()


def test_encode_folder_skips_already_rich_transcripts(tmp_path):
    (tmp_path / "done.mp4").write_bytes(b"fake")
    (tmp_path / "done.txt").write_text("[0:13-0:15] Participant: Hi.", encoding="utf-8")
    with patch("encoder.cli.words", return_value=WORDS) as asr:
        results = encode_folder(tmp_path)
    assert results == []
    asr.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'encoder.cli'`

- [ ] **Step 3: Write the implementation**

Create `encoder/cli.py`:

```python
"""python -m encoder <folder> -- encode every video that has a plain transcript.

Local testing and backfill entrypoint. Deliberately writes a `.rich.txt`
sidecar by default: the `.txt` beside a video is client data, and this tool
does not overwrite it without being asked.
"""
import argparse
import re
import sys
from pathlib import Path

from .asr.local import DEFAULT_MODEL_SIZE, words
from .core import encode

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

_RICH_LINE_RE = re.compile(r"^\[\d+:\d{2}-\d+:\d{2}\]", re.MULTILINE)


def is_rich(text: str) -> bool:
    """True if the transcript already carries end timestamps."""
    return bool(_RICH_LINE_RE.search(text))


def encode_folder(folder, in_place: bool = False, size: str = DEFAULT_MODEL_SIZE,
                  log=lambda message: None) -> list[dict]:
    """Encode every video in `folder` that has a plain transcript beside it.

    Returns one {"video", "output", "stats"} per encoded video.
    """
    folder = Path(folder)
    results = []
    for video in sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES):
        plain = video.with_suffix(".txt")
        if not plain.exists():
            continue
        text = plain.read_text(encoding="utf-8-sig")
        if is_rich(text):
            log(f"skip {video.name}: already rich")
            continue

        log(f"encoding {video.name}")
        result = encode(text, words(video, size=size))

        if in_place:
            plain.replace(video.with_suffix(".forven.txt"))
            output = plain
        else:
            output = video.with_suffix(".rich.txt")
        output.write_text(result["rich"] + "\n", encoding="utf-8")

        stats = result["stats"]
        log(f"  {stats['sentences']} sentences, {stats['exact']} exact, "
            f"{stats['match_rate']:.1%} word match -> {output.name}")
        results.append({"video": video, "output": output, "stats": stats})
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m encoder", description=__doc__)
    parser.add_argument("folder", help="folder of videos with plain .txt transcripts")
    parser.add_argument("--in-place", action="store_true",
                        help="overwrite <video>.txt, preserving the original as <video>.forven.txt")
    parser.add_argument("--model", default=DEFAULT_MODEL_SIZE,
                        help=f"whisper model size (default: {DEFAULT_MODEL_SIZE})")
    args = parser.parse_args(argv)

    results = encode_folder(args.folder, in_place=args.in_place, size=args.model,
                            log=lambda message: print(message, flush=True))
    print(f"\nencoded {len(results)} video(s)")
    return 0
```

Create `encoder/__main__.py`:

```python
import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_cli.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/cli.py encoder/__main__.py tests/test_encoder_cli.py
git commit -m "feat(encoder): CLI for encoding a folder of interviews"
```

---

### Task 8: Flask service

**Files:**
- Create: `encoder/service.py`
- Test: `tests/test_encoder_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_encoder_service.py`:

```python
import io
import json
from unittest.mock import patch

import pytest

from encoder.service import create_app

TRANSCRIPT = "[00:13] Participant: He's a Corgi mix."
WORDS = [
    {"w": "He's", "s": 13.4, "e": 13.7},
    {"w": "a", "s": 13.7, "e": 13.9},
    {"w": "Corgi", "s": 13.9, "e": 14.4},
    {"w": "mix.", "s": 14.4, "e": 15.2},
]


@pytest.fixture
def client():
    return create_app(testing=True).test_client()


def test_health(client):
    assert client.get("/health").get_json() == {"ok": True}


def test_encode_words_returns_rich_and_stats(client):
    response = client.post("/encode/words", json={"transcript": TRANSCRIPT, "words": WORDS})
    assert response.status_code == 200
    body = response.get_json()
    assert body["rich"] == "[0:13-0:16] Participant: He's a Corgi mix."
    assert body["stats"]["exact"] == 1


def test_encode_words_rejects_missing_transcript(client):
    response = client.post("/encode/words", json={"words": WORDS})
    assert response.status_code == 400
    assert "transcript" in response.get_json()["error"]


def test_encode_words_rejects_non_list_words(client):
    response = client.post("/encode/words", json={"transcript": TRANSCRIPT, "words": "nope"})
    assert response.status_code == 400
    assert "words" in response.get_json()["error"]


def test_encode_words_rejects_malformed_word_entries(client):
    response = client.post("/encode/words",
                           json={"transcript": TRANSCRIPT, "words": [{"w": "hi"}]})
    assert response.status_code == 400


def test_encode_audio_runs_asr_then_the_same_core(client):
    with patch("encoder.service.words", return_value=WORDS) as asr:
        response = client.post(
            "/encode",
            data={"transcript": TRANSCRIPT,
                  "audio": (io.BytesIO(b"fake audio"), "align.opus")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert response.get_json()["rich"] == "[0:13-0:16] Participant: He's a Corgi mix."
    asr.assert_called_once()


def test_encode_audio_rejects_missing_audio(client):
    response = client.post("/encode", data={"transcript": TRANSCRIPT},
                           content_type="multipart/form-data")
    assert response.status_code == 400
    assert "audio" in response.get_json()["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'encoder.service'`

- [ ] **Step 3: Write the implementation**

Create `encoder/service.py`:

```python
"""Encoder HTTP service -- stateless, two endpoints, no database.

POST /encode/words  {transcript, words}      primary path: the browser did the ASR
POST /encode        multipart transcript+audio  fallback: we do the ASR

Both funnel into encoder.core.encode, so the alignment algorithm has one
implementation regardless of who produced the word stream.
"""
import os
import tempfile

from flask import Flask, jsonify, request
from flask_cors import CORS

from .asr.local import DEFAULT_MODEL_SIZE, words
from .core import encode

# The fallback path receives mono 16 kHz audio, ~0.7 MB for a 4-minute
# interview. 64 MB leaves generous headroom for a long one while still
# refusing a video upload, which this service must never receive.
MAX_CONTENT_LENGTH = 64 * 1024 * 1024


def _validate_words(payload):
    """Return an error string, or None when the word stream is usable."""
    if not isinstance(payload, list):
        return "words must be a list"
    for entry in payload:
        if not isinstance(entry, dict) or not {"w", "s", "e"} <= entry.keys():
            return "each word must be an object with w, s and e"
    return None


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    CORS(app, origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","))

    model_size = os.environ.get("ENCODER_MODEL_SIZE", DEFAULT_MODEL_SIZE)

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/encode/words")
    def encode_words():
        payload = request.get_json(silent=True) or {}
        transcript = payload.get("transcript")
        if not transcript or not isinstance(transcript, str):
            return jsonify({"error": "transcript is required"}), 400
        error = _validate_words(payload.get("words"))
        if error:
            return jsonify({"error": error}), 400
        return jsonify(encode(transcript, payload["words"]))

    @app.post("/encode")
    def encode_audio():
        transcript = request.form.get("transcript")
        if not transcript:
            return jsonify({"error": "transcript is required"}), 400
        upload = request.files.get("audio")
        if upload is None:
            return jsonify({"error": "audio is required"}), 400

        suffix = os.path.splitext(upload.filename or "")[1] or ".wav"
        handle, path = tempfile.mkstemp(suffix=suffix)
        os.close(handle)
        try:
            upload.save(path)
            stream = words(path, size=model_size)
        finally:
            os.unlink(path)
        return jsonify(encode(transcript, stream))

    return app


if __name__ == "__main__":
    create_app().run(port=5002, debug=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_encoder_service.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add encoder/service.py tests/test_encoder_service.py
git commit -m "feat(encoder): stateless Flask service with words and audio endpoints"
```

---

### Task 9: Deployment files and docs

**Files:**
- Create: `encoder/requirements.txt`, `encoder/Dockerfile`, `encoder/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create `encoder/requirements.txt`**

```
flask>=2.0
flask-cors
faster-whisper
```

- [ ] **Step 2: Create `encoder/Dockerfile`**

Build context is the repo root so the `encoder/` package copies as a unit.

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY encoder/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY encoder/ ./encoder/

ENV PORT=5002
EXPOSE 5002

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 600 'encoder.service:create_app()'"]
```

Add `gunicorn` to `encoder/requirements.txt`.

- [ ] **Step 3: Create `encoder/README.md`**

```markdown
# Transcript Encoder

Turns a video plus its plain Forven transcript into a **rich** transcript —
`[M:SS-M:SS] Speaker: sentence` — with real per-sentence start and end times.

Standalone: imports nothing from the Sizzle Reel app. The only contract is the
transcript file format, so this folder can be deployed on its own.

## CLI

    python -m encoder "FORVEN VIDEOS"
    python -m encoder "FORVEN VIDEOS" --in-place --model tiny

Writes `<video>.rich.txt`. With `--in-place`, writes `<video>.txt` and preserves
the original as `<video>.forven.txt` — the `.txt` beside a video is client data
and is never overwritten silently.

## Service

    POST /encode/words   {transcript, words}          -> {rich, stats}
    POST /encode         multipart transcript+audio   -> {rich, stats}
    GET  /health

`/encode/words` is the primary path: the browser runs the ASR and posts a ~30 KB
word list. `/encode` is the fallback for browsers that cannot, and takes mono
16 kHz audio (~0.7 MB for a 4-minute interview). **Never send video to this
service.**

## How it works

The ASR is an *anchor* source, not a transcript source — Forven supplies every
word that reaches the output. `difflib` maps the ASR's word stream onto Forven's
canonical text, so each sentence inherits real times while keeping the client's
exact wording and speaker labels. Measured on a real interview: 47 of 48
sentences got both boundaries from matched words, and the `tiny` model scored
the same as `base` on that metric.

## Env

- `ENCODER_MODEL_SIZE` — whisper size for the fallback path (default `base`)
- `ALLOWED_ORIGINS` — comma-separated CORS origins (default `*`)
```

- [ ] **Step 4: Document the package in `CLAUDE.md`**

Add after the "Shared lower-level modules" section:

```markdown
### Transcript encoder — `encoder/` (standalone)

Produces the **rich** transcripts `shared.py` consumes. Imports nothing from
`app.py` / `generator_app.py` / `shared.py`; the only contract is the file
format, so it deploys independently. See `encoder/README.md`.

- `encoder/core/` — pure: `forven` (parse/split) → `reconcile` (difflib align
  ASR words onto Forven text) → `emit` (rich lines, ceil-end, monotonicity
  clamp). `core.encode()` is the public API and the single implementation of
  the algorithm, shared by CLI, service, and the browser path.
- `encoder/asr/local.py` — faster-whisper word stream. Decodes `.webm`/`.mp4`
  directly via PyAV, so this package needs **no ffmpeg**.
- `encoder/service.py` — `POST /encode/words` (browser did the ASR, ~30 KB) and
  `POST /encode` (audio fallback, ~0.7 MB). Never send video to it.
- `encoder/cli.py` — `python -m encoder <folder>`.

**The ASR is an anchor source, not a transcript source.** Forven supplies every
word in the output; the ASR only says when it was spoken. This is why `tiny`
matches `base` on anchored-sentence count, and why the browser path is viable.
```

- [ ] **Step 5: Run the full suite and commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass, no regressions in existing tests

```bash
git add encoder/requirements.txt encoder/Dockerfile encoder/README.md CLAUDE.md
git commit -m "docs(encoder): deployment files and architecture notes"
```

---

### Task 10: Encode the real folder and verify

Not a code task — the validation the spec's §9 requires before trusting the encoder.

- [ ] **Step 1: Encode the Forven interviews**

```bash
.\venv\Scripts\python.exe -m encoder "FORVEN VIDEOS"
```

- [ ] **Step 2: Check the reported stats**

For each interview, confirm `exact` is within one or two of `sentences` and word match is above ~90%. Investigate anything materially worse — it means the ASR failed to anchor, most likely from noisy audio.

- [ ] **Step 3: Confirm the output is rich tier to the app**

```bash
.\venv\Scripts\python.exe -c "from shared import parse_transcript_lines, transcript_tier; from pathlib import Path; [print(p.name, transcript_tier(parse_transcript_lines(p.read_text(encoding='utf-8')))) for p in Path('FORVEN VIDEOS').glob('*.rich.txt')]"
```

Expected: every file prints `rich`.

- [ ] **Step 4: Watch clips**

Rename one `.rich.txt` to `<video>.txt` (keeping the original), load the folder in the app, select lines, and generate a reel. **Watching the clips is the only real proof the timings are right.** Check that clips start on the first word and do not clip the last.

---

## Follow-on plan (not this one)

Spec build-order steps 3–4: `static/transcript-encoder.js` (mediabunny audio extraction, transformers.js whisper-tiny in a Worker, capability detection with fallback to `POST /encode`) and wiring into the cloud upload flow.

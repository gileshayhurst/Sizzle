# Test Video Data Generation — Design Spec

**Date:** 2026-05-25  
**Status:** Approved

## Overview

Generate synthetic test data that simulates real consumer video surveys for the Sizzle Reel pipeline. The goal is 5 topic folders, each containing 4–5 respondents reviewing the same business, with pre-populated `.txt` transcripts so Whisper is bypassed and the Claude timestamp extraction + video editing stages are fully exercised.

## Motivation

Before going live with real survey footage, we need test data that mirrors production inputs: multiple people answering survey questions about the same business, in varied speech styles, covering topics unevenly. This lets us manually verify the sizzle reel output (watch it, confirm it pulled the right moments) and identify where Claude's timestamp extraction falters.

---

## Folder Structure

```
test_videos/
  riverside_grocery/         (5 respondents)
  bella_vista_restaurant/    (5 respondents)
  iron_fitness_gym/          (4 respondents)
  lakeview_hotel/            (4 respondents)
  morning_grounds_cafe/      (5 respondents)
```

Each folder contains one `.mp4` + one `.txt` per respondent, e.g.:

```
riverside_grocery/
  sarah_k.mp4
  sarah_k.txt
  mike_t.mp4
  mike_t.txt
  ...
```

---

## Businesses

| Folder | Business |
|--------|----------|
| `riverside_grocery/` | Riverside Market (grocery store) |
| `bella_vista_restaurant/` | Bella Vista Italian Restaurant |
| `iron_fitness_gym/` | Iron Fitness Gym |
| `lakeview_hotel/` | The Lakeview Hotel |
| `morning_grounds_cafe/` | Morning Grounds Coffee Shop |

---

## Transcript Design

### Speech Style Distribution (per folder)

Each folder's respondents are spread across three speech styles to simulate how real survey participants communicate differently:

**5-respondent folders** (`riverside_grocery`, `bella_vista_restaurant`, `morning_grounds_cafe`):
- 2 respondents — structured Q&A
- 2 respondents — free-form testimonial
- 1 respondent — hybrid

**4-respondent folders** (`iron_fitness_gym`, `lakeview_hotel`):
- 2 respondents — structured Q&A
- 1 respondent — free-form testimonial
- 1 respondent — hybrid

### Relevance Distribution (per folder)

For any given test prompt (e.g., "what did people say about checkout lines?"), respondents are designed with varying levels of relevance:

- **2–3 respondents — clearly relevant**: the pipeline should include them; content is unambiguous
- **1–2 respondents — partially relevant**: mentions the topic briefly or vaguely; tests whether Claude is too aggressive or too conservative
- **1 respondent — not relevant**: never meaningfully addresses the topic; pipeline should return `none` and exclude them

This distribution allows manual accuracy verification: after running the sizzle reel on a prompt, the expected set of included/excluded respondents is known in advance.

### Transcript Timing

- Each video is **3–5 minutes** long; transcript timestamps reflect this duration
- Relevant content is deliberately placed at **varying positions** across respondents (beginning, middle, end of video) to exercise clip extraction across the full video timeline
- Timestamp granularity mirrors Whisper output: segments every 5–20 seconds

---

## Video File Spec

| Property | Value |
|----------|-------|
| Format | `.mp4` (H.264 video + silent AAC audio) |
| Resolution | 640×480 |
| Framerate | 24fps |
| Duration | Derived from final transcript timestamp (3–5 min) |
| Visual | Solid color background (unique per topic folder) + respondent name text overlay |
| Audio | Silent AAC track (required for clean ffmpeg clip extraction with `-c copy`) |

Background colors (one per folder):
- `riverside_grocery` → `#3a7d44` (green)
- `bella_vista_restaurant` → `#c0392b` (red)
- `iron_fitness_gym` → `#2c3e50` (dark blue)
- `lakeview_hotel` → `#8e44ad` (purple)
- `morning_grounds_cafe` → `#a0522d` (brown)

---

## Generator Script

**File:** `create_test_data.py` (repo root)

**Responsibilities:**
- Defines all businesses, respondents, and transcript content as Python data structures
- Iterates over the data, writes each `.txt` transcript file
- Calls `subprocess.run(["ffmpeg", ...])` to generate each `.mp4` using `lavfi` (color source filter + drawtext)
- Idempotent: skips video generation if the `.mp4` already exists
- Prints progress per file

**Usage:**
```bash
python create_test_data.py
```

No arguments. Generates all folders and files under `test_videos/`. Safe to re-run.

**Dependencies:** Only `ffmpeg` (already required by the main pipeline). No new Python packages.

---

## Test Prompts

Suggested prompts to use when manually verifying each folder's sizzle reel output:

| Folder | Suggested test prompts |
|--------|----------------------|
| `riverside_grocery` | "what did people say about checkout lines", "comments about produce quality", "staff helpfulness" |
| `bella_vista_restaurant` | "feedback about wait times", "what did people say about the food", "comments about the atmosphere" |
| `iron_fitness_gym` | "what did people say about the equipment", "comments about cleanliness", "feedback on classes" |
| `lakeview_hotel` | "what did people say about check-in", "comments about room quality", "noise complaints" |
| `morning_grounds_cafe` | "what did people say about the coffee", "feedback on service speed", "comments about the atmosphere" |

For each prompt, the expected set of included/excluded respondents is deterministic (based on transcript content), enabling accuracy measurement.

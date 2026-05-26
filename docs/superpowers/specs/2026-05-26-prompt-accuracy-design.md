# Claude Prompt Accuracy Improvements — Design Spec

**Date:** 2026-05-26

## Overview

Targeted improvements to the Claude system prompt in `claude_client.py` to fix two observed accuracy problems:

1. **False negatives** — relevant clips are missed because the prompt requires content to "directly address" the topic, which is too strict
2. **Imprecise boundaries** — selected clips start too early, including preamble and lead-in sentences before the relevant statement

No architectural changes. The fix is entirely within the system prompt string.

---

## Changes to the System Prompt

### Problem 1: False negatives

**Current wording:** "identify the timestamp ranges where the speaker directly addresses the prompt topic"

**New wording:** "identify every timestamp range where the speaker addresses or meaningfully mentions the prompt topic"

**Why:** The word "directly" causes Claude to skip indirect but relevant mentions — e.g. a respondent who mentions checkout while describing their overall visit. Replacing it with "addresses or meaningfully mentions" broadens recall while still excluding pure tangential references.

Add a completeness instruction: "Scan the entire transcript. Return every range where the topic is addressed, not just the most prominent one."

### Problem 2: Imprecise boundaries

**New instruction to add:** "Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible."

This gives Claude explicit guidance that tight boundaries are preferred over conservative ones.

---

## Updated System Prompt (full text)

```
You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify every timestamp range where the speaker addresses or meaningfully mentions the prompt topic.

Return ONLY the timestamp ranges in the format: M:SS-M:SS
If multiple segments, separate with commas: M:SS-M:SS, M:SS-M:SS
If no relevant segments exist, return exactly: none

Rules:
- Scan the entire transcript. Return every range where the topic is addressed, not just the most prominent one.
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
- Only use timestamps that appear verbatim in the transcript
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or punctuation — just the timestamps or the word none
```

---

## Verification Plan

### Step 1: Finish test data generator

`create_test_data.py` is missing the `BUSINESSES` data structure and the `create_test_data()` orchestration (Tasks 3–4 of the previous plan). Implement these so `python create_test_data.py` generates all 23 respondents under `test_videos/`.

### Step 2: Run the updated pipeline on riverside_grocery

```
python sizzle.py test_videos/riverside_grocery --prompt what did people say about checkout lines
```

Expected respondents in output:
- `sarah_k` ✓ — clearly relevant, multiple checkout mentions
- `mike_t` ✓ — clearly relevant, extended checkout complaint
- `lynn_b` ✓ — clearly relevant, explicit checkout focus
- `diana_p` ~ — partial; brief mention, may or may not appear
- `james_r` ✗ — off-topic; should be excluded

Watch the output video and verify clips start close to the relevant sentence, not several sentences early.

### Step 3: Run across remaining folders

| Folder | Suggested prompt |
|--------|-----------------|
| `bella_vista_restaurant` | what did people say about the food |
| `iron_fitness_gym` | what did people say about the equipment |
| `lakeview_hotel` | what did people say about the room |
| `morning_grounds_cafe` | what did people say about the atmosphere |

Confirm no regressions (no clearly-irrelevant respondents included, clearly-relevant ones not missed).

---

## Files Changed

| File | Change |
|------|--------|
| `claude_client.py` | Replace `_SYSTEM_PROMPT` string |
| `create_test_data.py` | Add `BUSINESSES` data + implement `create_test_data()` |

No test file changes — unit tests mock the API call and verify parsing, not prompt quality. Prompt quality is verified by watching the sizzle reel output.

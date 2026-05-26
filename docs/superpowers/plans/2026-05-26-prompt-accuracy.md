# Prompt Accuracy Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two accuracy problems in the Claude timestamp extraction pipeline: missed relevant clips (false negatives) and clips that start too early (imprecise boundaries).

**Architecture:** Two parallel changes — rewrite the system prompt in `claude_client.py`, and finish the test data generator (`create_test_data.py`) so the improved prompt can be verified against known-correct synthetic data.

**Tech Stack:** Python stdlib, `anthropic` SDK (already installed), `ffmpeg` (already installed).

---

### Task 1: Update the system prompt in `claude_client.py`

**Files:**
- Modify: `claude_client.py`

There are no new unit tests to write for this task — the existing unit tests mock the API call and test parsing behaviour, not prompt quality. Prompt quality is verified in Task 3 by watching the actual sizzle reel output.

- [ ] **Step 1: Run the existing claude_client tests to confirm they currently pass**

```
pytest tests/test_claude_client.py -v
```

Expected: all tests PASS. This is a baseline — you want to confirm nothing is broken before you change anything.

- [ ] **Step 2: Replace `_SYSTEM_PROMPT` in `claude_client.py`**

Open `claude_client.py`. The current `_SYSTEM_PROMPT` string starts at line 5. Replace the entire string (lines 5–15) with:

```python
_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify every timestamp range where the speaker addresses or meaningfully mentions the prompt topic.

Return ONLY the timestamp ranges in the format: M:SS-M:SS
If multiple segments, separate with commas: M:SS-M:SS, M:SS-M:SS
If no relevant segments exist, return exactly: none

Rules:
- Scan the entire transcript. Return every range where the topic is addressed, not just the most prominent one.
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
- Only use timestamps that appear verbatim in the transcript
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or punctuation — just the timestamps or the word none"""
```

- [ ] **Step 3: Run the claude_client tests again to confirm they still pass**

```
pytest tests/test_claude_client.py -v
```

Expected: all tests still PASS. The tests mock the API response so prompt content doesn't affect them — this confirms you didn't accidentally break the function signature or return handling.

- [ ] **Step 4: Commit**

```
git add claude_client.py
git commit -m "feat: improve claude prompt for better recall and tighter timestamp boundaries"
```

---

### Task 2: Add BUSINESSES data to `create_test_data.py`

**Files:**
- Modify: `create_test_data.py`

This task adds all 23 respondents' transcript content as a Python data structure. No test changes needed — the existing test `test_create_test_data_calls_generate_video_for_each_respondent` asserts `call_count == 23` and will validate the count once `create_test_data()` is wired up in Task 3.

- [ ] **Step 1: Insert the BUSINESSES dict into `create_test_data.py`**

Open `create_test_data.py`. After the `import` lines and before `write_transcript`, insert the full `BUSINESSES` dict. The complete dict is in `docs/superpowers/plans/2026-05-25-test-video-data.md`, Task 3, Step 1 — copy it verbatim from there. It defines five top-level keys (`riverside_grocery`, `bella_vista_restaurant`, `iron_fitness_gym`, `lakeview_hotel`, `morning_grounds_cafe`), each with a `color` hex string and a `respondents` dict keyed by filename stem.

After inserting, `create_test_data.py` should look like:

```python
import subprocess
from pathlib import Path


BUSINESSES = {
    "riverside_grocery": {
        "color": "0x3a7d44",
        "respondents": {
            "sarah_k": { ... },
            ...
        },
    },
    ...
}


def write_transcript(path: str, content: str) -> None:
    ...


def generate_video(output_path: str, duration: int, color: str) -> None:
    ...


def create_test_data(output_dir: str) -> None:
    pass


if __name__ == "__main__":
    create_test_data("test_videos")
```

- [ ] **Step 2: Verify the data loaded correctly**

```
python -c "from create_test_data import BUSINESSES; total = sum(len(b['respondents']) for b in BUSINESSES.values()); print(f'Folders: {len(BUSINESSES)}, Respondents: {total}')"
```

Expected:
```
Folders: 5, Respondents: 23
```

If you see an import error, check for syntax issues in the dict (mismatched quotes, missing commas).

- [ ] **Step 3: Commit**

```
git add create_test_data.py
git commit -m "feat: add all 23 survey transcript definitions to BUSINESSES data"
```

---

### Task 3: Implement `create_test_data()` and verify end-to-end

**Files:**
- Modify: `create_test_data.py`

- [ ] **Step 1: Run the failing orchestration tests**

```
pytest tests/test_create_test_data.py -k "create_test_data" -v
```

Expected: 4 FAIL — the stub `create_test_data()` does nothing so no folders or files are created.

- [ ] **Step 2: Replace the `create_test_data` stub**

In `create_test_data.py`, replace:

```python
def create_test_data(output_dir: str) -> None:
    pass
```

With:

```python
def create_test_data(output_dir: str) -> None:
    base = Path(output_dir)
    for folder_name, business in BUSINESSES.items():
        folder = base / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        for filename, data in business["respondents"].items():
            txt_path = folder / f"{filename}.txt"
            mp4_path = folder / f"{filename}.mp4"
            write_transcript(str(txt_path), data["transcript"])
            generate_video(str(mp4_path), duration=data["duration"], color=business["color"])
            print(f"  {folder_name}/{filename}")
```

- [ ] **Step 3: Run all create_test_data tests**

```
pytest tests/test_create_test_data.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 4: Generate the test videos**

```
python create_test_data.py
```

Expected: 23 lines of output (one per respondent), then the script exits. This will take a minute or two — ffmpeg encodes 23 short video files.

```
  riverside_grocery/sarah_k
  riverside_grocery/mike_t
  riverside_grocery/diana_p
  riverside_grocery/james_r
  riverside_grocery/lynn_b
  bella_vista_restaurant/carlos_m
  bella_vista_restaurant/priya_s
  bella_vista_restaurant/tom_h
  bella_vista_restaurant/rachel_w
  bella_vista_restaurant/david_l
  iron_fitness_gym/alex_j
  iron_fitness_gym/brittany_f
  iron_fitness_gym/noah_c
  iron_fitness_gym/elena_r
  lakeview_hotel/mark_s
  lakeview_hotel/jessica_t
  lakeview_hotel/kevin_d
  lakeview_hotel/amy_n
  morning_grounds_cafe/oliver_p
  morning_grounds_cafe/sophia_r
  morning_grounds_cafe/ben_k
  morning_grounds_cafe/claire_m
  morning_grounds_cafe/theo_v
```

- [ ] **Step 5: Spot-check the output files**

```
python -c "
from pathlib import Path
folders = list(Path('test_videos').iterdir())
print(f'Folders: {len(folders)}')
for f in sorted(folders):
    files = list(f.iterdir())
    txts = [x for x in files if x.suffix == '.txt']
    mp4s = [x for x in files if x.suffix == '.mp4']
    print(f'  {f.name}: {len(mp4s)} mp4s, {len(txts)} txts')
"
```

Expected:
```
Folders: 5
  bella_vista_restaurant: 5 mp4s, 5 txts
  iron_fitness_gym: 4 mp4s, 4 txts
  lakeview_hotel: 4 mp4s, 4 txts
  morning_grounds_cafe: 5 mp4s, 5 txts
  riverside_grocery: 5 mp4s, 5 txts
```

- [ ] **Step 6: Run the pipeline on `riverside_grocery` with the checkout prompt**

```
python sizzle.py test_videos/riverside_grocery --prompt what did people say about checkout lines
```

Expected terminal output — the three clearly-relevant respondents should print timestamp ranges, james_r should print "no relevant segments found", diana_p may or may not appear (brief mention):

```
sarah_k.mp4:  <one or more M:SS-M:SS ranges>
mike_t.mp4:   <one or more M:SS-M:SS ranges>
diana_p.mp4:  no relevant segments found   ← acceptable either way
james_r.mp4:  no relevant segments found
lynn_b.mp4:   <one or more M:SS-M:SS ranges>
Sizzle reel saved to riverside_grocery.mp4
```

Open `riverside_grocery.mp4` and watch it. Verify:
- sarah_k, mike_t, and lynn_b are present
- james_r is absent
- Clips start close to the relevant sentence (not several sentences of preamble before the checkout content)

- [ ] **Step 7: Run the pipeline on the remaining four folders**

```
python sizzle.py test_videos/bella_vista_restaurant --prompt what did people say about the food
python sizzle.py test_videos/iron_fitness_gym --prompt what did people say about the equipment
python sizzle.py test_videos/lakeview_hotel --prompt what did people say about the room
python sizzle.py test_videos/morning_grounds_cafe --prompt what did people say about the atmosphere
```

For each, watch the output video. Confirm:
- Clearly-relevant respondents appear (carlos_m/priya_s/rachel_w for food; alex_j/brittany_f for equipment; mark_s/jessica_t for room; sophia_r/claire_m for atmosphere)
- Clearly-irrelevant respondents are absent (david_l for food; elena_r for equipment; amy_n for room; theo_v for atmosphere)

- [ ] **Step 8: Commit**

```
git add create_test_data.py
git commit -m "feat: implement create_test_data orchestration"
```

# Sizzle Reel Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI tool that reads timestamped video transcripts and a user prompt, then reports the timestamp ranges in each transcript where the content addresses the prompt.

**Architecture:** Single-pass per transcript — each `.txt` file is sent in full to the Claude API along with the user's prompt. Claude returns relevant timestamp ranges which are parsed and printed as a plain text report. Four focused modules handle loading, API interaction, parsing, and CLI orchestration.

**Tech Stack:** Python 3.11+, `anthropic` SDK, `pytest`, `pathlib`, `argparse`, `re`

---

## File Structure

```
Sizzle Reel/
├── sizzle.py                  # CLI entry point — main() only
├── loader.py                  # load_transcripts(folder_path)
├── claude_client.py           # query_claude(transcript, prompt)
├── timestamp_parser.py        # parse_timestamps(response)
├── requirements.txt           # anthropic, pytest
├── tests/
│   ├── test_loader.py
│   ├── test_claude_client.py
│   ├── test_timestamp_parser.py
│   └── fixtures/
│       ├── restaurant_review_1.txt
│       ├── restaurant_review_2.txt
│       └── off_topic.txt
└── docs/
    └── superpowers/
        ├── specs/2026-05-21-sizzle-reel-design.md
        └── plans/2026-05-21-sizzle-reel.md
```

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```
anthropic
pytest
```

- [ ] **Step 2: Install dependencies**

Run:
```
pip install -r requirements.txt
```

Expected: packages install without error. Verify with `python -c "import anthropic; print('ok')"`.

- [ ] **Step 3: Set ANTHROPIC_API_KEY environment variable**

The Claude client reads this from the environment. Get your key from https://console.anthropic.com.

```
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# Or add permanently via System Properties > Environment Variables
```

- [ ] **Step 4: Create tests directory and fixtures subdirectory**

Run:
```
mkdir -p tests/fixtures
```

- [ ] **Step 5: Commit**

```bash
git init
git add requirements.txt
git commit -m "chore: project setup"
```

---

## Task 2: Sample Test Fixtures

**Files:**
- Create: `tests/fixtures/restaurant_review_1.txt`
- Create: `tests/fixtures/restaurant_review_2.txt`
- Create: `tests/fixtures/off_topic.txt`

These fixtures are used for manual end-to-end testing in Task 6. Create them now so later tasks can reference them.

- [ ] **Step 1: Create restaurant_review_1.txt**

```
[0:05] Reviewer: So I recently visited Giordano's in Chicago.
[0:15] Reviewer: The pizza was absolutely amazing, deep dish at its finest.
[0:30] Reviewer: What really stood out to me was the service. Our waiter, Marco, was incredibly attentive.
[1:00] Reviewer: He checked on us multiple times without being intrusive.
[1:20] Reviewer: The wait time for the pizza was about 45 minutes, which is expected for deep dish.
[1:45] Reviewer: Overall, I'd give the hospitality a solid 9 out of 10.
[2:10] Reviewer: The atmosphere was cozy and the decor really matched the Chicago vibe.
[2:35] Reviewer: Prices are a bit steep but worth it for a special occasion.
```

Save to `tests/fixtures/restaurant_review_1.txt`.

- [ ] **Step 2: Create restaurant_review_2.txt**

```
[0:05] Reviewer: I took my family to Giordano's last weekend.
[0:18] Reviewer: The kids loved the environment and the staff were very welcoming.
[0:35] Reviewer: Our server was friendly but seemed a bit overwhelmed with other tables.
[1:05] Reviewer: She forgot one of our orders but quickly corrected it with a smile.
[1:30] Reviewer: The food quality was top notch, cheese pull was incredible.
[2:00] Reviewer: We waited quite a bit but the staff kept us updated on the wait time.
[2:30] Reviewer: I appreciate when servers are honest about timing, made the wait feel shorter.
[3:00] Reviewer: Would definitely come back, one of the better pizza spots in the city.
```

Save to `tests/fixtures/restaurant_review_2.txt`.

- [ ] **Step 3: Create off_topic.txt**

```
[0:05] Reviewer: Today I'm reviewing the parking situation near Giordano's.
[0:20] Reviewer: There's a public garage two blocks away that charges reasonable rates.
[0:45] Reviewer: Street parking is nearly impossible on weekends.
[1:10] Reviewer: I recommend using the garage rather than circling the block for an hour.
[1:35] Reviewer: The walk from the garage to the restaurant is very pleasant.
```

Save to `tests/fixtures/off_topic.txt`.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/
git commit -m "test: add sample transcript fixtures"
```

---

## Task 3: Transcript Loader

**Files:**
- Create: `loader.py`
- Create: `tests/test_loader.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_loader.py`:

```python
import pytest
from pathlib import Path
from loader import load_transcripts


def test_returns_dict_of_filename_to_text(tmp_path):
    (tmp_path / "video1.txt").write_text("[0:10] Speaker 1: Hello", encoding="utf-8")
    (tmp_path / "video2.txt").write_text("[0:05] Speaker 2: Hi", encoding="utf-8")
    result = load_transcripts(str(tmp_path))
    assert set(result.keys()) == {"video1.txt", "video2.txt"}
    assert "Hello" in result["video1.txt"]
    assert "Hi" in result["video2.txt"]


def test_raises_file_not_found_on_missing_folder():
    with pytest.raises(FileNotFoundError, match="Folder not found"):
        load_transcripts("/nonexistent/path/that/does/not/exist")


def test_raises_value_error_on_no_txt_files(tmp_path):
    (tmp_path / "notes.md").write_text("some notes")
    with pytest.raises(ValueError, match="No .txt files found"):
        load_transcripts(str(tmp_path))


def test_ignores_non_txt_files(tmp_path):
    (tmp_path / "video1.txt").write_text("[0:10] Speaker 1: Hello", encoding="utf-8")
    (tmp_path / "notes.md").write_text("some notes")
    result = load_transcripts(str(tmp_path))
    assert list(result.keys()) == ["video1.txt"]


def test_files_sorted_alphabetically(tmp_path):
    (tmp_path / "b_video.txt").write_text("b content", encoding="utf-8")
    (tmp_path / "a_video.txt").write_text("a content", encoding="utf-8")
    result = load_transcripts(str(tmp_path))
    assert list(result.keys()) == ["a_video.txt", "b_video.txt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_loader.py -v`

Expected: `ModuleNotFoundError: No module named 'loader'`

- [ ] **Step 3: Implement loader.py**

Create `loader.py`:

```python
from pathlib import Path


def load_transcripts(folder_path: str) -> dict[str, str]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise ValueError(f"No .txt files found in: {folder_path}")
    return {f.name: f.read_text(encoding="utf-8") for f in files}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_loader.py -v`

Expected: 5 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add loader.py tests/test_loader.py
git commit -m "feat: add transcript loader"
```

---

## Task 4: Timestamp Parser

**Files:**
- Create: `timestamp_parser.py`
- Create: `tests/test_timestamp_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timestamp_parser.py`:

```python
from timestamp_parser import parse_timestamps


def test_single_range():
    assert parse_timestamps("0:23-1:05") == ["0:23-1:05"]


def test_multiple_ranges():
    assert parse_timestamps("0:23-1:05, 2:14-2:40") == ["0:23-1:05", "2:14-2:40"]


def test_none_returns_none():
    assert parse_timestamps("none") is None


def test_none_is_case_insensitive():
    assert parse_timestamps("None") is None
    assert parse_timestamps("NONE") is None


def test_unparseable_returns_none():
    assert parse_timestamps("I cannot determine any relevant segments.") is None


def test_strips_surrounding_whitespace():
    assert parse_timestamps("  0:23-1:05  ") == ["0:23-1:05"]


def test_three_ranges():
    assert parse_timestamps("0:05-0:18, 1:30-2:00, 3:44-4:02") == [
        "0:05-0:18", "1:30-2:00", "3:44-4:02"
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_timestamp_parser.py -v`

Expected: `ModuleNotFoundError: No module named 'timestamp_parser'`

- [ ] **Step 3: Implement timestamp_parser.py**

Create `timestamp_parser.py`:

```python
import re


def parse_timestamps(response: str) -> list[str] | None:
    response = response.strip()
    if response.lower() == "none":
        return None
    pattern = r'\d+:\d{2}-\d+:\d{2}'
    matches = re.findall(pattern, response)
    return matches if matches else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timestamp_parser.py -v`

Expected: 7 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add timestamp_parser.py tests/test_timestamp_parser.py
git commit -m "feat: add timestamp parser"
```

---

## Task 5: Claude Client

**Files:**
- Create: `claude_client.py`
- Create: `tests/test_claude_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_client.py`:

```python
from unittest.mock import MagicMock, patch
from claude_client import query_claude


def _make_mock_response(text: str) -> MagicMock:
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


def test_returns_string_from_claude():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _make_mock_response("0:23-1:05")
        result = query_claude("[0:23] Speaker: Hello", "hospitality")
    assert result == "0:23-1:05"


def test_sends_transcript_in_user_message():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_client = mock_anthropic.return_value
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("my transcript text", "my prompt")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
    assert "my transcript text" in user_content


def test_sends_prompt_in_user_message():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_client = mock_anthropic.return_value
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("some transcript", "hospitality of waiters")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
    assert "hospitality of waiters" in user_content


def test_returns_none_string_when_no_match():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _make_mock_response("none")
        result = query_claude("[0:05] Speaker: Parking is hard to find.", "hospitality")
    assert result == "none"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_claude_client.py -v`

Expected: `ModuleNotFoundError: No module named 'claude_client'`

- [ ] **Step 3: Implement claude_client.py**

Create `claude_client.py`:

```python
import anthropic

_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify the timestamp ranges where the speaker directly addresses the prompt topic.

Return ONLY the timestamp ranges in the format: M:SS-M:SS
If multiple segments, separate with commas: M:SS-M:SS, M:SS-M:SS
If no relevant segments exist, return exactly: none

Rules:
- Only include segments that directly address the prompt topic, not tangential mentions
- Only use timestamps that appear verbatim in the transcript
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or punctuation — just the timestamps or the word none"""


def query_claude(transcript: str, prompt: str) -> str:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Transcript:\n{transcript}\n\nPrompt: {prompt}"
            }
        ]
    )
    return message.content[0].text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_claude_client.py -v`

Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add claude_client.py tests/test_claude_client.py
git commit -m "feat: add Claude API client"
```

---

## Task 6: CLI Entry Point

**Files:**
- Create: `sizzle.py`

No unit tests for `main()` — it is pure orchestration and is validated by the manual end-to-end test in Task 7.

- [ ] **Step 1: Implement sizzle.py**

Create `sizzle.py`:

```python
import argparse
import sys

from loader import load_transcripts
from claude_client import query_claude
from timestamp_parser import parse_timestamps


def main():
    parser = argparse.ArgumentParser(
        description="Generate a sizzle reel by finding relevant segments across video transcripts."
    )
    parser.add_argument("folder", help="Path to folder containing transcript .txt files")
    parser.add_argument("prompt", help="Topic to search for in the transcripts")
    args = parser.parse_args()

    try:
        transcripts = load_transcripts(args.folder)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    for filename, text in transcripts.items():
        try:
            response = query_claude(text, args.prompt)
            segments = parse_timestamps(response)
        except Exception as e:
            print(f"{filename}:  [warning: API error — {e}]")
            continue

        if segments:
            print(f"{filename}:  {', '.join(segments)}")
        else:
            print(f"{filename}:  no relevant segments found")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full test suite to make sure nothing is broken**

Run: `pytest tests/ -v`

Expected: all tests PASSED (no failures)

- [ ] **Step 3: Commit**

```bash
git add sizzle.py
git commit -m "feat: add CLI entry point"
```

---

## Task 7: End-to-End Manual Test

**Files:** none (uses existing fixtures and the live Claude API)

Requires `ANTHROPIC_API_KEY` to be set.

- [ ] **Step 1: Run with a narrow prompt (expect matches in reviews 1 and 2, not off_topic)**

Run:
```
python sizzle.py tests/fixtures "What do people say about the hospitality of the waiters?"
```

Expected output (exact timestamps will vary):
```
off_topic.txt:         no relevant segments found
restaurant_review_1.txt:  0:30-1:45
restaurant_review_2.txt:  0:18-1:05, 2:00-2:30
```

Verify: open `tests/fixtures/restaurant_review_1.txt` and confirm the reported range covers the waiter/service discussion.

- [ ] **Step 2: Run with a broad prompt (expect matches across all files)**

Run:
```
python sizzle.py tests/fixtures "What do people say about Giordano's?"
```

Expected: all three files return segments (off_topic discusses parking near Giordano's — may or may not match depending on Claude's judgment; either is acceptable).

- [ ] **Step 3: Run with an off-topic prompt (expect no matches in any file)**

Run:
```
python sizzle.py tests/fixtures "What do people say about the music playing in the restaurant?"
```

Expected:
```
off_topic.txt:         no relevant segments found
restaurant_review_1.txt:  no relevant segments found
restaurant_review_2.txt:  no relevant segments found
```

- [ ] **Step 4: Run with a bad folder path (expect clean error)**

Run:
```
python sizzle.py /nonexistent/path "some prompt"
```

Expected:
```
Error: Folder not found: /nonexistent/path
```

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "test: verify end-to-end manual tests pass"
```

# Sizzle Reel Generator — Design Spec
**Date:** 2026-05-21

## Overview

A Python CLI tool that takes a folder of timestamped video transcripts and a user prompt, then identifies and reports the timestamp ranges within each transcript where the content addresses the prompt. The goal is to save users time by surfacing only the relevant portions of multiple videos rather than requiring manual skimming.

## Architecture

Single-pass per transcript using the Claude API. Each transcript is sent to Claude in full along with the user's prompt. Claude identifies relevant segments and returns their timestamp ranges. Given that target videos are 5–10 minutes with natural pauses, transcripts will typically be 500–1,500 words — well within Claude's context window.

### CLI Interface

```
python sizzle.py <transcripts_folder> "<prompt>"
```

**Example:**
```
python sizzle.py ./transcripts "What do people say about the hospitality of the waiters?"
```

**Example output:**
```
video1.txt:  0:23–1:05, 2:14–2:40
video2.txt:  no relevant segments found
video3.txt:  0:05–0:18, 3:44–4:02
```

## Transcript Format

Input transcripts are `.txt` files in speaker-turn format:

```
[0:23] Speaker 1: "The waiter was incredibly attentive..."
[1:05] Speaker 2: "I thought the service was a bit slow..."
```

Each line begins with a timestamp in `[M:SS]` format, followed by a speaker label and their text.

## Components

| Function | Responsibility |
|---|---|
| `load_transcripts(folder_path)` | Scans the folder, reads each `.txt` file, returns `{filename: transcript_text}` |
| `query_claude(transcript, prompt)` | Constructs the system prompt, calls the Claude API, returns the raw response |
| `parse_timestamps(response)` | Extracts timestamp ranges from Claude's response; handles the "none" case |
| `main()` | Wires everything together, accepts CLI args via `argparse`, prints the report |

## Data Flow

1. User provides a folder path and a prompt string via CLI.
2. `load_transcripts` reads all `.txt` files from the folder into a dict.
3. For each transcript, `query_claude` sends the full transcript text and the user's prompt to Claude with a structured system prompt.
4. The system prompt instructs Claude to:
   - Identify only segments directly relevant to the user's prompt
   - Return timestamp ranges in the format `M:SS-M:SS` (comma-separated if multiple)
   - Return the literal string `none` if no relevant segments exist
5. `parse_timestamps` extracts the ranges from Claude's response.
6. `main` prints the final report to the terminal.

## System Prompt Design

The system prompt is the core engineering artifact. It must:
- Define "relevant" strictly (content that directly addresses the prompt topic, not tangential mentions)
- Specify the exact output format: `M:SS-M:SS, M:SS-M:SS` or `none`
- Prohibit fabricating timestamps — Claude must only use timestamps present in the transcript

## Error Handling

| Scenario | Behavior |
|---|---|
| Folder path does not exist | Print clear error and exit |
| No `.txt` files in folder | Print clear error and exit |
| Claude returns unparseable response | Log a per-file warning, continue processing remaining files |
| API error (rate limit, network) | Surface error message clearly and exit |
| Empty file or malformed transcript | Pass to Claude as-is; will return `none` |

## Dependencies

- `anthropic` — Claude API SDK
- `argparse` — CLI argument parsing (stdlib)
- `pathlib` — File handling (stdlib)

Install: `pip install anthropic`

## Testing

Two hand-crafted sample `.txt` transcripts covering a consistent topic (e.g., restaurant reviews):
- At least one file with clearly relevant segments
- At least one file with no relevant segments

Test prompts:
- **Narrow:** Specific topic with clear matches — verify correct timestamp ranges
- **Broad:** Should match many segments — verify comprehensive coverage
- **Off-topic:** Should return `no relevant segments found` for all files

Verification is manual: cross-reference reported timestamp ranges against the transcript text.

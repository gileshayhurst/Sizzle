# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Run all tests (use the venv python directly — plain pytest may not find modules)
.\venv\Scripts\python.exe -m pytest tests/ -v

# Run a single test file
.\venv\Scripts\python.exe -m pytest tests/test_loader.py -v

# Run a single test
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py::test_extract_clip_calls_correct_ffmpeg_args -v

# Run the tool
.\venv\Scripts\python.exe sizzle.py <videos_folder> --prompt your prompt words here
.\venv\Scripts\python.exe sizzle.py <videos_folder> --prompt your prompt words here --output custom_name.mp4

# Generate synthetic test data (creates MP4+TXT pairs in a folder)
.\venv\Scripts\python.exe create_test_data.py <output_folder>
```

## Environment

- Requires `ANTHROPIC_API_KEY` environment variable
- Requires `ffmpeg` installed as a system binary (`winget install ffmpeg` on Windows)
- **Windows note:** ffmpeg is on the PowerShell PATH but NOT the bash/tool-shell PATH. Run ffmpeg-dependent commands (sizzle.py, Whisper transcription) from PowerShell, not bash.
- Python dependencies: `pip install -r requirements.txt` (installs `anthropic`, `openai-whisper`, `pytest`)
- A `venv` is present in the repo root — activate before running, or prefix commands with `.\venv\Scripts\python.exe`

## Architecture

Three-stage pipeline orchestrated by `sizzle.py`:

**Stage 1 — Transcription** (`transcriber.py`, `loader.py`)
`scan_videos()` finds video files (`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`) in the input folder, sorted alphabetically. Each video is transcribed with local Whisper (`base` model, `word_timestamps=True`) and saved as `{video_name}.txt` alongside the source file. On subsequent runs, existing `.txt` files are reused — transcription is skipped if the file is non-empty.

Transcripts are emitted at **sentence level**: `_split_into_sentences()` in `transcriber.py` splits each Whisper segment on terminal punctuation and assigns each sentence the start time of its first word. This gives Claude sub-segment timestamp precision (typically ±1 second) rather than the coarser segment-level granularity. Transcript format: `[M:SS] Speaker: text`.

**Stage 2 — Timestamp extraction** (`claude_client.py`, `timestamp_parser.py`)
Each transcript is sent to Claude (`claude-opus-4-7`) with the user's prompt. The system prompt instructs Claude to:
- Return the 2–4 most substantive, clearly relevant segments (not every passing mention)
- Require the **primary subject** of each segment to match the prompt — not contextually adjacent items
- Start each range as late as possible (at the first word directly on topic) and end it as early as possible
- Apply sentiment filtering when the prompt requests positive/negative opinions

Claude returns `M:SS-M:SS` ranges (comma-separated, or `none`). `parse_timestamps()` extracts these with a regex.

**Stage 3 — Video assembly** (`video_editor.py`)
ffmpeg extracts each segment as a clip re-encoded to H.264/AAC (`-c:v libx264 -preset fast -c:a aac`) into a temp directory. Re-encoding is required — stream copy (`-c copy`) with output seeking produces clips that start on P/B frames, which the player cannot decode without a reference I-frame, causing a visible freeze at every transition. All clips are concatenated with the concat demuxer (`-c copy` is safe here because all clips are now properly formed with I-frame starts).

## Key Behaviours

- **Output filename** defaults to `{folder_name}{source_extension}` (e.g. `NOBU.mp4`). Because clips are re-encoded to H.264/AAC, the container must support those codecs; `.mp4` and `.mkv` always work.
- **Transcript caching:** delete `{video_name}.txt` alongside an MP4 to force re-transcription. This is needed to pick up sentence-level timestamp improvements if the file was transcribed with an older version.
- **Prompt** uses `nargs='+'` so no quotes are needed: words after `--prompt` are joined with spaces.
- **Whisper model** is loaded once in `sizzle.py` and passed into `transcribe_video()` to avoid reloading between videos.
- **ffmpeg errors** in `stitch_clips()` print stderr before raising — check terminal output for the raw ffmpeg message when debugging stitch failures.
- **Test data:** `create_test_data.py` generates synthetic MP4+TXT pairs (solid-colour videos with pre-written transcripts) across 5 business categories. Useful for prompt-engineering tests that don't require real footage.

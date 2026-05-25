# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_loader.py -v

# Run a single test
pytest tests/test_video_editor.py::test_extract_clip_calls_correct_ffmpeg_args -v

# Run the tool
python sizzle.py <videos_folder> --prompt your prompt words here
python sizzle.py <videos_folder> --prompt your prompt words here --output custom_name.mp4
```

## Environment

- Requires `ANTHROPIC_API_KEY` environment variable
- Requires `ffmpeg` installed as a system binary (`winget install ffmpeg` on Windows)
- Python dependencies: `pip install -r requirements.txt` (installs `anthropic`, `openai-whisper`, `pytest`)
- A `venv` is present in the repo root — activate before running

## Architecture

Three-stage pipeline orchestrated by `sizzle.py`:

**Stage 1 — Transcription** (`transcriber.py`, `loader.py`)
`scan_videos()` finds video files (`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`) in the input folder. Each video is transcribed with local Whisper (`base` model) and saved as `{video_name}.txt` alongside the source file. On subsequent runs, existing `.txt` files are reused — transcription is skipped if the file is non-empty. Transcripts are formatted as `[M:SS] Speaker: text`.

**Stage 2 — Timestamp extraction** (`claude_client.py`, `timestamp_parser.py`)
Each transcript is sent to Claude along with the user's prompt. Claude returns `M:SS-M:SS` timestamp ranges (or `none`). `parse_timestamps()` extracts these with a regex.

**Stage 3 — Video assembly** (`video_editor.py`)
ffmpeg extracts each segment as a clip (using `-c copy` — no re-encode) into a temp directory, preserving the source file extension. All clips are concatenated with ffmpeg's concat demuxer (`-c copy`) into the output file. The output extension matches the source format automatically.

## Key Behaviours

- **Output format** defaults to `{folder_name}.{source_extension}` — no re-encoding, so output and source must share a container format. If you add re-encoding, replace `-c copy` in `stitch_clips()` with `-c:v libx264 -c:a aac`.
- **Prompt** uses `nargs='+'` so no quotes are needed: words after `--prompt` are joined with spaces.
- **Whisper model** is loaded once in `sizzle.py` and passed into `transcribe_video()` to avoid reloading between videos.
- **ffmpeg errors** in `stitch_clips()` print stderr before raising — check terminal output for the raw ffmpeg message when debugging stitch failures.

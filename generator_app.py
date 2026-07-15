import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import concurrent.futures
from datetime import datetime
from pathlib import Path

# Load ANTHROPIC_API_KEY from .env if not already set.
_env_file = Path(__file__).parent / ".env"
if not os.environ.get("ANTHROPIC_API_KEY") and _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if _line.startswith("ANTHROPIC_API_KEY=") and not _line.startswith("#"):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip().strip('"').strip("'")
            break

# WinGet ffmpeg PATH patch — Windows only.
import sys as _sys
if not shutil.which("ffmpeg") and _sys.platform == "win32":
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break

from flask import Flask, jsonify, redirect, request, send_file
from flask_cors import CORS
from flask_sock import Sock

from loader import scan_videos
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips, stitch_clips_to_pipe
from shared import parse_transcript_lines as _parse_transcript_lines, filter_generated_reels as _filter_generated_reels
from captions import build_webvtt, collect_caption_lines, WEBVTT_MIME
import storage
import auth

LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"

_jobs: dict = {}
_jobs_lock = threading.Lock()
_library_lock = threading.Lock()

# ─── Job helpers ──────────────────────────────────────────────────────────────

def _new_job(job_type: str, total: int) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "type": job_type,
            "status": "running",
            "total": total,
            "done": 0,
            "log": [],
            "result": None,
            "error": None,
            "cancel": threading.Event(),
            "_thread": None,
        }
    return job_id


def _append_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["log"].append(message)


# ─── Library helpers ──────────────────────────────────────────────────────────

def _load_library(user_id: str | None = None) -> list:
    return storage.load_library(user_id)


def _save_library(entries: list, user_id: str | None = None) -> None:
    if storage.is_cloud():
        storage.write_json(storage.library_key(user_id), entries)
        return
    with LIBRARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _library_add(entry: dict, user_id: str | None = None) -> None:
    with _library_lock:
        entries = _load_library(user_id)
        entries.insert(0, entry)
        _save_library(entries, user_id)




# A clip shorter than this is imperceptible and produces a title-card->title-card
# artifact with no visible video between. Segments are extended to this floor, or
# dropped (title card included) when the source can't provide it.
MIN_CLIP_SECONDS = 1.5

# Trailing dead-air cap. A segment's raw end is the next line's start, which
# overshoots by any interview pause after the last selected line — leaving the
# speaker sitting silently. We instead estimate when that line's speech ends
# from its word count. Rate is assumed slow and a buffer is added so speech is
# never cut short (biased toward leaving a sliver of air over clipping a word).
SPEAKING_RATE = 2.0    # words/sec (conversational English ~2.5; slower = safer)
TAIL_BUFFER = 1.0      # seconds of grace after the estimated last word


def _group_lines_into_segments(
    all_lines: list, selected_raws: set, video_duration: float | None = None
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Each segment's end is capped near the last selected line's estimated speech
    end (see SPEAKING_RATE / TAIL_BUFFER) to trim trailing dead air, then the
    MIN_CLIP_SECONDS floor is applied — so a lone title card with no clip is
    never emitted, and short segments are extended (clamped to video duration)
    or dropped if the source can't reach the floor.
    """
    def _finalize(start: float, end: float, last_line: dict):
        # Cap trailing dead air before applying the floor. `end` is the next
        # line's start; clip instead at the last selected line's estimated
        # speech end. min() means this can only ever shorten a clip. Falls back
        # to `end` when there's no text to estimate from (fail toward keeping
        # content). ponytail: word-count heuristic — a >18-word line spoken
        # under ~1.8 wps may clip a fraction early; lower SPEAKING_RATE if so.
        words = len(last_line.get("text", "").split())
        if words:
            speech_end = last_line["seconds"] + words / SPEAKING_RATE + TAIL_BUFFER
            end = min(end, speech_end)
        if end - start < MIN_CLIP_SECONDS:
            extended = start + MIN_CLIP_SECONDS
            if video_duration is not None:
                extended = min(extended, video_duration)
            end = extended
        if end - start < MIN_CLIP_SECONDS:
            return None  # can't reach the floor (hit video end) -> drop
        return (start, end)

    segments = []
    current = []

    for line in all_lines:
        if line["raw"] in selected_raws:
            current.append(line)
        else:
            if current:
                seg = _finalize(current[0]["seconds"], line["seconds"], current[-1])
                if seg is not None:
                    segments.append(seg)
                current = []

    if current:
        end = video_duration if video_duration is not None else current[-1]["seconds"] + 10.0
        seg = _finalize(current[0]["seconds"], end, current[-1])
        if seg is not None:
            segments.append(seg)

    return segments


# ─── ffmpeg helpers ───────────────────────────────────────────────────────────

def _find_system_font() -> str | None:
    """Return a path to a TTF font on this system, or None.

    Checks Windows font directories first, then Linux paths installed via
    apt fonts-dejavu-core (present in the project's Dockerfiles).
    """
    candidates = [
        # Windows
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/times.ttf"),
        # Linux — Debian/Ubuntu (fonts-dejavu-core apt package)
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _format_seconds(sec: float) -> str:
    """Format seconds as M:SS for display on title cards."""
    m = int(sec) // 60
    s = int(sec) % 60
    return f"{m}:{s:02d}"


def get_video_dimensions(video_path: str) -> tuple:
    """Return (width, height) of the first video stream. Falls back to 1920x1080."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except Exception as exc:
        print(f"Warning: could not probe dimensions for {video_path}: {exc}",
              file=__import__("sys").stderr)
        return (1920, 1080)


def get_video_duration(video_path: str) -> float | None:
    """Return the video duration in seconds, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def make_title_card(
    lines: list, width: int, height: int, output_path: str, duration: float = 5.0, fade_in_secs: float = 0.0
) -> None:
    """Generate a black title card with white centred text, encoded H.264/AAC.

    Uses textfile= and a relative fontfile= so the ffmpeg filter string contains
    no Windows drive-letter colon.  This ffmpeg build (8.x on Windows) does not
    honour single-quote quoting or \\: escaping inside filter option values, so
    any ‘:’ in the filter string terminates the option value early.  Writing text
    to side-car files and running ffmpeg with cwd=tmp_dir avoids the issue
    entirely.
    """
    fontsize = max(24, height // 15)

    # Reduce font size if the longest line would overflow the frame.
    # Rough estimate: Arial glyph width ≈ 0.55× fontsize.
    max_chars = max(len(line) for line in lines)
    usable_width = width - 80
    while fontsize > 16 and max_chars * fontsize * 0.55 > usable_width:
        fontsize = int(fontsize * 0.9)

    tmp_dir = Path(output_path).parent
    prefix = Path(output_path).stem  # unique per clip, e.g. "clip_0000"

    # ── Font: copy into tmp_dir so we can reference it by filename only ──────
    font_src = _find_system_font()
    if font_src:
        font_name = Path(font_src).name          # e.g. "arial.ttf"
        font_dest = tmp_dir / font_name
        if not font_dest.exists():
            shutil.copy(font_src, font_dest)
        fontfile_arg = f"fontfile={font_name}:"  # relative — no colon in path
    else:
        fontfile_arg = ""

    # ── Text files: write each line to its own file so the filter string ─────
    # ── contains no user content at all (avoids all escaping issues).     ─────
    # drawtext still expands % format specifiers even from textfile, so double
    # any literal percent signs in the text.
    text_filenames = []
    for i, line in enumerate(lines):
        tf = tmp_dir / f"{prefix}_t{i}.txt"
        tf.write_text(line.replace("%", "%%"), encoding="utf-8")
        text_filenames.append(tf.name)  # relative filename only

    # ── Build filter ──────────────────────────────────────────────────────────
    line_height = int(fontsize * 1.2)
    spacing = 8
    n = len(lines)
    total_h = n * line_height + (n - 1) * spacing

    filters = []
    for i, tf_name in enumerate(text_filenames):
        if n == 1:
            # Avoid leading '(' — ffmpeg 8.x parser stops at the first balanced ')' when
            # the value starts with '(', cutting off '/2' and producing a parse error.
            y_expr = "h/2-text_h/2"
        else:
            y_off = i * (line_height + spacing)
            # Precompute at Python time so the value is a plain integer, not an expression
            # starting with '(' (same ffmpeg 8.x parser bug as above).
            y_expr = str((height - total_h) // 2 + y_off)
        filters.append(
            f"drawtext={fontfile_arg}textfile={tf_name}"
            f":fontcolor=white:fontsize={fontsize}:x=w/2-text_w/2:y={y_expr}"
        )

    if fade_in_secs > 0.0:
        filters.append(f"fade=t=in:st=0:d={fade_in_secs}")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", ",".join(filters),
        "-map", "0:v", "-map", "1:a",   # explicit mapping ensures audio is always included
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        "-t", str(duration),
    ]
    if fade_in_secs > 0.0:
        cmd += ["-af", f"afade=t=in:st=0:d={fade_in_secs}"]
    cmd.append(Path(output_path).name)

    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        cwd=str(tmp_dir),  # all relative paths resolve here
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
        result.check_returncode()


# ─── Generation worker ────────────────────────────────────────────────────────

def _build_segment_list(
    video_paths: list,
    selections: dict,
    video_urls: dict | None = None,
) -> list[dict]:
    """Parse transcripts and group selected lines into ordered segments.

    Shared between POST /generate (via _run_generation_impl) and POST /plan
    so segment ordering, timing, and title-card text are computed once.

    Returns a list of dicts ordered as they appear in video_paths:
      video_name, video_stem, ffmpeg_input, start_sec, end_sec, title_lines
    Videos with no transcript or no matching selections are skipped silently.
    """
    grouped = []
    for vp in video_paths:
        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists():
            continue
        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        ffmpeg_input = video_urls.get(vp.name, str(vp)) if video_urls else str(vp)
        duration = get_video_duration(ffmpeg_input)
        selected_set = set(selected_raws)
        segs = _group_lines_into_segments(all_lines, selected_set, video_duration=duration)
        if segs:
            grouped.append((vp, segs, ffmpeg_input, all_lines, selected_set))

    total_segs = sum(len(segs) for _, segs, _, _, _ in grouped)
    result = []
    seg_num = 0
    for vp, segs, ffmpeg_input, all_lines, selected_set in grouped:
        for start_sec, end_sec in segs:
            seg_num += 1
            result.append({
                "video_name": vp.name,
                "video_stem": vp.stem,
                "ffmpeg_input": ffmpeg_input,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "caption_lines": collect_caption_lines(
                    all_lines, selected_set, start_sec, end_sec),
                "title_lines": [
                    vp.stem,
                    f"from {_format_seconds(start_sec)}",
                    f"Segment {seg_num} / {total_segs}",
                ],
            })
    return result


def _run_generation(job_id: str, folder: str,
                    selections: dict, prompt: str, output_filename: str,
                    session_key: str = None,
                    video_paths: list = None,
                    video_urls: dict = None,
                    user_id: str = None) -> None:
    """Run a generation job, guaranteeing it always reaches a terminal state.

    The pipeline runs on a daemon thread whose only wrapper is a `finally` for
    temp-dir cleanup — so any unhandled exception would kill the thread and
    leave the job stuck at 'running'. The progress WebSocket streams a frame
    every 200ms regardless, so a stuck 'running' job strands the UI on the
    'finalizing' screen forever. This guard converts any escape into a terminal
    'error' so the stream always delivers a final 'done' frame.
    """
    try:
        _run_generation_impl(
            job_id, folder, selections, prompt, output_filename,
            session_key=session_key,
            video_paths=video_paths,
            video_urls=video_urls,
            user_id=user_id,
        )
    except Exception as exc:
        job = _jobs.get(job_id)
        if job is not None:
            with _jobs_lock:
                if job.get("status") not in ("done", "error", "cancelled"):
                    job["status"] = "error"
                    job["error"] = f"Generation failed unexpectedly: {exc}"
            try:
                _append_log(job_id, f"✗ Generation failed unexpectedly: {exc}")
            except Exception:
                pass


def _run_generation_impl(job_id: str, folder: str,
                    selections: dict, prompt: str, output_filename: str,
                    session_key: str = None,
                    video_paths: list = None,
                    video_urls: dict = None,
                    user_id: str = None) -> None:
    """Extract and stitch clips from selected transcript lines."""
    job = _jobs[job_id]
    if video_paths is None:
        try:
            video_paths = scan_videos(folder)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = str(exc)
            return
        video_paths = _filter_generated_reels(video_paths)

    # Build segment plan (shared with POST /plan)
    segments = _build_segment_list(video_paths, selections, video_urls)

    # Log per-video results and advance progress counter
    segment_video_names = {s["video_name"] for s in segments}
    for vp in video_paths:
        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return
        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue
        if not vp.with_suffix(".txt").exists():
            _append_log(job_id, f"· {vp.name} — no transcript, skipping")
        elif vp.name not in segment_video_names:
            _append_log(job_id, f"· {vp.name} — selections produced no segments")
        else:
            count = sum(1 for s in segments if s["video_name"] == vp.name)
            _append_log(job_id, f"✓ {vp.name} — {count} segment(s)")
        with _jobs_lock:
            job["done"] += 1

    if not segments:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = "No segments found in selections"
        return

    TITLE_CARD_DURATION = 5.0
    total_segs = len(segments)

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        # ── Phase 1: Plan ────────────────────────────────────────────────
        plan = []      # ordered list of {"type": "title"|"clip", ...}
        item_idx = 0
        _dim_cache: dict = {}

        for seg in segments:
            ffmpeg_input = seg["ffmpeg_input"]
            if ffmpeg_input not in _dim_cache:
                try:
                    _dim_cache[ffmpeg_input] = get_video_dimensions(ffmpeg_input)
                except Exception:
                    _dim_cache[ffmpeg_input] = (1920, 1080)
            width, height = _dim_cache[ffmpeg_input]
            card_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
            item_idx += 1
            clip_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
            item_idx += 1
            plan.append({
                "type": "title",
                "path": card_path,
                "lines": seg["title_lines"],
                "width": width,
                "height": height,
                "ok": False,
                "error": None,
            })
            plan.append({
                "type": "clip",
                "path": clip_path,
                "video_path": ffmpeg_input,
                "start_sec": seg["start_sec"],
                "end_sec": seg["end_sec"],
                "ok": False,
                "error": None,
            })

        # ── Phase 2: Execute ─────────────────────────────────────────────
        # Title cards: serial (fast, ~0.1s each)
        for item in plan:
            if item["type"] != "title":
                continue
            if job["cancel"].is_set():
                item["error"] = "cancelled"
                continue
            try:
                make_title_card(
                    item["lines"], item["width"], item["height"], item["path"],
                    duration=TITLE_CARD_DURATION, fade_in_secs=2.0,
                )
                item["ok"] = True
            except Exception as exc:
                item["error"] = str(exc)
                label = " | ".join(item.get("lines", []))
                _append_log(job_id, f"✗ Title card failed [{label}]: {exc}")

        # Clips: parallel (capped at 1 in cloud mode — concurrent ffmpeg processes
        # each downloading and VP9-decoding large webm files from R2 spike memory
        # and trigger OOM kills on Render).
        max_workers = 1 if storage.is_cloud() else min(4, os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for idx, item in enumerate(plan):
                if item["type"] != "clip":
                    continue
                # Skip if the paired title card failed
                title_item = plan[idx - 1]
                if not title_item["ok"]:
                    item["error"] = "title card failed"
                    continue
                if job["cancel"].is_set():
                    item["error"] = "cancelled"
                    continue
                item["future"] = executor.submit(
                    extract_clip,
                    item["video_path"],
                    item["start_sec"],
                    item["end_sec"],
                    item["path"],
                    2.0,  # fade_out_secs
                )

            for item in plan:
                if item["type"] != "clip" or "future" not in item:
                    continue
                if job["cancel"].is_set():
                    item["future"].cancel()
                    item["error"] = "cancelled"
                    continue
                try:
                    item["future"].result()
                    item["ok"] = True
                except Exception as exc:
                    item["error"] = str(exc)
                    _append_log(
                        job_id,
                        f"✗ {os.path.basename(item['video_path'])}"
                        f" [{item['start_sec']:.1f}-{item['end_sec']:.1f}]"
                        f" extraction failed: {exc}",
                    )

        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return

        # ── Phase 2 summary ──────────────────────────────────────────────
        ok_titles = sum(1 for it in plan if it["type"] == "title" and it["ok"])
        ok_clips  = sum(1 for it in plan if it["type"] == "clip"  and it["ok"])
        fail_titles = sum(1 for it in plan if it["type"] == "title" and not it["ok"])
        fail_clips  = sum(1 for it in plan if it["type"] == "clip"  and not it["ok"])
        _append_log(
            job_id,
            f"· Extraction summary: {ok_titles}/{ok_titles+fail_titles} title cards ok,"
            f" {ok_clips}/{ok_clips+fail_clips} clips ok"
        )
        for it in plan:
            if not it["ok"]:
                kind = it["type"]
                err  = it.get("error", "unknown error")
                _append_log(job_id, f"  ✗ {kind} failed: {err}")

        # ── Phase 3: Assemble ────────────────────────────────────────────
        clip_paths = []
        clip_durations = []
        segment_starts = []
        cumulative_time = 0.0
        title_card_count = 0

        i = 0
        while i < len(plan):
            title_item = plan[i]
            clip_item = plan[i + 1]
            i += 2

            if not title_item["ok"] or not clip_item["ok"]:
                _append_log(
                    job_id,
                    f"  · Skipping segment {title_card_count+1}:"
                    f" title_ok={title_item['ok']} clip_ok={clip_item['ok']}"
                )
                continue   # errors already logged in Phase 2

            segment_starts.append(cumulative_time)  # points to title card start
            clip_paths.append(title_item["path"])
            cumulative_time += TITLE_CARD_DURATION
            title_card_count += 1
            clip_paths.append(clip_item["path"])
            clip_durations.append(clip_item["end_sec"] - clip_item["start_sec"])
            cumulative_time += clip_item["end_sec"] - clip_item["start_sec"]

        _append_log(
            job_id,
            f"· Assembling {title_card_count} segment(s) → {len(clip_paths)} file(s) to stitch"
        )

        if not clip_paths:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = "No clips could be extracted"
            return

        reel_s3_key = f"{session_key}/{output_filename}" if storage.is_cloud() and session_key else None
        reel_download_url = None

        if reel_s3_key:
            # Cloud mode: stream ffmpeg output simultaneously to local file + S3 upload.
            _append_log(job_id, "· Stitching reel and uploading to cloud storage...")
            proc = stitch_clips_to_pipe(clip_paths)

            stderr_buf: list = []

            def _drain_stderr():
                stderr_buf.append(proc.stderr.read())

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            upload_exc = None
            try:
                with open(output_path, "wb") as _local_f:
                    class _TeeReader(io.RawIOBase):
                        def readable(self):
                            return True

                        def readinto(self, b):
                            data = proc.stdout.read(len(b))
                            n = len(data)
                            b[:n] = data
                            if data:
                                _local_f.write(data)
                            return n

                    storage.upload_stream(reel_s3_key, _TeeReader())
            except Exception as exc:
                upload_exc = exc
                _append_log(job_id, f"✗ Streaming upload failed: {exc}")
            finally:
                # Close stdout before wait() — if upload raised mid-stream,
                # ffmpeg may block on a full pipe buffer with nothing reading it.
                try:
                    proc.stdout.close()
                except OSError:
                    pass
                proc.wait()
                stderr_thread.join()
                try:
                    os.unlink(proc._concat_list_path)
                except OSError:
                    pass

            if proc.returncode != 0:
                stderr_text = (stderr_buf[0] if stderr_buf else b"").decode(errors="replace")
                with _jobs_lock:
                    job["status"] = "error"
                    job["error"] = f"Stitch failed: {stderr_text[:300]}"
                return

            if upload_exc is None:
                reel_download_url = storage.presigned_url(reel_s3_key)
                _append_log(job_id, "✓ Reel stitched and uploaded to cloud storage")
            else:
                _append_log(job_id, "· Reel saved locally (cloud upload failed)")
        else:
            # Local mode: write to disk directly.
            _append_log(job_id, "· Stitching reel...")
            try:
                stitch_clips(clip_paths, output_path)
            except Exception as exc:
                with _jobs_lock:
                    job["status"] = "error"
                    job["error"] = f"Stitch failed: {exc}"
                return

    duration = int(sum(clip_durations) + title_card_count * TITLE_CARD_DURATION)

    # In local mode: record the output filename in a sidecar file inside the
    # output folder.  When this folder is later uploaded in cloud mode, the
    # sidecar travels with it and tells the server which files are generated
    # reels so they can be filtered from the source-video list.
    if not storage.is_cloud():
        try:
            marker = Path(folder) / "sizzle_generated_reels.txt"
            existing = set(marker.read_text(encoding="utf-8").splitlines()) if marker.exists() else set()
            existing.add(output_filename)
            marker.write_text("\n".join(sorted(existing)), encoding="utf-8")
        except Exception:
            pass  # sidecar is best-effort; never fail generation over it

    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_durations),
        "duration_seconds": duration,
        "segment_starts": segment_starts,
    }
    if reel_download_url:
        result["download_url"] = reel_download_url

    _append_log(job_id, f"✓ Done — saved to {output_filename}")
    with _jobs_lock:
        job["result"] = result

    library_entry = {
        "id": str(uuid.uuid4()),
        "filename": output_filename,
        "path": output_path,
        "source_folder": (Path(session_key).name if session_key else Path(folder).name) + "/",
        "prompt": prompt,
        "duration_seconds": duration,
        "clip_count": len(clip_durations),
        "segment_starts": segment_starts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if storage.is_cloud() and session_key and reel_download_url:
        # Only record the S3 key when the upload actually succeeded; otherwise
        # the library endpoint would redirect to a non-existent R2 object.
        library_entry["reel_s3_key"] = f"{session_key}/{output_filename}"

    # ── Captions: derive a WebVTT track from the same segments ───────────
    vtt = build_webvtt(segments)
    if vtt:
        stem = Path(output_filename).stem
        if storage.is_cloud() and session_key:
            captions_key = f"{session_key}/{stem}.vtt"
            try:
                # upload_bytes, not upload_file/upload_stream: the reel goes via
                # upload_stream (an invariant the cloud tests guard); captions are
                # a small in-memory text payload, so skip the temp-file round-trip.
                storage.upload_bytes(
                    captions_key, vtt.encode("utf-8"), WEBVTT_MIME)
                library_entry["captions_key"] = captions_key
            except Exception as exc:
                _append_log(job_id, f"· Captions upload skipped: {exc}")
        else:
            try:
                sidecar = Path(output_path).with_suffix(".vtt")
                sidecar.write_text(vtt, encoding="utf-8")
                library_entry["captions_filename"] = sidecar.name
            except Exception as exc:
                _append_log(job_id, f"· Captions sidecar skipped: {exc}")

    _library_add(library_entry, user_id)

    # Schedule cleanup of the cloud session temp dir 1 hour after generation.
    # The dir is kept alive so /video/<job_id> can serve the reel directly
    # without an R2 round-trip.  After the TTL the local file is gone and the
    # endpoint falls back to the presigned R2 URL.
    if storage.is_cloud() and folder.startswith(tempfile.gettempdir()):
        def _deferred_cleanup(path=folder):
            shutil.rmtree(path, ignore_errors=True)
        _cleanup_timer = threading.Timer(3600, _deferred_cleanup)
        _cleanup_timer.daemon = True
        _cleanup_timer.start()

    with _jobs_lock:
        job["result"]["entry_id"] = library_entry["id"]
        job["status"] = "done"


# ─── WebSocket job handler ────────────────────────────────────────────────────

def _job_ws_impl(ws, job_id):
    """Stream job progress over a WebSocket until the job reaches a terminal state."""
    last_log_len = 0
    while True:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                try:
                    ws.send(json.dumps({
                        "type": "done",
                        "status": "error",
                        "error": "job not found",
                        "result": None,
                    }))
                except Exception:
                    pass
                return
            log_snapshot = list(job["log"])
            done         = job["done"]
            total        = job["total"]
            status       = job["status"]
            result       = job.get("result")
            error        = job.get("error")

        try:
            for msg in log_snapshot[last_log_len:]:
                ws.send(json.dumps({"type": "log", "message": msg}))
            last_log_len = len(log_snapshot)
            ws.send(json.dumps({"type": "progress", "done": done, "total": total}))
            if status in ("done", "error", "cancelled"):
                ws.send(json.dumps({
                    "type": "done",
                    "status": status,
                    "result": result,
                    "error": error,
                }))
                return
        except Exception:
            return  # client disconnected

        time.sleep(0.2)


# ─── Flask app ────────────────────────────────────────────────────────────────

def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.config["TESTING"] = testing
    app.before_request(auth.require_auth)

    def _uid():
        from flask import g
        return getattr(g, "user_id", None)

    sock = Sock(app)

    @sock.route("/ws/job/<job_id>")
    def job_ws(ws, job_id):
        from flask import request as _req
        if storage.is_cloud() and not auth.verify_token(_req.args.get("token")):
            try:
                ws.send(json.dumps({"type": "done", "status": "error",
                                    "error": "authentication required", "result": None}))
            except Exception:
                pass
            return
        _job_ws_impl(ws, job_id)

    @app.post("/generate")
    def generate():
        body = request.get_json() or {}
        prompt = body.get("prompt", "").strip()
        mode = body.get("mode", "highlight")
        selections = body.get("selections", {})
        output_filename = body.get("output_filename", "sizzle_reel.mp4").strip()
        output_filename = Path(output_filename).name
        session_key = body.get("session_key", "").strip() or None
        gen_user_id = _uid()

        VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        video_paths_for_gen = None
        video_urls_for_gen = None

        if storage.is_cloud():
            if not session_key:
                return jsonify({"error": "session_key required in cloud mode"}), 400
            tmp_session_dir = tempfile.mkdtemp(prefix="sizzle_gen_")
            _tmp_dir_to_cleanup = None  # intentionally no immediate cleanup

            all_keys = storage.list_keys(session_key + "/")
            selected_filenames = set(selections.keys())

            # Download only transcript files for selected videos
            for key in all_keys:
                p = Path(key)
                if p.suffix.lower() == ".txt":
                    stem = p.stem
                    if any(Path(fn).stem == stem for fn in selected_filenames):
                        storage.download_file(key, os.path.join(tmp_session_dir, p.name))

            # Generate presigned URLs (2hr TTL) for selected video files only
            video_urls_for_gen = {}
            for key in all_keys:
                p = Path(key)
                if p.suffix.lower() in VIDEO_EXTS and p.name in selected_filenames:
                    video_urls_for_gen[p.name] = storage.presigned_url(key, expires=7200)

            # Synthetic Path objects — only .name and .with_suffix(".txt") are used
            video_paths_for_gen = sorted(
                [Path(tmp_session_dir) / fn for fn in video_urls_for_gen],
                key=lambda p: p.name,
            )
            selected_count = len(video_paths_for_gen)
            folder = tmp_session_dir
        else:
            folder = body.get("folder", "").strip()
            if not folder or not Path(folder).exists():
                return jsonify({"error": "Folder not found"}), 404
            _tmp_dir_to_cleanup = None

        try:
            check_ffmpeg()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

        if not storage.is_cloud():
            try:
                video_paths = scan_videos(folder)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 422
            video_paths = _filter_generated_reels(video_paths)
            selected_count = sum(1 for p in video_paths if selections.get(p.name))

        job_id = _new_job("generation", max(selected_count, 1))
        if app.config.get("TESTING"):
            # In test mode run synchronously so the worker finishes (and all
            # mock interactions complete) before the POST response is returned.
            # This prevents a live daemon thread from calling patched symbols
            # during a subsequent test's patch window.
            try:
                _run_generation(
                    job_id, folder, selections, prompt, output_filename,
                    session_key=session_key,
                    video_paths=video_paths_for_gen,
                    video_urls=video_urls_for_gen,
                    user_id=gen_user_id,
                )
            finally:
                if _tmp_dir_to_cleanup:
                    shutil.rmtree(_tmp_dir_to_cleanup, ignore_errors=True)
        else:
            def _run_with_cleanup():
                try:
                    _run_generation(
                        job_id, folder, selections, prompt, output_filename,
                        session_key=session_key,
                        video_paths=video_paths_for_gen,
                        video_urls=video_urls_for_gen,
                        user_id=gen_user_id,
                    )
                finally:
                    if _tmp_dir_to_cleanup:
                        shutil.rmtree(_tmp_dir_to_cleanup, ignore_errors=True)

            t = threading.Thread(target=_run_with_cleanup, daemon=True)
            with _jobs_lock:
                _jobs[job_id]["_thread"] = t
            t.start()
        return jsonify({"job_id": job_id})

    @app.post("/plan")
    def plan():
        """Return the ordered segment plan + presigned URLs for browser-side encoding."""
        if not storage.is_cloud():
            return jsonify({"error": "browser planning is only available in cloud mode"}), 400

        body = request.get_json() or {}
        session_key = (body.get("session_key") or "").strip()
        if not session_key:
            return jsonify({"error": "session_key required"}), 400

        VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        selections = body.get("selections", {})
        output_filename = Path((body.get("output_filename") or "sizzle_reel.mp4").strip()).name

        all_keys = storage.list_keys(session_key + "/")
        selected_filenames = set(selections.keys())

        tmp_dir = tempfile.mkdtemp(prefix="sizzle_plan_")
        try:
            # Download only the .txt transcripts we need (same logic as /generate)
            for key in all_keys:
                p = Path(key)
                if p.suffix.lower() == ".txt":
                    stem = p.stem
                    if any(Path(fn).stem == stem for fn in selected_filenames):
                        storage.download_file(key, os.path.join(tmp_dir, p.name))

            # Generate presigned GET URLs for selected video files (2-hour TTL)
            video_urls: dict = {}
            for key in all_keys:
                p = Path(key)
                if p.suffix.lower() in VIDEO_EXTS and p.name in selected_filenames:
                    video_urls[p.name] = storage.presigned_url(key, expires=7200)

            # Synthetic Path objects pointing to downloaded transcripts
            video_paths = sorted(
                [Path(tmp_dir) / fn for fn in video_urls],
                key=lambda p: p.name,
            )

            segments = _build_segment_list(video_paths, selections, video_urls)
            if not segments:
                return jsonify({"error": "No segments found in selections"}), 422

            captions_vtt = build_webvtt(segments)
            captions_key = None
            captions_put_url = None
            if captions_vtt:
                stem = Path(output_filename).stem
                captions_key = f"{session_key}/{stem}.vtt"
                captions_put_url = storage.presigned_put_url(captions_key, expires=7200)

            # Probe dimensions from the first video's presigned URL (cheap, ~0.1s)
            try:
                width, height = get_video_dimensions(segments[0]["ffmpeg_input"])
            except Exception:
                width, height = 1920, 1080

            # Presigned PUT URL so the browser can upload the finished reel to R2
            reel_key = f"{session_key}/{output_filename}"
            presigned_put = storage.presigned_put_url(reel_key, expires=7200)

            return jsonify({
                "session_key": session_key,
                "output_filename": output_filename,
                "width": width,
                "height": height,
                "reel_key": reel_key,
                "presigned_put_url": presigned_put,
                "captions_vtt": captions_vtt,
                "captions_key": captions_key,
                "captions_put_url": captions_put_url,
                "segments": [
                    {
                        "video": seg["video_name"],
                        "presigned_get_url": seg["ffmpeg_input"],
                        "start_sec": seg["start_sec"],
                        "end_sec": seg["end_sec"],
                        "title_lines": seg["title_lines"],
                    }
                    for seg in segments
                ],
            })
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @app.post("/library")
    def library_add_endpoint():
        """Record a reel that the browser encoded and uploaded directly to R2."""
        body = request.get_json() or {}
        session_key = (body.get("session_key") or "").strip()
        output_filename = (body.get("output_filename") or "").strip()
        if not session_key or not output_filename:
            return jsonify({"error": "session_key and output_filename required"}), 400

        entry = {
            "id": str(uuid.uuid4()),
            "filename": output_filename,
            "path": "",
            "source_folder": Path(session_key).name + "/",
            "prompt": body.get("prompt", ""),
            "duration_seconds": int(body.get("duration_seconds", 0)),
            "clip_count": int(body.get("clip_count", 0)),
            "segment_starts": body.get("segment_starts", []),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reel_s3_key": f"{session_key}/{output_filename}",
        }
        captions_key = (body.get("captions_key") or "").strip()
        if captions_key:
            entry["captions_key"] = captions_key
        _library_add(entry, _uid())
        return jsonify({"id": entry["id"]})

    @app.get("/status/<job_id>")
    def job_status(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        status = job["status"]
        return jsonify({
            "type": job["type"],
            "status": status,
            "total": job["total"],
            "done": job["done"],
            "log": list(job["log"]),
            "result": job["result"],
            "error": job["error"],
        })

    @app.delete("/jobs/<job_id>")
    def cancel_job(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["cancel"].set()
                if job["status"] == "running":
                    job["status"] = "cancelled"
        return jsonify({"ok": True})

    @app.get("/video/<job_id>")
    def serve_video(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error": "not found"}), 404
        result = job["result"]
        # Prefer the local temp file — it exists as long as the container hasn't
        # restarted and is the most reliable path (no R2 round-trip required).
        path = Path(result["path"])
        if path.is_file():
            return send_file(str(path), conditional=True)
        # Fallback: redirect to presigned R2 URL (only available when upload succeeded)
        if storage.is_cloud() and result.get("download_url"):
            return redirect(result["download_url"])
        return jsonify({"error": "file not found on disk"}), 404

    @app.get("/library-video/<entry_id>")
    def serve_library_video(entry_id):
        entries = _load_library(_uid())
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        download = request.args.get("download") == "1"
        # Local file first — works as long as the generator container hasn't restarted.
        path = Path(entry["path"])
        if path.is_file():
            return send_file(
                str(path),
                conditional=True,
                as_attachment=download,
                download_name=entry.get("filename", "reel.mp4"),
            )
        # Fallback: redirect the browser straight to a presigned R2 URL instead of
        # proxying every byte through this host (proxying costs host bandwidth on
        # every view — the dominant ongoing bandwidth drain on a metered plan).
        #
        # The presigned URL forces Content-Type=video/mp4 via a response-override
        # param; that (together with R2 CORS now allowing GET — see set_cors.py)
        # satisfies Chrome's ORB, which is what previously blocked a bare redirect.
        # The browser handles Range/seeking against R2 directly.
        if storage.is_cloud() and entry.get("reel_s3_key"):
            try:
                disposition = "attachment" if download else "inline"
                # Strip chars that could break out of the quoted filename token or
                # inject a header (", \, CR, LF). filename is server-side data, but
                # it flows unescaped into the presigned URL's Content-Disposition.
                safe_filename = re.sub(
                    r'["\\\r\n]', "", entry.get("filename", "reel.mp4")
                ) or "reel.mp4"
                url = storage.presigned_url(
                    entry["reel_s3_key"],
                    content_type="video/mp4",
                    content_disposition=f'{disposition}; filename="{safe_filename}"',
                )
                return redirect(url)
            except Exception as exc:
                return jsonify({"error": f"cloud fetch failed: {exc}"}), 502
        return jsonify({"error": "file not found on disk"}), 404

    @app.get("/library-captions/<entry_id>")
    def serve_library_captions(entry_id):
        entries = _load_library(_uid())
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        # Local sidecar first (works until the container restarts).
        fname = entry.get("captions_filename")
        if fname:
            sidecar = Path(entry["path"]).with_name(fname)
            if sidecar.is_file():
                return app.response_class(
                    sidecar.read_text(encoding="utf-8"), mimetype=WEBVTT_MIME)
        # Cloud: the VTT is tiny text — proxy it directly (unlike metered video).
        key = entry.get("captions_key")
        if key and storage.is_cloud():
            try:
                data = storage.read_file_bytes(key)
                return app.response_class(data, mimetype=WEBVTT_MIME)
            except Exception:
                return jsonify({"error": "captions not found"}), 404
        return jsonify({"error": "no captions"}), 404

    @app.post("/library/<entry_id>/download-captioned")
    def download_captioned(entry_id):
        """Burn the reel's VTT into a downloadable MP4 (local mode only).

        Cloud mode burns in the browser via ReelEncoder.burnCaptions — the Render
        free tier deliberately does not re-encode video server-side.
        """
        if storage.is_cloud():
            return jsonify({"error": "cloud burn-in runs in the browser"}), 400
        entries = _load_library(_uid())
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        reel = Path(entry["path"])
        fname = entry.get("captions_filename")
        vtt = Path(reel).with_name(fname) if fname else None
        if not reel.is_file() or not vtt or not vtt.is_file():
            return jsonify({"error": "reel or captions missing"}), 404

        out_dir = Path(tempfile.mkdtemp(prefix="sizzle_cap_"))
        out_path = out_dir / f"{reel.stem}_captioned.mp4"
        # ffmpeg's subtitles filter needs a POSIX-style path with the colon after
        # the Windows drive letter escaped, quoted inside the filter string.
        vtt_arg = str(vtt).replace("\\", "/").replace(":", "\\:")
        style = "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=1,Shadow=0,BackColour=&H80000000"
        cmd = [
            "ffmpeg", "-y", "-i", str(reel),
            "-vf", f"subtitles='{vtt_arg}':force_style='{style}'",
            "-c:a", "copy", str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not out_path.is_file():
            return jsonify({"error": "burn-in failed"}), 500
        return send_file(
            str(out_path), mimetype="video/mp4",
            as_attachment=True,
            download_name=f"{reel.stem}_captioned.mp4",
        )

    @app.get("/library")
    def get_library():
        entries = _load_library(_uid())
        # Playback is routed through /library-video/<id>, which serves the local
        # file when present and otherwise redirects to a presigned R2 URL (with a
        # forced video/mp4 Content-Type so Chrome's ORB permits the load). Keeping
        # the indirection means the client never needs a presigned URL injected here.
        return jsonify(entries)

    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        delete_file = request.args.get("delete_file") == "true"
        file_path_to_delete = None
        with _library_lock:
            entries = _load_library(_uid())
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None:
                return jsonify({"error": "not found"}), 404
            if delete_file:
                file_path_to_delete = entry.get("path")
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries, _uid())
        if file_path_to_delete:
            try:
                Path(file_path_to_delete).unlink(missing_ok=True)
            except Exception:
                pass  # best-effort; never fail a delete over a missing file
        return jsonify({"ok": True})

    @app.patch("/library/<entry_id>")
    def edit_library_entry(entry_id):
        body = request.get_json() or {}
        with _library_lock:
            entries = _load_library(_uid())
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None:
                return jsonify({"error": "not found"}), 404
            if "title" in body:
                entry["title"] = str(body["title"])
            if "notes" in body:
                entry["notes"] = str(body["notes"])
            _save_library(entries, _uid())
        return jsonify(entry)

    @app.post("/find-local-folder")
    def find_local_folder():
        body = request.get_json() or {}
        probe_name = body.get("probe_name", "").strip()
        probe_content = body.get("probe_content", "").strip()
        if (not probe_name or not probe_content or
                '/' in probe_name or '\\' in probe_name or '..' in probe_name):
            return jsonify({"path": None})

        home = Path.home()
        search_roots = ["Downloads", "Videos", "Documents", "Desktop", "Pictures"]

        for root_name in search_roots:
            root = home / root_name
            if not root.exists():
                continue
            # Collect folders up to depth 2 under this root
            dirs_to_check = [root]
            try:
                for item in root.iterdir():
                    if item.is_dir():
                        dirs_to_check.append(item)
                        try:
                            for subitem in item.iterdir():
                                if subitem.is_dir():
                                    dirs_to_check.append(subitem)
                        except (PermissionError, OSError):
                            pass
            except (PermissionError, OSError):
                pass

            for folder_path in dirs_to_check:
                probe_path = folder_path / probe_name
                try:
                    if probe_path.is_file() and probe_path.read_text(encoding="utf-8").strip() == probe_content:
                        return jsonify({"path": str(folder_path)})
                except (PermissionError, OSError):
                    continue

        return jsonify({"path": None})

    @app.post("/open-folder")
    def open_folder_in_explorer():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        file_path = body.get("file_path", "").strip()

        # In cloud mode (Linux containers) there is no local folder to open;
        # skip silently so the endpoint remains safe to call from all modes.
        if not folder:
            return jsonify({"ok": True})

        try:
            if file_path and Path(file_path).is_file():
                # Highlight the specific file in Explorer (Windows only).
                subprocess.Popen(['explorer', f'/select,{file_path}'])
            elif Path(folder).exists():
                subprocess.Popen(['explorer', folder])
        except Exception:
            pass  # no-op on non-Windows (Linux/macOS) where explorer doesn't exist
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5001)

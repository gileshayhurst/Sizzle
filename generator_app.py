import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
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

# WinGet ffmpeg PATH patch.
if not shutil.which("ffmpeg"):
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from loader import scan_videos
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips
from shared import parse_transcript_lines as _parse_transcript_lines

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

def _load_library() -> list:
    if not LIBRARY_PATH.exists():
        return []
    try:
        with LIBRARY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_library(entries: list) -> None:
    with LIBRARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _library_add(entry: dict) -> None:
    with _library_lock:
        entries = _load_library()
        entries.insert(0, entry)
        _save_library(entries)


def _filter_generated_reels(video_paths: list) -> list:
    """Remove paths recorded as generated reels. Fails open."""
    try:
        library_paths = {Path(e["path"]).resolve() for e in _load_library()}
    except Exception:
        return video_paths
    return [vp for vp in video_paths if vp.resolve() not in library_paths]



def _group_lines_into_segments(
    all_lines: list, selected_raws: set
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges."""
    segments = []
    current = []

    for line in all_lines:
        if line["raw"] in selected_raws:
            current.append(line)
        else:
            if current:
                segments.append((current[0]["seconds"], line["seconds"]))
                current = []

    if current:
        segments.append((current[0]["seconds"], current[-1]["seconds"] + 10.0))

    return segments


# ─── ffmpeg helpers ───────────────────────────────────────────────────────────

def _find_system_font() -> str | None:
    """Return a path to a TTF font on this system, or None."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/times.ttf"),
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


def make_title_card(
    lines: list, width: int, height: int, output_path: str, duration: float = 5.0
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
            y_expr = "(h-text_h)/2"
        else:
            y_off = i * (line_height + spacing)
            y_expr = f"(h-{total_h})/2+{y_off}"
        filters.append(
            f"drawtext={fontfile_arg}textfile={tf_name}"
            f":fontcolor=white:fontsize={fontsize}:x=(w-text_w)/2:y={y_expr}"
        )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-t", str(duration),
            Path(output_path).name,  # relative output too (cwd=tmp_dir)
        ],
        check=False,
        capture_output=True,
        cwd=str(tmp_dir),  # all relative paths resolve here
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
        result.check_returncode()


# ─── Generation worker ────────────────────────────────────────────────────────

def _run_generation(job_id: str, folder: str, mode: str,
                    selections: dict, prompt: str, output_filename: str) -> None:
    """Extract and stitch clips from selected transcript lines."""
    job = _jobs[job_id]
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(exc)
        return
    video_paths = _filter_generated_reels(video_paths)

    video_segments = []

    for vp in video_paths:
        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return

        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue

        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists():
            _append_log(job_id, f"· {vp.name} — no transcript, skipping")
            continue

        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        segs = _group_lines_into_segments(all_lines, set(selected_raws))

        if segs:
            _append_log(job_id, f"✓ {vp.name} — {len(segs)} segment(s)")
            video_segments.append((vp, segs))
        else:
            _append_log(job_id, f"· {vp.name} — selections produced no segments")

        with _jobs_lock:
            job["done"] += 1

    if not video_segments:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = "No segments found in selections"
        return

    TITLE_CARD_DURATION = 5.0
    total_segs = sum(len(segs) for _, segs in video_segments)

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths = []
        clip_durations = []
        segment_starts = []
        cumulative_time = 0.0
        clip_index = 0
        seg_num = 0
        title_card_count = 0

        for vp, segs in video_segments:
            if job["cancel"].is_set():
                with _jobs_lock:
                    job["status"] = "cancelled"
                return

            try:
                width, height = get_video_dimensions(str(vp))
            except Exception:
                width, height = 1920, 1080

            for start_sec, end_sec in segs:
                seg_num += 1

                card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                card_lines = [
                    vp.stem,
                    f"from {_format_seconds(start_sec)}",
                    f"Segment {seg_num} / {total_segs}",
                ]
                # Record the transition card start BEFORE adding the card's duration
                # so navigation seeks to the visible title card, not past it.
                segment_starts.append(cumulative_time)
                card_added = False

                try:
                    make_title_card(card_lines, width, height, card_path)
                    clip_paths.append(card_path)
                    clip_index += 1
                    cumulative_time += TITLE_CARD_DURATION
                    card_added = True
                    title_card_count += 1
                except Exception as exc:
                    # Card failed — remove the stale segment marker and skip this
                    # segment entirely.  Do not attempt clip extraction: without a
                    # title card the segment is incomplete, and cumulative_time was
                    # not advanced so subsequent segment_starts would be offset.
                    segment_starts.pop()
                    _append_log(job_id, f"· Could not create title card for {vp.name}: {exc}")
                    continue

                # Always write extracted clips to .mp4 regardless of source
                # extension.  Non-MP4 containers (e.g. .webm) do not support the
                # H.264/AAC encoding used here and would cause ffmpeg to error.
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    cumulative_time += end_sec - start_sec
                    clip_index += 1
                except Exception as exc:
                    # Clip extraction failed — roll back the title card that was
                    # already appended so the reel does not contain an orphaned card.
                    segment_starts.pop()
                    if card_added:
                        clip_paths.pop()
                        cumulative_time -= TITLE_CARD_DURATION
                    _append_log(
                        job_id,
                        f"✗ {vp.name} [{start_sec:.1f}-{end_sec:.1f}] — extraction failed: {exc}",
                    )

        if not clip_paths:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = "No clips could be extracted"
            return

        _append_log(job_id, "· Stitching reel...")
        try:
            stitch_clips(clip_paths, output_path)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = f"Stitch failed: {exc}"
            return

    duration = int(sum(clip_durations) + title_card_count * TITLE_CARD_DURATION)
    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_durations),
        "duration_seconds": duration,
        "segment_starts": segment_starts,
    }

    _append_log(job_id, f"✓ Done — saved to {output_filename}")
    with _jobs_lock:
        job["result"] = result

    _library_add({
        "id": str(uuid.uuid4()),
        "filename": output_filename,
        "path": output_path,
        "source_folder": Path(folder).name + "/",
        "prompt": prompt,
        "duration_seconds": duration,
        "clip_count": len(clip_durations),
        "segment_starts": segment_starts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })

    with _jobs_lock:
        job["status"] = "done"


# ─── Flask app ────────────────────────────────────────────────────────────────

def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.config["TESTING"] = testing

    @app.post("/generate")
    def generate():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()
        mode = body.get("mode", "highlight")
        selections = body.get("selections", {})
        output_filename = body.get("output_filename", "sizzle_reel.mp4").strip()
        output_filename = Path(output_filename).name

        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404

        try:
            check_ffmpeg()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

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
            _run_generation(job_id, folder, mode, selections, prompt, output_filename)
        else:
            t = threading.Thread(
                target=_run_generation,
                args=(job_id, folder, mode, selections, prompt, output_filename),
                daemon=True,
            )
            with _jobs_lock:
                _jobs[job_id]["_thread"] = t
            t.start()
        return jsonify({"job_id": job_id})

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
                _jobs[job_id]["status"] = "cancelled"
        return jsonify({"ok": True})

    @app.get("/video/<job_id>")
    def serve_video(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error": "not found"}), 404
        path = Path(job["result"]["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)

    @app.get("/library-video/<entry_id>")
    def serve_library_video(entry_id):
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        path = Path(entry["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)

    @app.get("/library")
    def get_library():
        return jsonify(_load_library())

    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        with _library_lock:
            entries = _load_library()
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries)
        return jsonify({"ok": True})

    @app.post("/open-folder")
    def open_folder_in_explorer():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if folder and Path(folder).exists():
            subprocess.Popen(['explorer', folder])
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5001)

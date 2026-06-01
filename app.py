import json
import os
import re as _re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

# Load ANTHROPIC_API_KEY from a .env file in the project root if not already set.
_env_file = Path(__file__).parent / ".env"
if not os.environ.get("ANTHROPIC_API_KEY") and _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if _line.startswith("ANTHROPIC_API_KEY=") and not _line.startswith("#"):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip().strip('"').strip("'")
            break

# WinGet installs ffmpeg to a user-local path that isn't on the subprocess PATH.
# Patch it in at startup so all child processes (ffmpeg, whisper) can find it.
if not shutil.which("ffmpeg"):
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break

from flask import Flask, jsonify, render_template, request, send_file

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_timestamps
from transcriber import transcribe_video
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips

LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"
RECENT_FOLDERS_PATH = Path(__file__).parent / "recent_folders.json"

_jobs: dict = {}
_jobs_lock = threading.Lock()
_library_lock = threading.Lock()
_recent_folders_lock = threading.Lock()
_whisper_model = None
_model_lock = threading.Lock()


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        with _model_lock:
            if _whisper_model is None:
                import whisper as _whisper
                _whisper_model = _whisper.load_model("base")
    return _whisper_model


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
        }
    return job_id


def _append_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["log"].append(message)


def _pick_directory() -> str | None:
    """Open a native OS folder dialog. Returns the selected path or None."""
    import tkinter as tk
    from tkinter import filedialog
    result: dict = {"path": None}

    def run() -> None:
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        result["path"] = filedialog.askdirectory(parent=root) or None
        root.destroy()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return result["path"]


_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+\w+:\s*(.*)')


def _parse_transcript_lines(raw_text: str) -> list[dict]:
    lines = []
    for raw in raw_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        ts, text = m.group(1), m.group(2)
        seconds = parse_timestamp_to_seconds(ts)
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "text": text,
            "seconds": seconds,
            "minute_bucket": int(seconds) // 60,
        })
    return lines


def _group_by_minute(lines: list[dict]) -> list[dict]:
    buckets: dict[int, list] = {}
    for line in lines:
        b = line["minute_bucket"]
        buckets.setdefault(b, []).append(line)
    result = []
    for b in sorted(buckets):
        result.append({
            "bucket": b,
            "label": f"{b}:00 – {b + 1}:00",
            "lines": buckets[b],
        })
    return result


def _group_lines_into_segments(
    all_lines: list[dict], selected_raws: set[str]
) -> list[tuple[float, float]]:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Lines are grouped into segments: any unselected line between two selected
    lines ends the current segment and starts a new one.

    End time = seconds of the first line AFTER the segment (the next line in the
    full transcript, whether selected or not). If the segment runs to the end of
    the transcript, end = last_line.seconds + 10.
    """
    segments: list[tuple[float, float]] = []
    current: list[dict] = []

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


def _filter_generated_reels(video_paths: list[Path]) -> list[Path]:
    """Remove paths that are recorded as generated reels in the library.

    Prevents previously generated sizzle reels saved in the source folder
    from being re-discovered as source videos on subsequent folder opens.
    Fails open: if the library cannot be read, all paths are returned unchanged.
    """
    try:
        library_paths = {Path(entry["path"]).resolve() for entry in _load_library()}
    except Exception:
        return video_paths
    return [vp for vp in video_paths if vp.resolve() not in library_paths]


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


def _load_recent_folders() -> list:
    if not RECENT_FOLDERS_PATH.exists():
        return []
    try:
        with RECENT_FOLDERS_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_recent_folder(folder: str, video_count: int) -> None:
    """Prepend folder to recent_folders.json, deduplicate by path, keep max 5."""
    with _recent_folders_lock:
        entries = [e for e in _load_recent_folders() if e.get("path") != folder]
        entries.insert(0, {
            "path": folder,
            "video_count": video_count,
            "last_opened": datetime.now().isoformat(timespec="seconds"),
        })
        entries = entries[:5]
        try:
            with RECENT_FOLDERS_PATH.open("w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
        except OSError:
            pass  # history is best-effort; never fail a load-folder for this


def _find_system_font() -> str | None:
    """Return a path to a TTF font on this system, or None if none found.

    On Windows the drawtext filter crashes when fontconfig has no config file.
    Specifying an explicit fontfile bypasses fontconfig entirely.
    """
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


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Return (width, height) of the first video stream. Falls back to 1920×1080."""
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
        )
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except Exception as exc:
        print(f"Warning: could not probe dimensions for {video_path}: {exc}",
              file=__import__("sys").stderr)
        return (1920, 1080)


def make_title_card(
    name: str, width: int, height: int, output_path: str, duration: float = 5.0
) -> None:
    """Generate a black title card with white centred text, encoded H.264/AAC."""
    import textwrap

    fontsize = max(24, height // 15)

    # Wrap to ~85% of frame width; Arial avg char width ≈ 0.6 × fontsize.
    chars_per_line = max(10, int(width * 0.85 / (fontsize * 0.6)))
    lines = textwrap.wrap(name, chars_per_line) or [name]

    def _escape(s: str) -> str:
        return (
            s.replace("\\", "\\\\")
             .replace("'", "\\'")
             .replace(":", "\\:")
             .replace("%", "%%")
        )

    # Prefix fontfile= to bypass fontconfig (crashes on Windows without a config file)
    font = _find_system_font()
    if font:
        escaped_font = font.replace("\\", "/").replace(":", "\\:")
        fontfile_arg = f"fontfile='{escaped_font}':"
    else:
        fontfile_arg = ""

    # Use one drawtext filter per line so each line is individually x-centred.
    # ffmpeg's \n escape in text= is unreliable; stacking filters is more robust.
    line_height = int(fontsize * 1.2)
    spacing = 8
    n = len(lines)
    total_h = n * line_height + (n - 1) * spacing

    filters = []
    for i, line in enumerate(lines):
        if n == 1:
            y_expr = "(h-text_h)/2"
        else:
            y_off = i * (line_height + spacing)
            y_expr = f"(h-{total_h})/2+{y_off}"
        filters.append(
            f"drawtext={fontfile_arg}text='{_escape(line)}':fontcolor=white"
            f":fontsize={fontsize}:x=(w-text_w)/2:y={y_expr}"
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
            output_path,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
        result.check_returncode()


def _run_generation(job_id: str, folder: str, mode: str,
                    selections: dict, prompt: str, output_filename: str) -> None:
    """Extract and stitch clips from selected transcript lines.

    No Claude call — clip ranges come from _group_lines_into_segments().
    Segment title cards ("Segment N") are inserted between non-contiguous
    selected clusters within a single video. Video-name title cards are
    inserted between different source videos (existing behaviour).
    """
    job = _jobs[job_id]
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(exc)
        return
    video_paths = _filter_generated_reels(video_paths)

    video_segments: list[tuple] = []

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

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        clip_durations: list[float] = []
        clip_index = 0
        seg_num = 1
        prev_vp = None

        for vp, segs in video_segments:
            # Video-name title card between different source videos
            if prev_vp is not None:
                card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                try:
                    width, height = get_video_dimensions(str(vp))
                    make_title_card(vp.stem, width, height, card_path)
                    clip_paths.append(card_path)
                    clip_index += 1
                except Exception as exc:
                    _append_log(job_id, f"· Could not create title card for {vp.name}: {exc}")
            prev_vp = vp

            for seg_idx, (start_sec, end_sec) in enumerate(segs):
                # Segment title card between non-contiguous segments in same video
                if seg_idx > 0:
                    card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                    try:
                        width, height = get_video_dimensions(str(vp))
                        make_title_card(f"Segment {seg_num}", width, height, card_path)
                        clip_paths.append(card_path)
                        clip_index += 1
                        seg_num += 1
                    except Exception as exc:
                        _append_log(job_id, f"· Could not create segment {seg_num} card: {exc}")

                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    clip_index += 1
                except Exception as exc:
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

    duration = int(sum(clip_durations))

    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_durations),
        "duration_seconds": duration,
    }

    _append_log(job_id, f"✓ Done — saved to {output_filename}")
    with _jobs_lock:
        job["status"] = "done"
        job["result"] = result

    _library_add({
        "id": str(uuid.uuid4()),
        "filename": output_filename,
        "path": output_path,
        "source_folder": Path(folder).name + "/",
        "prompt": prompt,
        "duration_seconds": duration,
        "clip_count": len(clip_durations),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })


def _run_analyze(folder: str, prompt: str) -> dict:
    """Call Claude on every transcript in folder. Returns per-video matched raw lines."""
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        return {"error": str(exc)}
    video_paths = _filter_generated_reels(video_paths)

    highlights: dict[str, list[str]] = {}
    errors: list[str] = []

    for vp in video_paths:
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists() or txt_path.stat().st_size == 0:
            highlights[vp.name] = []
            continue

        transcript = txt_path.read_text(encoding="utf-8")
        all_lines = _parse_transcript_lines(transcript)

        try:
            response = query_claude(transcript, prompt)
            ranges = parse_timestamps(response) or []
        except Exception as exc:
            errors.append(f"{vp.name}: {exc}")
            highlights[vp.name] = []
            continue

        matched: list[str] = []
        for seg in ranges:
            start_str, end_str = seg.split("-", 1)
            start_sec = parse_timestamp_to_seconds(start_str)
            end_sec = parse_timestamp_to_seconds(end_str)
            for line in all_lines:
                if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                    if line["raw"] not in matched:
                        matched.append(line["raw"])

        highlights[vp.name] = matched

    if len(errors) == len(video_paths) and not any(highlights.values()):
        return {"error": "; ".join(errors)}

    return {"highlights": highlights}


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/browse")
    def browse():
        path = _pick_directory()
        if path is None:
            return jsonify({"path": None})
        return jsonify({"path": path})

    @app.get("/recent-folders")
    def recent_folders():
        return jsonify(_load_recent_folders())

    @app.post("/load-folder")
    def load_folder():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422

        video_paths = _filter_generated_reels(video_paths)
        if not video_paths:
            return jsonify({"error": "No source video files found (folder contains only previously generated reels)"}), 422

        _save_recent_folder(folder, len(video_paths))
        filenames = [p.name for p in video_paths]
        needs_transcription = [p for p in video_paths
                                if not p.with_suffix(".txt").exists()
                                or p.with_suffix(".txt").stat().st_size == 0]

        if not needs_transcription:
            return jsonify({"job_id": None, "files": filenames, "folder": folder})

        job_id = _new_job("transcription", len(needs_transcription))

        def _transcribe():
            model = _get_whisper_model()
            for i, vp in enumerate(needs_transcription):
                with _jobs_lock:
                    cancel_event = _jobs[job_id]["cancel"]
                if cancel_event.is_set():
                    with _jobs_lock:
                        _jobs[job_id]["status"] = "cancelled"
                    return
                _append_log(job_id, f"⟳ {vp.name} — transcribing...")
                try:
                    transcript = transcribe_video(str(vp), model=model)
                    vp.with_suffix(".txt").write_text(transcript, encoding="utf-8")
                    _append_log(job_id, f"✓ {vp.name} — done")
                except Exception as exc:
                    _append_log(job_id, f"✗ {vp.name} — failed: {exc}")
                    with _jobs_lock:
                        _jobs[job_id]["error"] = f"{vp.name}: {exc}"
                with _jobs_lock:
                    _jobs[job_id]["done"] = i + 1
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = {"folder": folder, "files": filenames}

        threading.Thread(target=_transcribe, daemon=True).start()
        return jsonify({"job_id": job_id, "files": filenames, "folder": folder})

    @app.get("/status/<job_id>")
    def job_status(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "type": job["type"],
            "status": job["status"],
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

    @app.get("/transcripts")
    def get_transcripts():
        folder = request.args.get("folder", "").strip()
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422
        video_paths = _filter_generated_reels(video_paths)
        files = []
        for vp in video_paths:
            txt_path = vp.with_suffix(".txt")
            if not txt_path.exists():
                lines = []
            else:
                lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
            files.append({"name": vp.name, "lines": lines})
        return jsonify({"files": files})

    @app.post("/analyze")
    def analyze():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        result = _run_analyze(folder, prompt)
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result)

    @app.post("/generate")
    def generate():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()   # optional — stored for library only
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
        threading.Thread(
            target=_run_generation,
            args=(job_id, folder, mode, selections, prompt, output_filename),
            daemon=True,
        ).start()
        return jsonify({"job_id": job_id})

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
            import subprocess
            subprocess.Popen(['explorer', folder])
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)

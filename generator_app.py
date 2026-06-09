import json
import os
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
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips
from shared import parse_transcript_lines as _parse_transcript_lines
import storage

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
    if storage.is_cloud():
        return storage.read_json(storage.library_key())
    if not LIBRARY_PATH.exists():
        return []
    try:
        with LIBRARY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_library(entries: list) -> None:
    if storage.is_cloud():
        storage.write_json(storage.library_key(), entries)
        return
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
            "-map", "0:v", "-map", "1:a",   # explicit mapping ensures audio is always included
            "-c:v", "libx264", "-preset", "ultrafast",
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
                    selections: dict, prompt: str, output_filename: str,
                    session_key: str = None) -> None:
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
        # ── Phase 1: Plan ────────────────────────────────────────────────
        plan = []      # ordered list of {"type": "title"|"clip", ...}
        item_idx = 0
        seg_num = 0

        for vp, segs in video_segments:
            try:
                width, height = get_video_dimensions(str(vp))
            except Exception:
                width, height = 1920, 1080
            for start_sec, end_sec in segs:
                seg_num += 1
                card_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
                item_idx += 1
                clip_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
                item_idx += 1
                plan.append({
                    "type": "title",
                    "path": card_path,
                    "lines": [
                        vp.stem,
                        f"from {_format_seconds(start_sec)}",
                        f"Segment {seg_num} / {total_segs}",
                    ],
                    "width": width,
                    "height": height,
                    "ok": False,
                    "error": None,
                })
                plan.append({
                    "type": "clip",
                    "path": clip_path,
                    "video_path": str(vp),
                    "start_sec": start_sec,
                    "end_sec": end_sec,
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
                    item["lines"], item["width"], item["height"], item["path"]
                )
                item["ok"] = True
            except Exception as exc:
                item["error"] = str(exc)
                _append_log(job_id, f"· Could not create title card: {exc}")

        # Clips: parallel
        max_workers = min(4, os.cpu_count() or 4)
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
                continue   # errors already logged in Phase 2

            segment_starts.append(cumulative_time)  # points to title card start
            clip_paths.append(title_item["path"])
            cumulative_time += TITLE_CARD_DURATION
            title_card_count += 1
            clip_paths.append(clip_item["path"])
            clip_durations.append(clip_item["end_sec"] - clip_item["start_sec"])
            cumulative_time += clip_item["end_sec"] - clip_item["start_sec"]

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

    # In cloud mode: upload the finished reel to S3 and add a presigned download URL.
    reel_download_url = None
    if storage.is_cloud() and session_key:
        reel_s3_key = f"{session_key}/{output_filename}"
        _append_log(job_id, f"⟳ Uploading reel to cloud storage…")
        try:
            storage.upload_file(output_path, reel_s3_key)
            reel_download_url = storage.presigned_url(reel_s3_key)
            _append_log(job_id, f"✓ Reel uploaded to cloud storage")
        except Exception as exc:
            _append_log(job_id, f"✗ Could not upload reel to cloud storage: {exc}")

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
    _library_add(library_entry)

    with _jobs_lock:
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

    sock = Sock(app)

    @sock.route("/ws/job/<job_id>")
    def job_ws(ws, job_id):
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

        if storage.is_cloud():
            if not session_key:
                return jsonify({"error": "session_key required in cloud mode"}), 400
            # Download all session files from S3 into a local temp dir for ffmpeg.
            # Do NOT set _tmp_dir_to_cleanup in cloud mode — we keep the temp dir
            # alive so /video/<job_id> and /library-video/<id> can serve the
            # generated reel directly without relying on R2 (which may have failed
            # to upload). The Render container restart cleans /tmp periodically.
            tmp_session_dir = tempfile.mkdtemp(prefix="sizzle_gen_")
            _tmp_dir_to_cleanup = None  # intentionally no immediate cleanup
            for key in storage.list_keys(session_key + "/"):
                filename = Path(key).name
                storage.download_file(key, os.path.join(tmp_session_dir, filename))
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
                _run_generation(job_id, folder, mode, selections, prompt, output_filename, session_key=session_key)
            finally:
                if _tmp_dir_to_cleanup:
                    shutil.rmtree(_tmp_dir_to_cleanup, ignore_errors=True)
        else:
            def _run_with_cleanup():
                try:
                    _run_generation(job_id, folder, mode, selections, prompt, output_filename, session_key)
                finally:
                    if _tmp_dir_to_cleanup:
                        shutil.rmtree(_tmp_dir_to_cleanup, ignore_errors=True)

            t = threading.Thread(target=_run_with_cleanup, daemon=True)
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
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        # Local file first — works as long as the generator container hasn't restarted.
        path = Path(entry["path"])
        if path.is_file():
            return send_file(str(path), conditional=True)
        # Fallback: stream from R2 *through* Flask so the response carries the
        # CORS headers that flask-cors adds.  A redirect to a presigned URL would
        # send the browser directly to R2 (no CORS headers) and Chrome would
        # block the response with ERR_BLOCKED_BY_ORB.
        if storage.is_cloud() and entry.get("reel_s3_key"):
            try:
                import io as _io
                data = storage.read_file_bytes(entry["reel_s3_key"])
                return send_file(
                    _io.BytesIO(data),
                    mimetype="video/mp4",
                    conditional=True,
                    download_name=entry.get("filename", "reel.mp4"),
                )
            except Exception as exc:
                return jsonify({"error": f"cloud fetch failed: {exc}"}), 502
        return jsonify({"error": "file not found on disk"}), 404

    @app.get("/library")
    def get_library():
        entries = _load_library()
        # Note: we deliberately do NOT inject presigned R2 URLs here.
        # Chrome's ORB (Opaque Response Blocking) rejects cross-origin media
        # responses that don't pass through a CORS-aware server.  All video
        # playback is routed through /library-video/<id> which proxies R2
        # content via Flask (flask-cors adds the required headers).
        return jsonify(entries)

    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        delete_file = request.args.get("delete_file") == "true"
        file_path_to_delete = None
        with _library_lock:
            entries = _load_library()
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None:
                return jsonify({"error": "not found"}), 404
            if delete_file:
                file_path_to_delete = entry.get("path")
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries)
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
            entries = _load_library()
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None:
                return jsonify({"error": "not found"}), 404
            if "title" in body:
                entry["title"] = str(body["title"])
            if "notes" in body:
                entry["notes"] = str(body["notes"])
            _save_library(entries)
        return jsonify(entry)

    @app.post("/open-folder")
    def open_folder_in_explorer():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if folder and Path(folder).exists():
            subprocess.Popen(['explorer', folder])
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5001)

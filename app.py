import json
import os
import shutil
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
# Guard to Windows only — Linux containers find ffmpeg via the system PATH (apt install).
import sys as _sys
if not shutil.which("ffmpeg") and _sys.platform == "win32":
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break

from flask import Flask, jsonify, render_template, request, send_file

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_timestamps
from transcriber import transcribe_video
from video_editor import parse_timestamp_to_seconds
from shared import parse_transcript_lines as _parse_transcript_lines
import storage

RECENT_FOLDERS_PATH = Path(__file__).parent / "recent_folders.json"
PROMPT_HISTORY_PATH = Path(__file__).parent / "prompt_history.json"

# Maps session_key → local temp dir for the duration of the process
_cloud_session_dirs: dict[str, str] = {}
_cloud_session_lock = threading.Lock()

_jobs: dict = {}
_jobs_lock = threading.Lock()
_recent_folders_lock = threading.Lock()
_prompt_history_lock = threading.Lock()
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



def _filter_generated_reels(video_paths: list[Path]) -> list[Path]:
    """Remove paths that are recorded as generated reels in the library.

    Prevents previously generated sizzle reels saved in the source folder
    from being re-discovered as source videos on subsequent folder opens.
    Fails open: if the library cannot be read, all paths are returned unchanged.

    In cloud mode, matches by filename only — full paths differ between sessions
    so path-based matching would never filter anything.
    """
    try:
        library = _load_library()
        if storage.is_cloud():
            library_filenames = {
                entry.get("filename") or Path(entry["path"]).name
                for entry in library
            }
            return [vp for vp in video_paths if vp.name not in library_filenames]
        library_paths = {Path(entry["path"]).resolve() for entry in library}
    except Exception:
        return video_paths
    return [vp for vp in video_paths if vp.resolve() not in library_paths]


def _load_library() -> list:
    if storage.is_cloud():
        return storage.read_json(storage.library_key())
    library_path = Path(__file__).parent / "sizzle_library.json"
    if not library_path.exists():
        return []
    try:
        with library_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


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



def _load_prompt_history() -> dict:
    if not PROMPT_HISTORY_PATH.exists():
        return {"recent": [], "templates": []}
    try:
        with PROMPT_HISTORY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"recent": [], "templates": []}


def _save_prompt_history(data: dict) -> None:
    try:
        with PROMPT_HISTORY_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _prompt_history_use(text: str) -> None:
    with _prompt_history_lock:
        data = _load_prompt_history()
        recent = [t for t in data.get("recent", []) if t != text]
        recent.insert(0, text)
        data["recent"] = recent[:10]
        _save_prompt_history(data)


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


def _ensure_cloud_session(session_key: str) -> str:
    """Download session files from S3 into a local temp dir if not already cached.

    Returns the local temp dir path. Thread-safe.
    """
    with _cloud_session_lock:
        if session_key in _cloud_session_dirs:
            return _cloud_session_dirs[session_key]
        tmp = tempfile.mkdtemp(prefix="sizzle_session_")
        _cloud_session_dirs[session_key] = tmp

    # Download outside the lock — this can take time
    for key in storage.list_keys(session_key + "/"):
        filename = Path(key).name
        storage.download_file(key, os.path.join(tmp, filename))

    return tmp


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            app_mode=os.environ.get("APP_MODE", "local"),
            generator_url=os.environ.get("GENERATOR_URL", "http://localhost:5001"),
        )

    _VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    _ALLOWED_UPLOAD_EXTENSIONS = _VIDEO_EXTENSIONS | {".txt"}

    @app.post("/upload")
    def upload():
        """Cloud-mode endpoint: receive uploaded video and transcript files as a session.

        Accepts video files (.mp4, .mov, .avi, .mkv, .webm) and pre-made transcript
        files (.txt). When a .txt file is uploaded alongside a video, transcription is
        skipped for that video — the app uses the supplied transcript directly.
        At least one video file must be included.
        """
        files = request.files.getlist("files")
        if not files or all(f.filename == "" for f in files):
            return jsonify({"error": "No files provided"}), 400

        # Validate all files before writing any
        has_video = False
        for f in files:
            ext = Path(f.filename).suffix.lower()
            if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
                return jsonify({"error": f"Unsupported file type: {f.filename}. Upload videos (.mp4 .mov .avi .mkv .webm) and/or transcripts (.txt)."}), 400
            if ext in _VIDEO_EXTENSIONS:
                has_video = True
        if not has_video:
            return jsonify({"error": "At least one video file is required."}), 400

        session_key = storage.new_session_key()

        # Determine local session directory
        if storage.is_cloud():
            session_dir = Path(tempfile.mkdtemp(prefix="sizzle_"))
        else:
            session_dir = storage._data_root() / session_key
            session_dir.mkdir(parents=True, exist_ok=True)

        saved_names = []
        for f in files:
            filename = Path(f.filename).name  # strip any path components
            dest = session_dir / filename
            f.save(str(dest))
            if storage.is_cloud():
                storage.upload_file(str(dest), f"{session_key}/{filename}")
            saved_names.append(filename)

        # In cloud mode, files are now in S3; clean up the local temp dir
        if storage.is_cloud():
            shutil.rmtree(str(session_dir), ignore_errors=True)
            # session_dir is gone; return S3 key as folder indicator
            folder_indicator = session_key
        else:
            folder_indicator = str(session_dir)

        return jsonify({
            "session_key": session_key,
            "folder": folder_indicator,
            "files": saved_names,
        })

    @app.post("/upload/prepare")
    def upload_prepare():
        """Cloud-mode: validate filenames and create an upload session.

        The browser calls this first to get a session_key, then uploads each
        file via POST /upload/file (server proxies bytes to R2 — no CORS needed),
        then calls /upload/commit.

        Request JSON: {"files": ["video1.mp4", "transcript1.txt", ...]}
        Response JSON: {"session_key": "sessions/<uuid>", "folder": "sessions/<uuid>"}
        """
        if not storage.is_cloud():
            return jsonify({"error": "This endpoint is only available in cloud mode"}), 400

        body = request.get_json(silent=True) or {}
        filenames = body.get("files", [])
        if not filenames:
            return jsonify({"error": "No files provided"}), 400

        has_video = False
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
                return jsonify({"error": f"Unsupported file type: {name}. Upload videos (.mp4 .mov .avi .mkv .webm) and/or transcripts (.txt)."}), 400
            if ext in _VIDEO_EXTENSIONS:
                has_video = True
        if not has_video:
            return jsonify({"error": "At least one video file is required."}), 400

        session_key = storage.new_session_key()
        return jsonify({
            "session_key": session_key,
            "folder": session_key,
        })

    @app.post("/upload/file")
    def upload_file_proxy():
        """Cloud-mode: accept one file from the browser and stream it to R2.

        The browser posts files here (same origin — no CORS required) and this
        server streams the bytes directly to R2 via boto3.

        Request: multipart/form-data
          - file:        the file bytes
          - session_key: the session key from /upload/prepare
        Response JSON: {"key": "sessions/<uuid>/filename", "filename": "filename"}
        """
        if not storage.is_cloud():
            return jsonify({"error": "This endpoint is only available in cloud mode"}), 400

        f = request.files.get("file")
        session_key = request.form.get("session_key", "").strip()
        if not f:
            return jsonify({"error": "No file provided"}), 400
        if not session_key:
            return jsonify({"error": "session_key is required"}), 400

        safe_name = Path(f.filename).name  # strip any path components
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify({"error": f"Unsupported file type: {safe_name}"}), 400

        key = f"{session_key}/{safe_name}"
        # upload_fileobj streams in multipart chunks — memory-efficient for large videos
        storage._s3().upload_fileobj(f.stream, storage._bucket(), key)
        return jsonify({"key": key, "filename": safe_name})

    @app.post("/upload/commit")
    def upload_commit():
        """Cloud-mode: acknowledge that the browser finished uploading to R2.

        Called after all presigned PUT uploads complete. Server just validates
        the request and echoes back the session info — no file I/O needed here
        since files are already in R2.

        Request JSON: {"session_key": "sessions/<uuid>", "files": ["video1.mp4", ...]}
        Response JSON: {"session_key": "sessions/<uuid>", "folder": "sessions/<uuid>", "files": [...]}
        """
        if not storage.is_cloud():
            return jsonify({"error": "This endpoint is only available in cloud mode"}), 400

        body = request.get_json(silent=True) or {}
        session_key = body.get("session_key")
        if not session_key:
            return jsonify({"error": "session_key is required"}), 400

        files = body.get("files", [])
        return jsonify({
            "session_key": session_key,
            "folder": session_key,
            "files": files,
        })

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
        if storage.is_cloud() and folder and not Path(folder).exists():
            folder = _ensure_cloud_session(folder)
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422

        video_paths = _filter_generated_reels(video_paths)
        if not video_paths:
            return jsonify({"error": "No source video files found (folder contains only previously generated reels)"}), 422

        # In cloud mode Whisper is not available — only videos with pre-supplied
        # .txt transcripts can be used.
        if storage.is_cloud():
            # If the session was uploaded from a local folder that had previously
            # generated reels, a sidecar file lists their filenames so we can
            # filter them out even though they also have .txt transcripts.
            locally_generated: set[str] = set()
            sidecar = Path(folder) / "sizzle_generated_reels.txt"
            if sidecar.exists():
                try:
                    locally_generated = set(sidecar.read_text(encoding="utf-8").splitlines())
                except Exception:
                    pass

            video_paths = [p for p in video_paths
                           if p.name not in locally_generated
                           and p.with_suffix(".txt").exists()
                           and p.with_suffix(".txt").stat().st_size > 0]
            if not video_paths:
                return jsonify({"error": "No transcripts found. In cloud mode, upload a .txt transcript alongside each video."}), 422

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
                    if storage.is_cloud():
                        # Upload transcript to S3 so the generator service can access it
                        for sk, td in _cloud_session_dirs.items():
                            if td == str(vp.parent):
                                storage.upload_file(
                                    str(vp.with_suffix(".txt")),
                                    f"{sk}/{vp.stem}.txt"
                                )
                                break
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
        if storage.is_cloud() and folder and not Path(folder).exists():
            folder = _ensure_cloud_session(folder)
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
        if storage.is_cloud() and folder and not Path(folder).exists():
            folder = _ensure_cloud_session(folder)
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        result = _run_analyze(folder, prompt)
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result)

    @app.get("/prompt-history")
    def get_prompt_history():
        with _prompt_history_lock:
            return jsonify(_load_prompt_history())

    @app.post("/prompt-history")
    def post_prompt_history():
        body = request.get_json() or {}
        action = body.get("action", "")
        text = body.get("text", "").strip()
        name = body.get("name", "").strip()
        if action == "use":
            if text:
                _prompt_history_use(text)
        elif action == "save_template":
            if name and text:
                with _prompt_history_lock:
                    data = _load_prompt_history()
                    templates = data.get("templates", [])
                    templates = [t for t in templates if t["name"] != name]
                    templates.append({"name": name, "text": text})
                    data["templates"] = templates
                    _save_prompt_history(data)
        elif action == "delete_template":
            if name:
                with _prompt_history_lock:
                    data = _load_prompt_history()
                    data["templates"] = [t for t in data.get("templates", []) if t["name"] != name]
                    _save_prompt_history(data)
        else:
            return jsonify({"error": "unknown action"}), 400
        return jsonify({"ok": True})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)

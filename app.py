import concurrent.futures
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
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_scored_timestamps
from transcriber import transcribe_video
from video_editor import parse_timestamp_to_seconds
from shared import (
    read_transcript as _read_transcript,
    parse_transcript_lines as _parse_transcript_lines,
    filter_generated_reels as _filter_generated_reels,
    group_lines_into_segments as _group_lines_into_segments,
)
import storage

RECENT_FOLDERS_PATH = Path(__file__).parent / "recent_folders.json"
PROMPT_HISTORY_PATH = Path(__file__).parent / "prompt_history.json"

# Maps session_key → local temp dir for the duration of the process
_cloud_session_dirs: dict[str, str] = {}
_cloud_session_ready: dict[str, threading.Event] = {}
_cloud_session_lock = threading.Lock()

_jobs: dict = {}
_jobs_lock = threading.Lock()
_recent_folders_lock = threading.Lock()
_prompt_history_lock = threading.Lock()
_whisper_models: dict = {}
_model_lock = threading.Lock()
_WHISPER_CACHE_DIR = os.environ.get(
    "WHISPER_CACHE_DIR", str(Path(__file__).parent / ".whisper_cache")
)


def _compute_transcription_parallelism(cpu_count: int, num_videos: int) -> tuple[int, int]:
    """Return (workers, cpu_threads) for parallel transcription.

    - workers: how many videos to transcribe concurrently. Capped at the video
      count and at half the cores, leaving room for each job's internal threads.
    - cpu_threads: CTranslate2 threads per job, dividing cores evenly across workers.

    Invariant: workers * cpu_threads <= cpu_count.
    """
    cpu_count = max(1, cpu_count)
    num_videos = max(1, num_videos)
    workers = min(num_videos, max(1, cpu_count // 2))
    cpu_threads = max(1, cpu_count // workers)
    return workers, cpu_threads


def _get_whisper_model(cpu_threads: int = 0, num_workers: int = 1):
    """Return a cached faster-whisper base model configured for the given thread layout.

    Cached by (cpu_threads, num_workers) so warm jobs reuse the model. compute_type
    int8 gives the CPU speedup; download_root pins weights so cold boots don't re-fetch.
    """
    key = (cpu_threads, num_workers)
    model = _whisper_models.get(key)
    if model is None:
        with _model_lock:
            model = _whisper_models.get(key)
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(
                    "base",
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=cpu_threads,
                    num_workers=num_workers,
                    download_root=_WHISPER_CACHE_DIR,
                )
                _whisper_models[key] = model
    return model


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
    """Call Claude on every transcript in folder. Returns per-video scored
    segments plus a legacy `highlights` union of the matched lines."""
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        return {"error": str(exc)}
    video_paths = _filter_generated_reels(video_paths)

    def _analyze_one(vp: Path) -> tuple[str, list[dict], str | None]:
        """Analyze a single video. Returns (name, segments, error).

        Runs the (slow) Claude call plus timestamp matching for one video so the
        whole folder can be processed concurrently — a folder of many long videos
        analyzed serially takes long enough for the hosting proxy to time out and
        return an HTML error page the frontend can't parse as JSON.
        """
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists() or txt_path.stat().st_size == 0:
            return vp.name, [], None

        transcript = _read_transcript(txt_path)
        all_lines = _parse_transcript_lines(transcript)

        try:
            response = query_claude(transcript, prompt)
            scored = parse_scored_timestamps(response) or []
        except Exception as exc:
            return vp.name, [], f"{vp.name}: {exc}"

        segments: list[dict] = []
        for seg, score in scored:
            start_str, end_str = seg.split("-", 1)
            start_sec = parse_timestamp_to_seconds(start_str)
            end_sec = parse_timestamp_to_seconds(end_str)
            seg_line_dicts = [
                line for line in all_lines
                if not line.get("is_interviewer")  # analyze never auto-selects the interviewer
                and start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5
            ]
            lines = list(dict.fromkeys(d["raw"] for d in seg_line_dicts))
            if not lines:
                continue  # segment mapped to no respondent lines — drop it
            # The create-screen length estimate must match the clip the generator
            # will actually cut. Claude's end timestamp is the *start* of the last
            # line; the real clip plays that line to its spoken end, so estimate
            # the duration with the same grouping/tail logic the generator uses
            # (shared.group_lines_into_segments) rather than end_sec - start_sec.
            grouped = _group_lines_into_segments(seg_line_dicts, set(lines))
            if grouped:
                clip_start = grouped[0][0]
                clip_dur = sum(e - s for s, e in grouped)
            else:
                clip_start, clip_dur = start_sec, max(0.0, end_sec - start_sec)
            segments.append({
                "start": start_str,
                "end": end_str,
                "start_seconds": clip_start,
                "end_seconds": clip_start + clip_dur,
                "duration_seconds": clip_dur,
                "score": score,
                "lines": lines,
            })

        # Reconcile the per-candidate estimates against what the generator would
        # actually cut. Above, each candidate is grouped in ISOLATION, but
        # generator_app groups the UNION of every selected line across the file
        # (one pass over all_lines). Candidates that sit next to each other with
        # no unselected line between them therefore merge into a single run in
        # the generator and get truncated by the MAX_CLIP_SECONDS ceiling — so
        # the naive sum of candidate durations systematically over-promises.
        # Scale the candidates down by the ratio of merged to solo total, which
        # makes a full selection exact and any prefix far closer.
        if segments:
            union_raws = {raw for seg in segments for raw in seg["lines"]}
            merged = _group_lines_into_segments(all_lines, union_raws)
            total_merged = sum(e - s for s, e in merged)
            total_solo = sum(seg["duration_seconds"] for seg in segments)
            if total_solo > total_merged > 0:
                scale = total_merged / total_solo
                for seg in segments:
                    seg["duration_seconds"] *= scale
                    seg["end_seconds"] = seg["start_seconds"] + seg["duration_seconds"]

        segments.sort(key=lambda s: s["start_seconds"])
        return vp.name, segments, None

    segments_by_file: dict[str, list[dict]] = {}
    highlights: dict[str, list[str]] = {}
    errors: list[str] = []

    # Run the per-video Claude calls concurrently. Wall time collapses from the
    # sum of every call to roughly the slowest single call, keeping the request
    # under the hosting proxy's timeout.
    max_workers = min(8, len(video_paths)) or 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_analyze_one, video_paths))

    for name, segments, error in results:
        segments_by_file[name] = segments
        # Legacy union: preserves the existing `highlights` contract for any
        # caller/test that still reads it.
        union: list[str] = []
        for seg in segments:
            for raw in seg["lines"]:
                if raw not in union:
                    union.append(raw)
        highlights[name] = union
        if error:
            errors.append(error)

    if len(errors) == len(video_paths) and not any(highlights.values()):
        return {"error": "; ".join(errors)}

    return {"segments": segments_by_file, "highlights": highlights}


class SessionDownloadCancelled(Exception):
    """A cloud session download was cancelled via its job's cancel event."""


def _ensure_cloud_session(session_key: str, job_id: str | None = None,
                          cancel_event: threading.Event | None = None) -> str:
    """Download session files from S3 into a local temp dir if not already cached.

    Thread-safe: concurrent callers for the same session_key block until the
    first caller finishes downloading (rather than getting a half-populated dir).

    When job_id/cancel_event are supplied (the async /load-folder path), progress
    is reported to the job between files, and the download aborts with
    SessionDownloadCancelled when the event is set. On cancel the cache entries
    are removed BEFORE waiters are released, so a retry re-downloads cleanly and
    any waiter wakes to a missing entry and raises SessionDownloadCancelled too.
    """
    with _cloud_session_lock:
        if session_key in _cloud_session_dirs:
            event = _cloud_session_ready[session_key]
            is_new = False
        else:
            tmp = tempfile.mkdtemp(prefix="sizzle_session_")
            _cloud_session_dirs[session_key] = tmp
            event = threading.Event()
            _cloud_session_ready[session_key] = event
            is_new = True

    if not is_new:
        event.wait()          # block until the first caller finishes
        with _cloud_session_lock:
            cached = _cloud_session_dirs.get(session_key)
        if cached is None:    # first caller was cancelled and cleaned up
            raise SessionDownloadCancelled(session_key)
        return cached

    tmp = _cloud_session_dirs[session_key]
    try:
        # The main app only ever reads .txt sidecars (scan_videos merely enumerates
        # filenames; analyze/transcripts read transcripts). Downloading the video
        # bytes would pile hundreds of MB per session into Render's /tmp and blow the
        # 2GB ephemeral-disk limit. So download only transcripts; give each video a
        # 0-byte placeholder so scan_videos still lists it.
        keys = storage.list_keys(session_key + "/")
        if job_id is not None:
            txt_total = sum(1 for k in keys if Path(k).suffix.lower() == ".txt")
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id]["total"] = txt_total
        done = 0
        for key in keys:
            if cancel_event is not None and cancel_event.is_set():
                raise SessionDownloadCancelled(session_key)
            filename = Path(key).name
            dest = os.path.join(tmp, filename)
            if Path(filename).suffix.lower() == ".txt":
                storage.download_file(key, dest)
                done += 1
                if job_id is not None:
                    with _jobs_lock:
                        if job_id in _jobs:
                            _jobs[job_id]["done"] = done
            else:
                Path(dest).touch()
    except SessionDownloadCancelled:
        # Remove the cache entries BEFORE the finally releases waiters, so
        # waiters see the missing entry (= cancelled) rather than a
        # half-populated dir, and a retry re-downloads.
        with _cloud_session_lock:
            _cloud_session_dirs.pop(session_key, None)
            _cloud_session_ready.pop(session_key, None)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        event.set()           # release waiters even if download failed
    return tmp


def _scan_load_folder(folder: str) -> tuple[dict | None, str | None]:
    """Scan `folder` and apply every load-folder filter.

    Shared by the synchronous /load-folder path and the cloud session_download
    job thread. Returns (result, error): exactly one is non-None. result is
    {"folder", "files", "needs_transcription"} where needs_transcription is a
    list of video Paths lacking a non-empty .txt transcript.
    """
    try:
        video_paths = scan_videos(folder)
    except ValueError as e:
        return None, str(e)

    video_paths = _filter_generated_reels(video_paths)
    if not video_paths:
        return None, "No source video files found (folder contains only previously generated reels)"

    # Check the sidecar for reels generated into this specific folder.
    # In cloud mode this catches reels that were generated locally and then
    # re-uploaded; in local mode it catches reels not yet in the library
    # (e.g. library cleared) or downloaded from a different session.
    locally_generated: set[str] = set()
    sidecar = Path(folder) / "sizzle_generated_reels.txt"
    if sidecar.exists():
        try:
            locally_generated = set(sidecar.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass
    if locally_generated:
        video_paths = [p for p in video_paths if p.name not in locally_generated]
        if not video_paths:
            return None, "No source video files found (folder contains only previously generated reels)"

    # In cloud mode Whisper is not available — only videos with pre-supplied
    # .txt transcripts can be used.
    if storage.is_cloud():
        video_paths = [p for p in video_paths
                       if p.with_suffix(".txt").exists()
                       and p.with_suffix(".txt").stat().st_size > 0]
        if not video_paths:
            return None, "No transcripts found. In cloud mode, upload a .txt transcript alongside each video."

    _save_recent_folder(folder, len(video_paths))
    filenames = [p.name for p in video_paths]
    needs_transcription = [p for p in video_paths
                           if not p.with_suffix(".txt").exists()
                           or p.with_suffix(".txt").stat().st_size == 0]
    return {"folder": folder, "files": filenames,
            "needs_transcription": needs_transcription}, None


def _valid_session_folder(folder: str) -> bool:
    """Cloud mode: a client-supplied folder must be an upload-session key under
    `sessions/`. Rejecting anything else stops a real server path (e.g. /etc)
    from being passed in to read arbitrary local files. No-op in local mode,
    which is a trusted single-user desktop app.
    """
    if not storage.is_cloud():
        return True
    return bool(folder) and folder.startswith("sessions/")


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing

    # ponytail: in-memory limiter storage — per-instance, resets on restart.
    # Fine on Render's single free-tier instance; move to Redis if it scales out.
    # Keyed by client IP; enabled only in cloud (local desktop app is unmetered).
    limiter = Limiter(key_func=get_remote_address, app=app,
                      default_limits=["600 per hour"])
    app.config["RATELIMIT_ENABLED"] = storage.is_cloud()
    # Cap request bodies the host buffers. Large video bytes go browser->R2 via
    # presigned PUT and never hit this host (see /upload/prepare).
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

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
    @limiter.limit("30 per minute")
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
    @limiter.limit("30 per minute")
    def upload_prepare():
        """Cloud-mode: validate filenames and create an upload session.

        The browser calls this first to get a session_key plus one presigned PUT
        URL per file, then uploads each file DIRECTLY to R2 (browser → R2, the
        host never sees the bytes), then calls /upload/commit.

        Uploading straight to R2 avoids routing large video bytes through this
        host — the old /upload/file proxy hit the host's request body-size limit
        (surfacing as "unexpected end of JSON input" in the browser) and doubled
        the host's metered bandwidth per file.

        Request JSON: {"files": ["video1.mp4", "transcript1.txt", ...]}
        Response JSON: {"session_key": "sessions/<uuid>", "folder": "sessions/<uuid>",
                        "uploads": {"video1.mp4": "<presigned PUT url>", ...}}
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
        uploads = {
            name: storage.presigned_put_url(f"{session_key}/{Path(name).name}", expires=7200)
            for name in filenames
        }
        return jsonify({
            "session_key": session_key,
            "folder": session_key,
            "uploads": uploads,
        })

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
        if not _valid_session_folder(folder):
            return jsonify({"error": "forbidden"}), 403
        if storage.is_cloud() and folder and not Path(folder).exists():
            session_key = folder
            with _cloud_session_lock:
                ready = _cloud_session_ready.get(session_key)
                cached = (ready is not None and ready.is_set()
                          and session_key in _cloud_session_dirs)
            if not cached:
                # Download runs as a cancellable background job; the frontend
                # polls /status/<job_id> and cancels via DELETE /jobs/<job_id>.
                job_id = _new_job("session_download", 0)

                def _download():
                    cancel_event = _jobs[job_id]["cancel"]
                    try:
                        local_dir = _ensure_cloud_session(
                            session_key, job_id=job_id, cancel_event=cancel_event)
                    except SessionDownloadCancelled:
                        with _jobs_lock:
                            if job_id in _jobs and _jobs[job_id]["status"] == "running":
                                _jobs[job_id]["status"] = "cancelled"
                        return
                    except Exception as exc:
                        with _jobs_lock:
                            if job_id in _jobs:
                                _jobs[job_id]["status"] = "error"
                                _jobs[job_id]["error"] = str(exc)
                        return
                    result, error = _scan_load_folder(local_dir)
                    with _jobs_lock:
                        if job_id not in _jobs:
                            return
                        if error:
                            _jobs[job_id]["status"] = "error"
                            _jobs[job_id]["error"] = error
                        else:
                            _jobs[job_id]["status"] = "done"
                            _jobs[job_id]["result"] = {
                                "folder": result["folder"],
                                "files": result["files"],
                            }

                threading.Thread(target=_download, daemon=True).start()
                return jsonify({"job_id": job_id, "job_type": "session_download"})
            folder = _ensure_cloud_session(session_key)
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404

        result, error = _scan_load_folder(folder)
        if error:
            return jsonify({"error": error}), 422

        filenames = result["files"]
        needs_transcription = result["needs_transcription"]

        if not needs_transcription:
            return jsonify({"job_id": None, "files": filenames, "folder": folder})

        job_id = _new_job("transcription", len(needs_transcription))

        def _transcribe():
            cancel_event = _jobs[job_id]["cancel"]
            cpu_count = os.cpu_count() or 1
            workers, cpu_threads = _compute_transcription_parallelism(
                cpu_count, len(needs_transcription)
            )
            model = _get_whisper_model(cpu_threads, workers)
            _append_log(
                job_id,
                f"⟳ transcribing {len(needs_transcription)} video(s) "
                f"({workers} at a time)...",
            )

            def _do_one(vp):
                transcript = transcribe_video(str(vp), model=model)
                # A cancel may have fired while this video was transcribing; skip
                # the write/upload so no transcript appears after status=cancelled.
                if cancel_event.is_set():
                    return
                vp.with_suffix(".txt").write_text(transcript, encoding="utf-8")
                if storage.is_cloud():
                    for sk, td in _cloud_session_dirs.items():
                        if td == str(vp.parent):
                            storage.upload_file(
                                str(vp.with_suffix(".txt")),
                                f"{sk}/{vp.stem}.txt",
                            )
                            break

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            futures = {executor.submit(_do_one, vp): vp for vp in needs_transcription}
            pending = set(futures)
            done_count = 0
            try:
                while pending:
                    if cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        with _jobs_lock:
                            _jobs[job_id]["status"] = "cancelled"
                        _append_log(job_id, "✗ transcription cancelled")
                        return
                    just_done, pending = concurrent.futures.wait(
                        pending,
                        timeout=0.5,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in just_done:
                        vp = futures[future]
                        try:
                            future.result()
                            _append_log(job_id, f"✓ {vp.name} — done")
                        except Exception as exc:
                            _append_log(job_id, f"✗ {vp.name} — failed: {exc}")
                            with _jobs_lock:
                                _jobs[job_id]["error"] = f"{vp.name}: {exc}"
                        done_count += 1
                        with _jobs_lock:
                            _jobs[job_id]["done"] = done_count
            finally:
                executor.shutdown(wait=False)
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
                if job["status"] == "running":
                    job["status"] = "cancelled"
        return jsonify({"ok": True})

    @app.get("/transcripts")
    def get_transcripts():
        folder = request.args.get("folder", "").strip()
        if not _valid_session_folder(folder):
            return jsonify({"error": "forbidden"}), 403
        if storage.is_cloud() and folder and not Path(folder).exists():
            folder = _ensure_cloud_session(folder)
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422
        video_paths = _filter_generated_reels(video_paths)
        sidecar = Path(folder) / "sizzle_generated_reels.txt"
        if sidecar.exists():
            try:
                locally_generated = set(sidecar.read_text(encoding="utf-8").splitlines())
                video_paths = [p for p in video_paths if p.name not in locally_generated]
            except Exception:
                pass
        files = []
        for vp in video_paths:
            txt_path = vp.with_suffix(".txt")
            if not txt_path.exists():
                lines = []
            else:
                lines = _parse_transcript_lines(_read_transcript(txt_path))
            files.append({"name": vp.name, "lines": lines})
        return jsonify({"files": files})

    @app.post("/analyze")
    @limiter.limit("10 per minute;100 per hour")
    def analyze():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        if not _valid_session_folder(folder):
            return jsonify({"error": "forbidden"}), 403
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

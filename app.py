import json
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_timestamps
from transcriber import transcribe_video
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips

LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"

_jobs: dict = {}
_jobs_lock = threading.Lock()
_library_lock = threading.Lock()
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

    @app.post("/load-folder")
    def load_folder():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422

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
                if _jobs[job_id]["cancel"].is_set():
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
                    _jobs[job_id]["done"] = i + 1
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = {"folder": folder, "files": filenames}

        threading.Thread(target=_transcribe, daemon=True).start()
        return jsonify({"job_id": job_id, "files": filenames, "folder": folder})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)

"""Encoder HTTP service -- stateless, two endpoints, no database.

POST /encode/words  {transcript, words}         primary path: the browser did the ASR
POST /encode        multipart transcript+audio  fallback: we do the ASR

Both funnel into encoder.core.encode, so the alignment algorithm has one
implementation regardless of who produced the word stream.
"""
import os
import tempfile

from flask import Flask, jsonify, request
from flask_cors import CORS

from .asr.local import DEFAULT_MODEL_SIZE, words
from .core import encode

# The fallback path receives mono 16 kHz audio, ~0.7 MB for a 4-minute
# interview. 64 MB leaves generous headroom for a long one while still
# refusing a video upload, which this service must never receive.
MAX_CONTENT_LENGTH = 64 * 1024 * 1024


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _validate_words(payload):
    """Return an error string, or None when the word stream is usable."""
    if not isinstance(payload, list):
        return "words must be a list"
    for entry in payload:
        if not isinstance(entry, dict) or not {"w", "s", "e"} <= entry.keys():
            return "each word must be an object with w, s and e"
    return None


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    CORS(app, origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","))

    model_size = os.environ.get("ENCODER_MODEL_SIZE", DEFAULT_MODEL_SIZE)

    # The audio fallback is the ONLY thing in this service that loads a Whisper
    # model. /encode/words needs no model at all, so a memory-constrained
    # deployment (Render free tier, 512 MB) can serve the primary path safely
    # and refuse the fallback rather than being OOM-killed by it -- an OOM takes
    # the whole container down, interrupting other requests, where a 503 fails
    # just the one caller. Clients that get the 503 keep their plain transcript,
    # which still produces a working reel.
    audio_fallback = _enabled("ENCODER_AUDIO_FALLBACK", True)

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "audio_fallback": audio_fallback})

    @app.post("/encode/words")
    def encode_words():
        payload = request.get_json(silent=True) or {}
        transcript = payload.get("transcript")
        if not transcript or not isinstance(transcript, str):
            return jsonify({"error": "transcript is required"}), 400
        error = _validate_words(payload.get("words"))
        if error:
            return jsonify({"error": error}), 400
        return jsonify(encode(transcript, payload["words"]))

    @app.post("/encode")
    def encode_audio():
        # Checked before touching the upload so a disabled deployment never
        # allocates the model, and never buffers the audio either.
        if not audio_fallback:
            return jsonify({
                "error": "audio fallback is disabled on this deployment; "
                         "use POST /encode/words with browser-side word timings",
            }), 503

        transcript = request.form.get("transcript")
        if not transcript:
            return jsonify({"error": "transcript is required"}), 400
        upload = request.files.get("audio")
        if upload is None:
            return jsonify({"error": "audio is required"}), 400

        suffix = os.path.splitext(upload.filename or "")[1] or ".wav"
        handle, path = tempfile.mkstemp(suffix=suffix)
        os.close(handle)
        try:
            upload.save(path)
            stream = words(path, size=model_size)
        finally:
            os.unlink(path)
        return jsonify(encode(transcript, stream))

    return app


if __name__ == "__main__":
    create_app().run(port=5002, debug=True)

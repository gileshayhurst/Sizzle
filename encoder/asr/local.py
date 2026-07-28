"""faster-whisper word timings -- the fallback path's ASR, and the CLI's.

faster-whisper decodes the source file itself (PyAV), so a 53 MB .webm can be
passed straight in; this package needs no ffmpeg. The model is only an ANCHOR
source, so `tiny` is a legitimate choice when speed matters more than word
match -- it anchors as many sentences as `base`.
"""
import threading

_model = None
_model_size = None
_lock = threading.Lock()

DEFAULT_MODEL_SIZE = "base"


def get_model(size: str = DEFAULT_MODEL_SIZE):
    """Load the Whisper model once, lazily.

    Double-checked lock, mirroring app._get_whisper_model, so importing this
    module costs nothing and the service's primary path never pays the RAM.
    """
    global _model, _model_size
    if _model is not None and _model_size == size:
        return _model
    with _lock:
        if _model is None or _model_size != size:
            from faster_whisper import WhisperModel
            _model = WhisperModel(size, device="cpu", compute_type="int8")
            _model_size = size
    return _model


def words(source, model=None, size: str = DEFAULT_MODEL_SIZE) -> list[dict]:
    """Transcribe `source` and return a flat word stream.

    Returns [{"w": str, "s": float, "e": float}] -- the interface the core
    consumes, identical to what the browser path produces.
    """
    model = model or get_model(size)
    segments, _info = model.transcribe(str(source), word_timestamps=True)
    return [
        {"w": word.word, "s": round(word.start, 3), "e": round(word.end, 3)}
        for segment in segments
        for word in (segment.words or [])
    ]

import whisper


def _seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def transcribe_video(video_path: str, model=None) -> str:
    if model is None:
        model = whisper.load_model("base")
    result = model.transcribe(video_path)
    lines = []
    for segment in result["segments"]:
        ts = _seconds_to_timestamp(segment["start"])
        text = segment["text"].strip()
        lines.append(f"[{ts}] Speaker: {text}")
    return "\n".join(lines)

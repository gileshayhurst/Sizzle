import re as _re

import storage as _storage
from pathlib import Path as _Path
from video_editor import parse_timestamp_to_seconds

_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+(\w[\w ]*?):\s*(.*)')

# Speaker labels that identify the AI interview agent (case-insensitive,
# whitespace-normalized). Anything NOT in this set is treated as the
# respondent, so detection fails safe toward keeping content.
INTERVIEWER_LABELS = {
    "interviewer", "ai", "ai agent", "ai interviewer",
    "agent", "moderator", "bot", "assistant", "host",
}


def is_interviewer_label(speaker: str) -> bool:
    """True if the speaker label denotes the AI interviewer/agent."""
    normalized = " ".join(speaker.split()).lower()
    return normalized in INTERVIEWER_LABELS


def parse_transcript_lines(raw_text: str) -> list[dict]:
    """Parse a Whisper transcript into structured line dicts.

    Each dict has keys: raw, timestamp, text, seconds, minute_bucket.
    Lines that do not match the [M:SS] Speaker: text format are silently skipped.
    """
    lines = []
    for raw in raw_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        ts, speaker, text = m.group(1), m.group(2).strip(), m.group(3)
        seconds = parse_timestamp_to_seconds(ts)
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "speaker": speaker,
            "is_interviewer": is_interviewer_label(speaker),
            "text": text,
            "seconds": seconds,
            "minute_bucket": int(seconds) // 60,
        })
    return lines


def filter_generated_reels(video_paths: list, library: list = None) -> list:
    """Remove video paths recorded as generated reels. Fails open.

    In cloud mode, matches by filename only — full paths differ between
    sessions so path-based matching would never filter anything.
    Pass library explicitly in tests to avoid live storage reads.
    """
    try:
        if library is None:
            library = _storage.load_library()
        if _storage.is_cloud():
            library_filenames = {
                e.get("filename") or _Path(e["path"]).name
                for e in library
            }
            return [vp for vp in video_paths if vp.name not in library_filenames]
        library_paths = {_Path(e["path"]).resolve() for e in library}
    except Exception:
        return video_paths
    return [vp for vp in video_paths if vp.resolve() not in library_paths]

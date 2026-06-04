import re as _re

from video_editor import parse_timestamp_to_seconds

_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+\w+:\s*(.*)')


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

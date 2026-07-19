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


# A clip shorter than this is imperceptible. Segments are extended to this floor,
# or dropped when the source can't provide it.
MIN_CLIP_SECONDS = 1.5

# Trailing dead-air cap. A segment's raw end is the next line's start, which
# overshoots by any interview pause after the last selected line. We instead
# estimate when that line's speech ends from its word count. Rate is assumed slow
# and a buffer is added so speech is never cut short (biased toward leaving a
# sliver of air over clipping a word).
SPEAKING_RATE = 2.0    # words/sec (conversational English ~2.5; slower = safer)
TAIL_BUFFER = 1.0      # seconds of grace after the estimated last word


def group_lines_into_segments(
    all_lines: list, selected_raws: set, video_duration: float | None = None
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Each segment's end is capped near the last selected line's estimated speech
    end (see SPEAKING_RATE / TAIL_BUFFER) to trim trailing dead air, then the
    MIN_CLIP_SECONDS floor is applied — so a lone title card with no clip is
    never emitted, and short segments are extended (clamped to video duration)
    or dropped if the source can't reach the floor.

    Pure logic shared by the generator (real clip ranges) and the main app's
    analyze (create-screen length estimate), so both compute identical durations.
    """
    def _finalize(start: float, end: float, last_line: dict):
        # Cap trailing dead air before applying the floor. `end` is the next
        # line's start; clip instead at the last selected line's estimated
        # speech end. min() means this can only ever shorten a clip. Falls back
        # to `end` when there's no text to estimate from (fail toward keeping
        # content).
        words = len(last_line.get("text", "").split())
        if words:
            speech_end = last_line["seconds"] + words / SPEAKING_RATE + TAIL_BUFFER
            end = min(end, speech_end)
        if end - start < MIN_CLIP_SECONDS:
            extended = start + MIN_CLIP_SECONDS
            if video_duration is not None:
                extended = min(extended, video_duration)
            end = extended
        if end - start < MIN_CLIP_SECONDS:
            return None  # can't reach the floor (hit video end) -> drop
        return (start, end)

    segments = []
    current = []

    for line in all_lines:
        if line["raw"] in selected_raws:
            current.append(line)
        else:
            if current:
                seg = _finalize(current[0]["seconds"], line["seconds"], current[-1])
                if seg is not None:
                    segments.append(seg)
                current = []

    if current:
        end = video_duration if video_duration is not None else current[-1]["seconds"] + 10.0
        seg = _finalize(current[0]["seconds"], end, current[-1])
        if seg is not None:
            segments.append(seg)

    return segments


def filter_generated_reels(video_paths: list, library: list = None) -> list:
    """Remove video paths recorded as generated reels. Fails open.

    In cloud mode, matches by filename only — full paths differ between
    sessions so path-based matching would never filter anything.

    In local mode, matches by resolved path for locally-generated reels and
    also by filename for cloud-generated entries (identified by reel_s3_key) —
    those have a Render /tmp path that never matches the user's local path.

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
        # Cloud-generated reels (reel_s3_key present) have a Render /tmp path
        # in the library — match them by filename so downloaded copies are
        # filtered out even when the path doesn't match.
        cloud_reel_names = {
            e.get("filename") or _Path(e["path"]).name
            for e in library
            if e.get("reel_s3_key")
        }
    except Exception:
        return video_paths
    return [vp for vp in video_paths
            if vp.resolve() not in library_paths and vp.name not in cloud_reel_names]

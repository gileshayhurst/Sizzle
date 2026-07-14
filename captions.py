"""WebVTT caption generation for reels.

Pure, framework-free: turns the selected transcript lines of a reel's segments
into a WebVTT string, re-timed onto the reel's [title 5s][clip]... timeline.
"""

WEBVTT_MIME = "text/vtt"

# Kept in sync with generator_app.TITLE_CARD_DURATION; passed explicitly so this
# module never imports the Flask app.
_DEFAULT_TITLE_CARD_DURATION = 5.0


def collect_caption_lines(all_lines, selected_raws, seg_start, seg_end):
    """Selected respondent lines whose source time falls in [seg_start, seg_end).

    Interviewer lines are excluded (captions show the respondent only, per spec),
    even if a user manually selected one.
    """
    return [
        {"text": line["text"], "seconds": line["seconds"]}
        for line in all_lines
        if line["raw"] in selected_raws
        and not line.get("is_interviewer")
        and seg_start <= line["seconds"] < seg_end
    ]


def _fmt_ts(sec: float) -> str:
    """Seconds -> WebVTT 'HH:MM:SS.mmm'."""
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def build_webvtt(segments, title_card_duration: float = _DEFAULT_TITLE_CARD_DURATION):
    """Build a WebVTT string from ordered reel segments, or None if no cues.

    Each segment dict needs: start_sec, end_sec (source clip range) and
    caption_lines (list of {"text", "seconds"} from collect_caption_lines).

    ponytail: assumes every segment is encoded (naive cumulative timeline). If a
    segment's extraction fails and assembly drops the pair, cues after it drift.
    Failure-aware re-timing is deliberately not built — dropped segments are rare
    and already produce a degraded reel. Upgrade path: pass the assembler's
    surviving segment_starts + clip_durations instead of recomputing here.
    """
    cues = []
    reel_t = 0.0
    for seg in segments:
        clip_dur = seg["end_sec"] - seg["start_sec"]
        clip_start = reel_t + title_card_duration
        clip_end = clip_start + clip_dur
        lines = seg.get("caption_lines", [])
        for i, line in enumerate(lines):
            cue_start = clip_start + (line["seconds"] - seg["start_sec"])
            if i + 1 < len(lines):
                cue_end = clip_start + (lines[i + 1]["seconds"] - seg["start_sec"])
            else:
                cue_end = clip_end
            cue_start = max(clip_start, min(cue_start, clip_end))
            cue_end = max(cue_start, min(cue_end, clip_end))
            text = (line["text"] or "").strip()
            if not text or cue_end <= cue_start:
                continue
            cues.append((cue_start, cue_end, text))
        reel_t = clip_end

    if not cues:
        return None
    body = "\n\n".join(f"{_fmt_ts(a)} --> {_fmt_ts(b)}\n{t}" for a, b, t in cues)
    return "WEBVTT\n\n" + body + "\n"

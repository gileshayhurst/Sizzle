"""WebVTT caption generation for reels.

Pure, framework-free: turns the selected transcript lines of a reel's segments
into a WebVTT string, re-timed onto the reel's [title 5s][clip]... timeline.
"""

WEBVTT_MIME = "text/vtt"

# Kept in sync with generator_app.TITLE_CARD_DURATION; passed explicitly so this
# module never imports the Flask app.
_DEFAULT_TITLE_CARD_DURATION = 5.0

# Broadcast-style caption sizing: at most two lines of LINE_MAX_CHARS on screen,
# so a long utterance becomes a sequence of short cues instead of one wall of
# text. MAX_CUE_SEC caps a single cue so a trailing clip gap can't leave the last
# caption lingering.
LINE_MAX_CHARS = 42
MAX_CUE_SEC = 6.0


def _wrap_lines(words, max_chars):
    """Greedy word-wrap into lines of <= max_chars (a word longer than max_chars
    becomes its own over-long line rather than being split mid-word)."""
    lines, cur, cur_len = [], [], 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and cur_len + add > max_chars:
            lines.append(" ".join(cur))
            cur, cur_len = [], 0
            add = len(w)
        cur.append(w)
        cur_len += add
    if cur:
        lines.append(" ".join(cur))
    return lines


def _chunk_line(text):
    """Split one transcript line into ordered cue strings, each <= two lines of
    <= LINE_MAX_CHARS joined by '\\n'. Empty/whitespace input -> []."""
    words = (text or "").split()
    if not words:
        return []
    lines = _wrap_lines(words, LINE_MAX_CHARS)
    return ["\n".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]


def _vlen(cue):
    """Visible characters in a cue (excludes the joining newline) — used so the
    line break never skews a chunk's proportional time share."""
    return len(cue) - cue.count("\n")


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
            # The line's on-screen window: from its source time to the next
            # selected line's start (same segment) or the clip end.
            win_start = clip_start + (line["seconds"] - seg["start_sec"])
            if i + 1 < len(lines):
                win_end = clip_start + (lines[i + 1]["seconds"] - seg["start_sec"])
            else:
                win_end = clip_end
            win_start = max(clip_start, min(win_start, clip_end))
            win_end = max(win_start, min(win_end, clip_end))

            chunks = _chunk_line(line.get("text"))
            if not chunks or win_end <= win_start:
                continue

            # Distribute the window across chunks proportionally to their length,
            # anchoring each chunk to its slot; MAX_CUE_SEC only ends a cue early
            # (a gap until the next chunk's start), never shifts later chunks.
            window = win_end - win_start
            total = sum(_vlen(c) for c in chunks) or 1
            acc = 0
            for c in chunks:
                cue_start = win_start + window * (acc / total)
                acc += _vlen(c)
                nominal_end = win_start + window * (acc / total)
                cue_end = min(nominal_end, cue_start + MAX_CUE_SEC)
                if cue_end <= cue_start:
                    continue
                cues.append((cue_start, cue_end, c))
        reel_t = clip_end

    if not cues:
        return None
    body = "\n\n".join(f"{_fmt_ts(a)} --> {_fmt_ts(b)}\n{t}" for a, b, t in cues)
    return "WEBVTT\n\n" + body + "\n"

import re as _re

import storage as _storage
from pathlib import Path as _Path
from video_editor import parse_timestamp_to_seconds

_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+(\w[\w ]*?):\s*(.*)')

# Sentence boundary: terminal punctuation followed by whitespace. Matches the
# convention transcriber._split_into_sentences uses for Whisper output.
# Known over-splits: "Dr. Smith", "U.S.", "Well..." — accepted rather than
# adding an abbreviation list, because the failure mode is a short fragment
# clip, which errs toward the finer granularity this function exists to produce.
_SENTENCE_SPLIT_RE = _re.compile(r'(?<=[.!?])\s+')

# Interpolated sentence starts are pulled this many seconds earlier. Forven
# turn windows include pauses, so a proportional estimate skews late — and late
# is the harmful direction (it clips the first word). Erring early costs at
# most a beat of lead-in, which the 0.4s clip fade-in softens.
START_BIAS_SECONDS = 1.0

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

# Rate used ONLY for the clip end in group_lines_into_segments, deliberately
# slower than SPEAKING_RATE. The two cannot share a value: SPEAKING_RATE also
# sizes normalize_transcript's interpolation window, where lowering it pushes
# sentence STARTS later and clips first words -- trading one cut for another.
#
# Slower here because the error is asymmetric. The clip end is a min() against
# the next line's start, so over-estimating costs at most trailing air (bounded
# by that next start, softened by the 0.4s fade-out), while under-estimating
# cuts the speaker off mid-point. Measured at 2.0 w/s an emphatic speaker lost
# their last 2s; people slow down and pause exactly where the content matters.
CLIP_TAIL_RATE = 1.6   # words/sec

# Hard ceiling on any single clip. Sentence normalization gets most clips to
# 5-15s; this is the safety net for turns with no terminal punctuation (which
# pass through unsplit) and for over-wide ranges returned by analyze. Set above
# the target range so it only fires on pathological cases -- normal clips still
# end on a natural sentence boundary.
#
# Raised from 22s: at 22s a long answer was guillotined mid-thought (a 12
# sentence, 120 word answer lost 7 of its sentences). This ceiling only ever
# truncates, so it must sit above any answer worth keeping whole.
MAX_CLIP_SECONDS = 40.0


# Duplicates transcriber.py's own formatter verbatim. Deliberate: importing
# transcriber from shared would put a Whisper-adjacent module on both
# services' import path for the sake of a divmod and an f-string.
def _seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def normalize_transcript(raw_text: str) -> str:
    """Split multi-sentence turn lines into one line per sentence.

    Production transcripts are Forven platform exports: one line per whole
    speaker turn, often 30-60s. That is the granularity ceiling on clip
    length, because both Claude's returned ranges and manual selection can
    only address whole lines. This splits each turn into sentences and
    interpolates a timestamp for each by word-count proportion across the
    turn's *estimated speech window* (not the raw turn window, which includes
    trailing dead air and would skew every estimate late).

    Must stay deterministic: the main app and generator service each normalize
    the same .txt independently, and the resulting `raw` line strings are the
    selection identity shared between them.

    Lines that don't parse, single-sentence lines, and unpunctuated turns pass
    through per-line unchanged, so the function is idempotent. Note this is
    per-line, not byte-identical at the file level: splitlines()+join()
    normalizes CRLF to LF and drops a trailing newline.

    Known accepted collision: two identical sentences in the same turn that
    interpolate to the same second (e.g. a repeated "Yeah.") produce identical
    `raw` output lines. Since `raw` is the cross-service selection identity,
    selecting one selects both, which can duplicate a short clip in the reel.
    Measured on production-shaped transcripts this affects ~1-2% of lines but
    around a third of transcripts, concentrated on short backchannel sentences
    ("Yeah.", "Right."). It is accepted rather than fixed because the
    alternative — nudging a later duplicate's timestamp forward to force
    uniqueness — was tried and reverted: it breaks the monotonic-timestamp
    invariant that group_lines_into_segments and captions.collect_caption_lines
    both rely on, trading an identity collision for frequent, silent
    mis-ordering. If this ever needs closing, do it in the selection layer
    (index-qualify the key) rather than by fabricating timestamps.
    """
    lines = raw_text.splitlines()
    parsed: list[tuple[float, str, str] | None] = []
    for raw in lines:
        m = _LINE_RE.match(raw.strip())
        if m:
            ts, speaker, text = m.group(1), m.group(2).strip(), m.group(3)
            parsed.append((parse_timestamp_to_seconds(ts), speaker, text))
        else:
            parsed.append(None)

    # Start of the next parseable line, used to bound each turn's window.
    next_starts: list[float | None] = [None] * len(parsed)
    upcoming: float | None = None
    for i in range(len(parsed) - 1, -1, -1):
        next_starts[i] = upcoming
        if parsed[i] is not None:
            upcoming = parsed[i][0]

    out: list[str] = []
    for raw, entry, next_start in zip(lines, parsed, next_starts):
        if entry is None:
            out.append(raw)
            continue
        start, speaker, text = entry
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s]
        if len(sentences) < 2:
            out.append(raw)
            continue

        total_words = sum(len(s.split()) for s in sentences)

        # Estimated end of speech in this turn, capped by the next line's
        # start. Interpolating over this (rather than to next_start) keeps
        # trailing silence from stretching every sentence estimate later.
        speech_end = start + total_words / SPEAKING_RATE + TAIL_BUFFER
        window_end = speech_end if next_start is None else min(next_start, speech_end)
        span = max(0.0, window_end - start)

        words_before = 0
        for sentence in sentences:
            offset = (words_before / total_words) * span
            ts_sec = max(start, start + offset - START_BIAS_SECONDS)
            out.append(f"[{_seconds_to_timestamp(ts_sec)}] {speaker}: {sentence}")
            words_before += len(sentence.split())

    return "\n".join(out)


def read_transcript(txt_path: str | _Path) -> str:
    """Read a transcript sidecar and return it sentence-normalized.

    Every transcript read in both services goes through here so no code path
    can accidentally work with un-normalized turn-level lines -- the `raw`
    strings are the selection identity shared across services, so they must
    match everywhere. (Same invariant as filter_generated_reels: if you add a
    new code path that reads a .txt, call this.) The file on disk is never
    modified; it is client data.
    """
    return normalize_transcript(_Path(txt_path).read_text(encoding="utf-8"))


def group_lines_into_segments(
    all_lines: list, selected_raws: set, video_duration: float | None = None
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Each segment's end is capped near the last selected line's estimated speech
    end (see SPEAKING_RATE / TAIL_BUFFER) to trim trailing dead air, then capped
    again at MAX_CLIP_SECONDS as a hard ceiling, then the MIN_CLIP_SECONDS floor
    is applied — so a lone title card with no clip is never emitted, and short
    segments are extended (clamped to video duration) or dropped if the source
    can't reach the floor.

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
            # Add START_BIAS_SECONDS back before estimating the speech end.
            # normalize_transcript pulls each sentence's recorded start that far
            # early to protect the FIRST word, so measuring the tail from the
            # biased start spent the whole TAIL_BUFFER undoing that shift --
            # leaving an effective margin of TAIL_BUFFER - START_BIAS_SECONDS,
            # i.e. exactly zero. Any speaker slower than SPEAKING_RATE, or any
            # pause for emphasis, then lost their last words. Measure the tail
            # from where speech actually starts so the buffer is real again.
            speech_start = last_line["seconds"] + START_BIAS_SECONDS
            speech_end = speech_start + words / CLIP_TAIL_RATE + TAIL_BUFFER
            end = min(end, speech_end)
        end = min(end, start + MAX_CLIP_SECONDS)
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

import re as _re

import storage as _storage
from pathlib import Path as _Path
from video_editor import parse_timestamp_to_seconds

# Optional end timestamp: [M:SS] (plain) or [M:SS-M:SS] (rich). Groups are
# 1=start, 2=end-or-None, 3=speaker, 4=text — NOTE the shift, this regex is
# also used by normalize_transcript.
_LINE_RE = _re.compile(r'^\[(\d+:\d{2})(?:-(\d+:\d{2}))?\]\s+(\w[\w ]*?):\s*(.*)')

# Sentence boundary: terminal punctuation followed by whitespace. Matches the
# convention transcriber._split_into_sentences uses for Whisper output.
# Known over-splits: "Dr. Smith", "U.S.", "Well..." — accepted rather than
# adding an abbreviation list, because the failure mode is a short fragment
# clip, which errs toward the finer granularity this function exists to produce.
_SENTENCE_SPLIT_RE = _re.compile(r'(?<=[.!?])\s+')

# Inline anchor inside an already-parsed rich line: [M:SS] embedded in text.
# Used by expand_anchors to split anchored turn lines into sentence-level rich lines.
_INLINE_ANCHOR_RE = _re.compile(r'\[(\d+:\d{2})\]')

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

    Each dict has keys: raw, timestamp, speaker, is_interviewer, text, seconds,
    end_seconds, minute_bucket. end_seconds is None unless the line uses the
    rich [M:SS-M:SS] format with an end strictly after its start.
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
        ts, end_ts = m.group(1), m.group(2)
        speaker, text = m.group(3).strip(), m.group(4)
        seconds = parse_timestamp_to_seconds(ts)
        # An end that is not strictly after the start cannot be real, so treat
        # it as absent. Tier detection (next task) requires EVERY respondent
        # line to have a valid end, so one bad end will fall the file back to
        # plain-tier clip boundaries rather than trusting a broken timestamp.
        end_seconds = None
        if end_ts:
            candidate = parse_timestamp_to_seconds(end_ts)
            if candidate > seconds:
                end_seconds = candidate
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "speaker": speaker,
            "is_interviewer": is_interviewer_label(speaker),
            "text": text,
            "seconds": seconds,
            "end_seconds": end_seconds,
            "minute_bucket": int(seconds) // 60,
        })
    return lines


def transcript_tier(lines: list[dict]) -> str:
    """Classify parsed lines as "rich" (real end times) or "plain".

    Rich only if EVERY respondent line carries a valid end_seconds. Interviewer
    lines are exempt: clip ends come from the last selected respondent line and
    captions exclude the interviewer.

    Strict all-or-nothing is deliberate. A per-line fallback would mix exact and
    estimated boundaries inside a single reel, producing inconsistent output
    with an invisible cause.

    Must stay pure and deterministic: app.py and generator_app.py classify the
    same .txt independently and must always agree.
    """
    respondent = [l for l in lines if not l.get("is_interviewer")]
    if not respondent:
        return "plain"
    if all(l.get("end_seconds") is not None for l in respondent):
        return "rich"
    return "plain"


# Lines whose speech overlaps Claude's returned range by less than this fraction
# of the line's own duration are excluded in rich tier. 0.5 means the majority
# of a line's speech must fall inside the range to be selected.
# ponytail: 0.5 is a calibrated starting point — tune against real rich transcripts
# if clips are too short (raise it) or too long (lower it).
MIN_LINE_OVERLAP_RATIO = 0.5


def lines_in_range(
    all_lines: list[dict], start_sec: float, end_sec: float
) -> list[dict]:
    """Return respondent lines whose speech falls within Claude's returned range.

    Plain tier: a line is included when its start falls within
    [start_sec - 0.5, end_sec + 0.5]. This is the existing predicate, unchanged.

    Rich tier: a line is included when its speech interval
    [seconds, end_seconds] overlaps Claude's range by more than
    MIN_LINE_OVERLAP_RATIO of the line's own duration. This excludes a 34s line
    that merely grazes the range at its first second, and includes a line that
    starts just before the range but lies almost entirely inside it.

    A line with end_seconds=None inside a rich file falls back to the plain
    predicate (possible on interviewer lines whose ends are rarely present).

    Interviewer lines are always excluded.
    """
    tier = transcript_tier(all_lines)
    result = []
    for line in all_lines:
        if line.get("is_interviewer"):
            continue
        if tier == "rich" and line.get("end_seconds") is not None:
            line_dur = line["end_seconds"] - line["seconds"]
            if line_dur <= 0:
                continue
            overlap = max(0.0, min(line["end_seconds"], end_sec) - max(line["seconds"], start_sec))
            if overlap / line_dur > MIN_LINE_OVERLAP_RATIO:
                result.append(line)
        else:
            if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                result.append(line)
    return result


# A clip shorter than this is imperceptible. Segments are extended to this floor,
# or dropped when the source can't provide it.
MIN_CLIP_SECONDS = 1.5

# Used ONLY by normalize_transcript, to size the interpolation window when
# splitting a turn into sentences (plain tier only). These no longer influence
# any clip end: that is real transcript data in the rich tier and the next
# line's start in the plain tier.
SPEAKING_RATE = 2.0    # words/sec
TAIL_BUFFER = 1.0      # seconds

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


def expand_anchors(raw_text: str) -> str:
    """Split anchored turn lines into consecutive sentence-level rich lines.

    An anchored line like:
        [0:04-0:20] Participant: text [0:09] more [0:14] still more

    becomes:
        [0:04-0:09] Participant: text
        [0:09-0:14] Participant: more
        [0:14-0:20] Participant: still more

    Lines with no inline anchors (including plain-tier lines and sentence-level
    rich lines) are returned unchanged, making the function idempotent.

    Malformed anchors (outside the line window, non-monotonic) cause the whole
    line to be returned unchanged — never fabricate a boundary.
    """
    out: list[str] = []
    for raw in raw_text.splitlines():
        stripped = raw.strip()
        m = _LINE_RE.match(stripped)
        # Only expand lines that have a valid outer end timestamp (rich lines).
        if not m or not m.group(2):
            out.append(raw)
            continue

        start_ts, end_ts = m.group(1), m.group(2)
        speaker, text = m.group(3).strip(), m.group(4)
        line_start = parse_timestamp_to_seconds(start_ts)
        line_end = parse_timestamp_to_seconds(end_ts)

        # Split text on inline anchors. re.split with a capturing group
        # interleaves captured timestamps: ["a ", "0:09", " b ", "0:14", " c"]
        parts = _INLINE_ANCHOR_RE.split(text)
        text_chunks = parts[0::2]   # ["a ", " b ", " c"]
        anchor_strs = parts[1::2]   # ["0:09", "0:14"]

        if not anchor_strs:
            out.append(raw)
            continue

        anchor_secs = [parse_timestamp_to_seconds(a) for a in anchor_strs]

        # Validate: strictly increasing, each within [line_start, line_end).
        valid = (
            anchor_secs[0] > line_start
            and anchor_secs[-1] < line_end
            and all(anchor_secs[i] < anchor_secs[i + 1] for i in range(len(anchor_secs) - 1))
        )
        if not valid:
            out.append(raw)
            continue

        # boundaries[i] is the start of text_chunks[i]; boundaries[-1] is line_end.
        boundaries = [line_start] + anchor_secs + [line_end]

        last_non_empty = max(
            (i for i, c in enumerate(text_chunks) if c.strip()), default=None
        )
        if last_non_empty is None:
            out.append(raw)
            continue

        # Build result lines. running_start tracks the accumulated start for
        # the current non-empty chunk — empty chunks don't advance it, so the
        # next non-empty chunk absorbs the empty span (spec: "neighbour absorbs").
        running_start = line_start
        result_lines: list[str] = []
        for i, chunk_text in enumerate(text_chunks):
            chunk_stripped = chunk_text.strip()
            # Last non-empty chunk always ends at line_end (absorbs trailing empties).
            chunk_end = line_end if i == last_non_empty else boundaries[i + 1]
            if chunk_stripped:
                s_ts = _seconds_to_timestamp(running_start)
                e_ts = _seconds_to_timestamp(chunk_end)
                result_lines.append(f"[{s_ts}-{e_ts}] {speaker}: {chunk_stripped}")
                running_start = boundaries[i + 1]
            # Empty chunk: running_start stays, next non-empty chunk absorbs the span.

        out.extend(result_lines if result_lines else [raw])

    return "\n".join(out)


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
            # Groups 3/4 (not 2/3): _LINE_RE gained an optional end group.
            ts, speaker, text = m.group(1), m.group(3).strip(), m.group(4)
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
    """Read a transcript sidecar and return it ready for parsing.

    Every transcript read in both services goes through here so no code path
    can accidentally work with un-normalized turn-level lines -- the `raw`
    strings are the selection identity shared across services, so they must
    match everywhere. (Same invariant as filter_generated_reels: if you add a
    new code path that reads a .txt, call this.) The file on disk is never
    modified; it is client data.

    Routes on tier:
      rich  -> expand_anchors: expands any inline anchors into sentence-level
               rich lines; sentence-level rich lines pass through unchanged.
      plain -> normalize_transcript: splits turn-level lines by sentence with
               interpolated timestamps, as before.
    """
    text = _Path(txt_path).read_text(encoding="utf-8")
    if transcript_tier(parse_transcript_lines(text)) == "rich":
        return expand_anchors(text)
    return normalize_transcript(text)


def group_lines_into_segments(
    all_lines: list, selected_raws: set, video_duration: float | None = None
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Rich transcripts end each clip at the last selected line's real end time.
    Plain transcripts end at the start of the first line after the run — the
    only end-adjacent timestamp such a file contains. No word-count estimation
    in either path.

    MAX_CLIP_SECONDS still applies in BOTH tiers and can truncate a genuinely
    long run mid-sentence. That is a reel-pacing decision, not a timing guess:
    a single 40s+ clip is too long for a highlight reel. It is the only
    remaining path by which a clip can end mid-sentence.

    Pure logic shared by the generator (real clip ranges) and the main app's
    analyze (create-screen length estimate), so both compute identical durations.
    """
    rich = transcript_tier(all_lines) == "rich"

    def _finalize(start: float, end: float, last_line: dict):
        # Rich: the transcript states when this speaker stopped. Trust it.
        # Plain: `end` is already the next line's start, the only real signal.
        if rich and last_line.get("end_seconds") is not None:
            end = last_line["end_seconds"]
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
        # No next line. Rich tier gets a real end from _finalize. Plain tier
        # uses the video duration when known; when it is not (cloud planning),
        # MAX_CLIP_SECONDS bounds it here and the browser encoder clamps the
        # range to the real media length via computeDuration().
        end = video_duration if video_duration is not None else float("inf")
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

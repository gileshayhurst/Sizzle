"""Render aligned sentences as a rich transcript.

Output is the format shared.transcript_tier classifies as "rich":
    [M:SS-M:SS] Role: sentence
"""
import math


def _stamp(total_seconds: int) -> str:
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def rich(aligned: list[dict]) -> str:
    """Render aligned sentences as rich transcript lines.

    Starts truncate and ends round UP. Truncating an end moves it earlier,
    clipping the speaker's final word -- the defect the rich format exists to
    remove. Erring early on a start costs at most a beat of lead-in, which the
    clip fade-in softens.

    Whole-second rounding can push a line's end past the next line's start, so
    an end is clamped to the following line's EMITTED start -- the value a
    reader actually parses. Where the clamp would collapse the line to zero
    length the overlap is kept instead: never clipping the last word matters
    more than strict monotonicity on a sub-second backchannel.
    """
    starts = [max(0, int(s["start"])) for s in aligned]
    lines = []
    for index, item in enumerate(aligned):
        start = starts[index]
        end = max(0, math.ceil(item["end"] - 1e-9))
        if index + 1 < len(aligned):
            clamped = min(end, starts[index + 1])
            if clamped > start:
                end = clamped
        lines.append(f"[{_stamp(start)}-{_stamp(end)}] {item['role']}: {item['text']}")
    return "\n".join(lines)


def measured(aligned: list[dict]) -> list[dict]:
    """Only the sentences whose BOTH boundaries came from matched ASR words.

    A sentence the ASR never matched gets an interpolated span in reconcile —
    which is precisely the proportional interpolation D1 rejected as "will clip
    words mid-syllable. Unacceptable for customer-facing output." Rather than
    emit that alongside real timings and hope nobody selects it, it is dropped.

    This is what makes transcript_tier == "rich" honest: the format promises
    measured end times, so every line in the file must actually have one. Text
    that failed to anchor is text that is not in the video, and is not clippable
    in any case — the truncated reference interview drops 54 of 145 lines for
    exactly that reason, and match_rate flags the file.
    """
    return [s for s in aligned if s["anchor"] == "exact"]


def stats(aligned: list[dict], match_rate: float) -> dict:
    """Per-file summary, surfaced to the operator rather than stored.

    `emitted` is what reaches the file; `exact`/`partial`/`unanchored` explain
    why, and `dropped` is what did not survive.
    """
    exact = sum(1 for s in aligned if s["anchor"] == "exact")
    return {
        "sentences": len(aligned),
        "emitted": exact,
        "dropped": len(aligned) - exact,
        "exact": exact,
        "partial": sum(1 for s in aligned if s["anchor"] == "partial"),
        "unanchored": sum(1 for s in aligned if s["anchor"] == "none"),
        "match_rate": round(match_rate, 4),
    }

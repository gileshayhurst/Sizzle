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


def stats(aligned: list[dict], match_rate: float) -> dict:
    """Per-file confidence summary, surfaced to the operator rather than stored."""
    return {
        "sentences": len(aligned),
        "exact": sum(1 for s in aligned if s["anchor"] == "exact"),
        "partial": sum(1 for s in aligned if s["anchor"] == "partial"),
        "unanchored": sum(1 for s in aligned if s["anchor"] == "none"),
        "match_rate": round(match_rate, 4),
    }

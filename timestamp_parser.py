import re

# The optional `\]?` matters: rich-tier responses come back bracketed
# (`[2:18-2:30]|9`) because the transcript itself uses [M:SS-M:SS] and
# _RICH_PROMPT_CLAUSE's example reinforces it. Without it the range still
# matched but the score group did not, so EVERY rich segment silently took the
# default of 5 and nothing could clear a score threshold.
_RANGE_RE = re.compile(r'(\d+:\d{2}-\d+:\d{2})\]?(?:\s*\|\s*([^\s,]+))?')


def parse_scored_timestamps(response: str) -> list[tuple[str, int]] | None:
    """Parse Claude's scored segment response.

    Each segment is 'M:SS-M:SS' optionally followed by '|N' (N = 1..10).
    Missing or non-integer score defaults to 5; out-of-range scores clamp to
    1..10. Returns None when the response is exactly 'none' (case-insensitive)
    or contains no ranges.
    """
    response = response.strip()
    if response.lower() == "none":
        return None
    result: list[tuple[str, int]] = []
    for rng, raw_score in _RANGE_RE.findall(response):
        score = 5
        if raw_score:
            try:
                score = max(1, min(10, int(raw_score)))
            except ValueError:
                score = 5
        result.append((rng, score))
    return result or None

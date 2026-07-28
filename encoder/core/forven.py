"""Parse Forven plain transcripts into turns and sentences.

Forven exports one line per whole speaker turn:
    [MM:SS] Participant: Sentence one. Sentence two.

This module is the only place that knows the input format, so a change to
Forven's export is a change to this file alone.
"""
import re

# [MM:SS] Role: text. Forven zero-pads the minute; accept any width.
_LINE_RE = re.compile(r"^\[(\d+):(\d{2})\]\s+(\w[\w ]*?):\s*(.*)")

# Terminal punctuation, whitespace, then a character that can open a sentence.
# Requiring the following character stops "10.5" and "e.g. this" from splitting.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"“‘])')

# A candidate ENDING in one of these is an abbreviation rather than a sentence
# boundary, so it absorbs the following chunk. The trailing single-letter arm
# covers initials ("J. Smith"). Deliberately excludes "no." and "fig." -- both
# are common sentence endings, and a false merge costs a whole boundary.
_ABBREV_RE = re.compile(
    r"(?:\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc)|\b[ap]\.m|\b[A-Za-z])\.$",
    re.IGNORECASE,
)


def parse(text: str) -> list[dict]:
    """Parse a plain Forven transcript into turns.

    Returns [{"start": float, "role": str, "text": str}]. Unparseable lines are
    skipped, matching how shared.parse_transcript_lines treats bad input.
    """
    turns = []
    for raw in text.splitlines():
        match = _LINE_RE.match(raw.strip())
        if not match:
            continue
        turns.append({
            "start": float(int(match.group(1)) * 60 + int(match.group(2))),
            "role": match.group(3).strip(),
            "text": match.group(4).strip(),
        })
    return turns


def sentences(text: str) -> list[str]:
    """Split a turn's text into sentences.

    Splits on terminal punctuation followed by whitespace and a sentence-opening
    character; never on commas, and never after an abbreviation. Disfluency
    prefixes ("Um,") stay attached and short answers ("Yeah.") remain their own
    sentence -- both are clippable units a reel may need.
    """
    out: list[str] = []
    held = ""
    for chunk in _SENTENCE_RE.split(text.strip()):
        candidate = f"{held} {chunk}".strip() if held else chunk
        if _ABBREV_RE.search(candidate):
            held = candidate
            continue
        if candidate:
            out.append(candidate)
        held = ""
    if held:
        out.append(held)
    return out

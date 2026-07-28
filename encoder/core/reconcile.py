"""Map ASR word timings onto Forven's canonical sentences.

The ASR is an ANCHOR source, not a transcript source: Forven supplies every
word that reaches the output, and the ASR only says when those words were
spoken. Measured on a real interview, `tiny` anchors as many sentences as
`base` (47 of 48), because a sentence needs only one matched word to be pinned.
That is why the browser path can ship a small model.
"""
import difflib
import re

# Used ONLY to give an unanchored sentence a plausible duration. Never applied
# to a sentence that has a real anchor.
WORDS_PER_SECOND = 2.5

_STRIP_RE = re.compile(r"[^a-z0-9']")


def normalize(token: str) -> str:
    """Reduce a token to its comparable core: lowercase, alphanumerics, apostrophe."""
    return _STRIP_RE.sub("", token.lower())


def _fallback_duration(text: str) -> float:
    return max(1.0, len(text.split()) / WORDS_PER_SECOND)


def align(sentences: list[dict], words: list[dict]) -> tuple[list[dict], float]:
    """Attach start/end times to each sentence.

    sentences: [{"role": str, "text": str}]
    words:     [{"w": str, "s": float, "e": float}]

    Returns (aligned, match_rate). Each aligned sentence gains "start", "end",
    "confidence" (fraction of its tokens matched) and "anchor", one of:
      exact   -- both boundaries came from matched words
      partial -- one boundary matched, the other derived
      none    -- nothing matched; times are interpolated from the previous end
    """
    tokens: list[str] = []
    spans: list[tuple[int, int]] = []
    for sentence in sentences:
        first = len(tokens)
        tokens.extend(t for t in (normalize(w) for w in sentence["text"].split()) if t)
        spans.append((first, len(tokens) - 1))

    asr_tokens = [normalize(word["w"]) for word in words]

    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    matcher = difflib.SequenceMatcher(a=tokens, b=asr_tokens, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for offset in range(size):
            starts[a + offset] = words[b + offset]["s"]
            ends[a + offset] = words[b + offset]["e"]

    match_rate = len(starts) / len(tokens) if tokens else 0.0

    aligned: list[dict] = []
    previous_end = 0.0
    for sentence, (first, last) in zip(sentences, spans):
        start = next((starts[i] for i in range(first, last + 1) if i in starts), None)
        end = next((ends[i] for i in range(last, first - 1, -1) if i in ends), None)
        matched = sum(1 for i in range(first, last + 1) if i in starts)
        total = max(1, last - first + 1)

        if start is not None and end is not None and end > start:
            anchor = "exact"
        elif start is not None or end is not None:
            anchor = "partial"
            if start is None:
                start = previous_end
            if end is None or end <= start:
                end = start + _fallback_duration(sentence["text"])
        else:
            anchor = "none"
            start = previous_end
            end = start + _fallback_duration(sentence["text"])

        previous_end = end
        aligned.append({
            **sentence,
            "start": start,
            "end": end,
            "confidence": matched / total,
            "anchor": anchor,
        })

    return aligned, match_rate

"""Pure transcript-encoding core.

encode() is the public API. Every entrypoint -- CLI, service, and the browser
path -- funnels through it, so the alignment algorithm has exactly one
implementation.
"""
from .emit import measured, rich, stats
from .forven import parse, repair, sentences
from .reconcile import align

__all__ = ["encode", "align", "measured", "parse", "repair", "rich", "sentences", "stats"]


def encode(transcript_text: str, words: list[dict]) -> dict:
    """Turn a plain Forven transcript plus ASR word timings into a rich transcript.

    Returns {"rich": str, "stats": dict}. Only sentences with both boundaries
    measured are emitted — see emit.measured. Callers must check
    stats["emitted"]: zero means nothing anchored, and overwriting a working
    plain transcript with an empty file would be worse than doing nothing.
    """
    flat = [
        {"role": turn["role"], "text": sentence}
        for turn in parse(transcript_text)
        for sentence in sentences(turn["text"])
    ]
    aligned, match_rate = align(flat, words)
    return {"rich": rich(measured(aligned)), "stats": stats(aligned, match_rate)}

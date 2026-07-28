"""Pure transcript-encoding core.

encode() is the public API. Every entrypoint -- CLI, service, and the browser
path -- funnels through it, so the alignment algorithm has exactly one
implementation.
"""
from .emit import rich, stats
from .forven import parse, sentences
from .reconcile import align

__all__ = ["encode", "align", "parse", "rich", "sentences", "stats"]


def encode(transcript_text: str, words: list[dict]) -> dict:
    """Turn a plain Forven transcript plus ASR word timings into a rich transcript.

    Returns {"rich": str, "stats": dict}.
    """
    flat = [
        {"role": turn["role"], "text": sentence}
        for turn in parse(transcript_text)
        for sentence in sentences(turn["text"])
    ]
    aligned, match_rate = align(flat, words)
    return {"rich": rich(aligned), "stats": stats(aligned, match_rate)}

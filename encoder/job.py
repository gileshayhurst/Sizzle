"""One-off job entrypoint: encode every interview in a cloud session.

    python -m encoder.job <session_key> [--workers N] [--model tiny]

Shape and rationale come from sizzle_reel_design.md §8. A persistent service
billed at rest is the wrong shape for work this bursty, so this runs as a Render
one-off job: billed per second while running, scaling to zero afterwards. Because
billing is per second, an oversized instance is roughly cost-neutral and far
faster for CPU-bound work -- so request a generous `planId` and encode the whole
folder in parallel rather than one interview at a time.

Video is pulled from R2 into the job, which costs nothing: R2 egress is free and
Render bills outbound, not inbound. The "never send video to the encoder" rule
applies to the always-on web service, whose metered egress and 512 MB ceiling are
the actual constraints -- not to a 16-CPU job pulling from object storage.

It is STREAMED, not downloaded. That distinction is load-bearing: bandwidth and
RAM were costed when this was designed, but disk never was, and Render one-off
jobs get only a **2 GB /tmp**. Writing each video to a temp file blew that volume
on the first wave of workers. Peak disk is now zero, so folder size and file size
stop being disk questions entirely, and worker count is bounded by RAM and cores.
"""
import argparse
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import r2
from .asr.local import DEFAULT_MODEL_SIZE, words
from .cli import VIDEO_SUFFIXES, is_rich
from .core import encode

# sessions/<uuid> is what storage.new_session_key() mints. Validated because this
# value reaches a command line -- see the fixed-entrypoint rule in the design
# doc's job security model (§10).
SESSION_KEY_RE = re.compile(r"^sessions/[0-9a-fA-F-]{8,64}$")

_local = threading.local()


def _model(size: str):
    """One model per worker thread.

    CTranslate2 makes no thread-safety promise for concurrent transcribe() calls
    on one model, and a model per thread is cheap at this size (~75 MB for tiny
    int8) next to the instance a job like this runs on.
    """
    if getattr(_local, "model", None) is None or _local.size != size:
        from faster_whisper import WhisperModel
        # cpu_threads=1: parallelism comes from running many interviews at once,
        # so letting each model grab every core would just make them fight.
        _local.model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=1)
        _local.size = size
    return _local.model


def find_pairs(keys: list[str]) -> list[tuple[str, str]]:
    """Pair each video key with its same-stem .txt key.

    Videos with no transcript are skipped -- there is nothing to align against.
    """
    texts = {k.rsplit(".", 1)[0]: k for k in keys if k.lower().endswith(".txt")}
    pairs = []
    for key in keys:
        stem, _, suffix = key.rpartition(".")
        if f".{suffix.lower()}" not in VIDEO_SUFFIXES:
            continue
        if stem in texts:
            pairs.append((key, texts[stem]))
    return sorted(pairs)


def encode_one(video_key: str, text_key: str, size: str, log=print) -> dict | None:
    """Encode one interview in place. Returns its stats, or None if skipped."""
    plain = r2.read_text(text_key)
    if is_rich(plain):
        log(f"skip {video_key}: already rich")
        return None

    # Streamed, never downloaded -- see r2.presigned_url. /tmp is 2 GB and one
    # interview can be 1.4 GB, so a temp file here is what killed the job.
    log(f"encoding {video_key}")
    result = encode(plain, words(r2.presigned_url(video_key), model=_model(size)))

    stats = result["stats"]
    # Nothing anchored: leave the working plain transcript alone rather than
    # replacing it with an empty file.
    if stats["emitted"] == 0:
        log(f"skip {video_key}: no sentences anchored "
            f"({stats['match_rate']:.1%} word match) — transcript left untouched")
        return None

    # Preserve the client's original before overwriting, mirroring the CLI's
    # --in-place semantics. Written FIRST so a failure between the two writes
    # cannot lose it.
    stem = text_key.rsplit(".", 1)[0]
    r2.upload_text(f"{stem}.forven.txt", plain)
    r2.upload_text(text_key, result["rich"] + "\n")

    dropped = f", {stats['dropped']} dropped" if stats["dropped"] else ""
    log(f"done {video_key}: {stats['emitted']} of {stats['sentences']} sentences"
        f"{dropped}, {stats['match_rate']:.1%} word match")
    return stats


def run(session_key: str, workers: int = 4, size: str = DEFAULT_MODEL_SIZE,
        log=print) -> list[dict]:
    """Encode every un-encoded interview in `session_key`, in parallel."""
    if not SESSION_KEY_RE.match(session_key):
        raise ValueError(f"refusing suspicious session key: {session_key!r}")

    pairs = find_pairs(r2.list_keys(session_key))
    log(f"{len(pairs)} interview(s) with transcripts in {session_key}")
    if not pairs:
        return []

    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(encode_one, video, text, size, log): video
            for video, text in pairs
        }
        for future in futures:
            video = futures[future]
            try:
                stats = future.result()
                if stats is not None:
                    results.append({"video": video, "stats": stats})
            except Exception as exc:
                # One bad interview must not lose the rest of the folder; the
                # plain transcript stays in place and still produces a reel.
                log(f"FAILED {video}: {exc}")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m encoder.job", description=__doc__)
    parser.add_argument("session_key", help="sessions/<uuid> prefix in R2")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("ENCODER_JOB_WORKERS", "4")),
                        help="interviews to encode concurrently (default 4)")
    parser.add_argument("--model", default=os.environ.get("ENCODER_MODEL_SIZE", DEFAULT_MODEL_SIZE))
    args = parser.parse_args(argv)

    results = run(args.session_key, workers=args.workers, size=args.model,
                  log=lambda m: print(m, flush=True))
    print(f"\nencoded {len(results)} interview(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

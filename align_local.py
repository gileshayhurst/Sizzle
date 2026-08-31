"""Align one ingested interview locally, without R2.

encoder/job.py drives alignment through encoder/r2.py, which is S3/R2-only and
raises in local mode. The memo's other sanctioned route is the library:
`encoder.core.encode` is a pure function over transcript text plus ASR word
timings. This script wires that up against files on disk.

    python align_local.py sessions/<uuid> <REF>

Writes <REF>.forven.txt (the original, preserved) and overwrites <REF>.txt with
the aligned version, mirroring what encoder.job does in cloud mode.
"""

import sys
from pathlib import Path

from encoder.asr.local import words
from encoder.core import encode

# The model is only an ANCHOR source - asr/local.py notes that `tiny` anchors as
# many sentences as `base`, so it is the right trade for a run like this.
MODEL_SIZE = "tiny"


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 2

    session_key, ref = argv[0], argv[1]
    root = Path(__file__).parent / session_key
    video = root / f"{ref}.mp4"
    text = root / f"{ref}.txt"

    for path in (video, text):
        if not path.exists():
            print(f"missing: {path}")
            return 1

    plain = text.read_text(encoding="utf-8")
    print(f"transcript: {len(plain.splitlines())} lines")
    print(f"video     : {video.stat().st_size / 1_000_000:.1f} MB")
    print(f"model     : {MODEL_SIZE}  (first run downloads it)")
    print("running ASR + forced alignment, this is the slow step...")

    word_timings = words(str(video), size=MODEL_SIZE)
    print(f"ASR produced {len(word_timings)} word timings")

    result = encode(plain, word_timings)

    original = root / f"{ref}.forven.txt"
    if not original.exists():
        original.write_text(plain, encoding="utf-8")
    text.write_text(result["rich"] + "\n", encoding="utf-8")

    print(f"wrote {text.name} (aligned) and preserved {original.name}")
    stats = {k: v for k, v in result.items() if k != "rich"}
    print("stats:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())

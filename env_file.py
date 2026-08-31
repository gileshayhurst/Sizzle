"""Load a .env file into os.environ.

app.py has a hand-rolled loader that reads exactly one key
(ANTHROPIC_API_KEY), so every other setting in .env - the S3/R2 credentials
especially - is invisible to anything run outside Render, where the dashboard
supplies the environment instead.

Existing environment variables always win, so an explicit export still
overrides the file.

    import env_file; env_file.load()
"""

import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / ".env"


def load(path=DEFAULT_PATH, override: bool = False) -> int:
    """Set variables from a KEY=VALUE file. Returns how many were applied.

    Ignores blank lines and comments, strips matched surrounding quotes, and
    never logs a value - these are credentials.
    """
    path = Path(path)
    if not path.exists():
        return 0

    applied = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = value
        applied += 1
    return applied

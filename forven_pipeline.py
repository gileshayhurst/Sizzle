"""List and ingest new interviews from Forven.

Alignment is NOT run here - it stays the existing `python -m encoder.job
<session_key>` entry point, unchanged, so this module has one job. Selection
and cutting also stay where they already are; this only keeps the local corpus
fed. Interviews already ingested are skipped, so re-running is cheap and safe.
"""

import storage
from forven_ingest import ingest

INGEST_INDEX_KEY = "library/forven_ingested.json"


def already_ingested() -> set:
    """Refs we have pulled before. Missing index means we have pulled none."""
    try:
        return set(storage.read_json(INGEST_INDEX_KEY) or [])
    except Exception:
        return set()


def record_ingested(refs) -> None:
    storage.write_json(INGEST_INDEX_KEY, sorted(already_ingested() | set(refs)))


def sync(client, *, tenant_public_id: str, since: str | None = None, log=print):
    """Ingest every visible interview we have not seen. Returns the session key.

    Returns None when there is nothing new. Rows arrive in insertion order, not
    date order, so the iterator is drained fully before deciding.
    """
    seen = already_ingested()
    fresh = [
        row["interview_ref"]
        for row in client.iter_interviews(tenant_public_id, since=since)
        if row.get("interview_ref") and row["interview_ref"] not in seen
    ]
    if not fresh:
        log("nothing new to ingest")
        return None

    session_key = storage.new_session_key()
    ingest(client, tenant_public_id=tenant_public_id, refs=fresh,
           session_key=session_key, log=log)
    record_ingested(fresh)
    log(f"ingested {len(fresh)} interview(s) into {session_key}")
    return session_key

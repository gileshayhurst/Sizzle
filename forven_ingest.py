"""Materialise Forven interviews as a sessions/<key>/ prefix.

The existing pipeline eats a prefix of video+transcript pairs matched by stem
(see encoder/job.py find_pairs). This module is the only thing that needs to
know the interviews came from an API rather than a folder - everything
downstream is unchanged.
"""

import os
import shutil
import tempfile
from urllib import request as urlrequest

import storage
from forven_api import entries_to_contract_text

DOWNLOAD_TIMEOUT_SECONDS = 300


def _download_to_storage(url: str, key: str) -> None:
    """Stream a presigned media URL into storage.

    Downloads to a temp file and hands that to storage.upload_file, which works
    in BOTH local and cloud mode - storage.upload_stream is cloud-only and
    raises in local mode. Going via a file also keeps a multi-hundred-megabyte
    interview off the heap.
    """
    handle, temp_path = tempfile.mkstemp(suffix=".download")
    os.close(handle)
    try:
        with urlrequest.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with open(temp_path, "wb") as out:
                shutil.copyfileobj(response, out)
        storage.upload_file(temp_path, key)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def ingest(client, *, tenant_public_id: str, refs: list, session_key: str,
           log=print) -> str:
    """Write one .txt + .mp4 pair per ref under session_key. Returns it.

    Interviews whose transcript is not ready are skipped, not failed: they will
    be picked up by a later run once transcription completes.
    """
    for ref in refs:
        detail = client.get_transcript(tenant_public_id, ref)
        contract = entries_to_contract_text(detail.get("transcript_entries"))
        if not contract:
            log(f"skip {ref}: transcript not ready ({detail.get('transcript_status')})")
            continue

        storage.upload_bytes(
            f"{session_key}/{ref}.txt", contract.encode("utf-8"), "text/plain"
        )
        link = client.media_link(tenant_public_id, ref, disposition="attachment")
        _download_to_storage(link["url"], f"{session_key}/{ref}.mp4")
        log(f"ingested {ref}")

    return session_key

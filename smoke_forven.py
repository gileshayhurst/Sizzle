"""Manual smoke test against the real Forven Video Access API.

Not a unit test - it makes real network calls. Run it once before trusting the
integration. Reads everything from environment variables so no key ever appears
on a command line or in shell history.

    FORVEN_PROD_API_KEY       prod key, needs allow_download
    FORVEN_PROD_TENANT_ID     production source org
    FORVEN_PROD_TENANT_NAME   expected name, for the echo guard
    FORVEN_STAGING_API_KEY    staging key, needs allow_upload_reels
    FORVEN_STAGING_TENANT_ID  staging destination org
    FORVEN_STAGING_TENANT_NAME expected name

Run the read-only checks first:

    .\\venv\\Scripts\\python.exe smoke_forven.py

Then, only when those pass, the write round-trip (this creates a real reel in
the STAGING tenant):

    .\\venv\\Scripts\\python.exe smoke_forven.py --write path\\to\\short.mp4
"""

import argparse
import os
import sys

import forven_api
import forven_config


def _ok(message):
    print(f"  PASS  {message}")


def _fail(message):
    print(f"  FAIL  {message}")


def check_destination():
    """Staging reachable, flag on, and pointing at the org we mean."""
    print("\n[1] Staging: reachability, feature flag, tenant echo")
    try:
        config = forven_config.destination_config()
    except forven_config.ConfigError as exc:
        _fail(f"{exc}  (set the FORVEN_STAGING_* variables)")
        return None

    client = forven_api.ForvenClient(config.base_url, config.api_key)
    expected = os.environ.get("FORVEN_STAGING_TENANT_NAME", "")

    try:
        page = client.list_interviews(config.tenant_public_id, page_size=1)
    except forven_api.NotVisibleError:
        _fail("404 - the feature flag is probably OFF in staging, or the tenant id is unknown.")
        print("        Ask the operator before debugging the client.")
        return None
    except forven_api.AuthError as exc:
        _fail(f"401 - staging key rejected: {exc}")
        return None

    print(f"        tenant echo: {page.tenant_name!r}")
    if expected and page.tenant_name != expected:
        _fail(f"echo is {page.tenant_name!r}, expected {expected!r} - WRONG TENANT")
        return None
    _ok("staging reachable, flag on, tenant matches")
    return client, config


def check_source():
    """Production reachable, and the key can actually download."""
    print("\n[2] Production: listing and allow_download")
    try:
        config = forven_config.source_config()
    except forven_config.ConfigError as exc:
        _fail(f"{exc}  (set the FORVEN_PROD_* variables)")
        return None

    client = forven_api.ForvenClient(config.base_url, config.api_key)
    expected = os.environ.get("FORVEN_PROD_TENANT_NAME", "")

    try:
        page = client.list_interviews(config.tenant_public_id, page_size=5)
    except forven_api.NotVisibleError:
        _fail("404 - feature flag off in production, or unknown tenant id.")
        return None
    except forven_api.AuthError as exc:
        _fail(f"401 - production key rejected: {exc}")
        return None

    print(f"        tenant echo: {page.tenant_name!r}")
    if expected and page.tenant_name != expected:
        _fail(f"echo is {page.tenant_name!r}, expected {expected!r} - WRONG TENANT")
        return None
    if not page.rows:
        _fail("no interviews visible to this tenant - nothing to smoke against")
        return None
    _ok(f"{len(page.rows)} interview(s) visible")

    ref = page.rows[0]["interview_ref"]
    print(f"        using interview_ref {ref}")

    try:
        detail = client.get_transcript(config.tenant_public_id, ref)
    except forven_api.ForvenApiError as exc:
        _fail(f"transcript fetch failed: {exc}")
        return None
    entries = detail.get("transcript_entries") or []
    _ok(f"transcript fetched: status={detail.get('transcript_status')}, {len(entries)} entries")

    if entries:
        # Verify the adapter produces the contract shape WITHOUT printing
        # participant speech: transcript content is research material and does
        # not get copied into logs, tickets, or AI tools.
        contract_lines = forven_api.entries_to_contract_text(entries).splitlines()
        shaped = sum(1 for line in contract_lines
                     if line.startswith("[") and "] " in line and ": " in line)
        _ok(f"adapter produced {shaped}/{len(contract_lines)} well-formed contract lines "
            f"(content withheld by design)")

    try:
        link = client.media_link(config.tenant_public_id, ref, disposition="attachment")
    except forven_api.CapabilityError as exc:
        _fail(f"403 - the production key lacks allow_download: {exc}")
        return None
    _ok(f"downloadable media link minted, expires {link.get('expires_at')}")

    return client, config, ref


def check_round_trip(destination, source_ref, reel_path):
    """Upload and register a real reel into the STAGING tenant."""
    print("\n[3] Staging: reel round-trip (this creates a real reel)")
    client, config = destination

    import forven_deliver

    try:
        result = forven_deliver.deliver(
            client,
            tenant_public_id=config.tenant_public_id,
            reel_path=reel_path,
            title="Integration smoke test",
            duration_seconds=5,
            source_interview_refs=[source_ref],
            metadata={"smoke": True},
        )
    except forven_api.CapabilityError as exc:
        _fail(f"403 - the staging key lacks allow_upload_reels: {exc}")
        return
    except Exception as exc:
        _fail(f"{type(exc).__name__}: {exc}")
        return

    _ok(f"registered reel_ref={result.get('reel_ref')} public_id={result.get('reel_public_id')}")
    print("        Confirm it appears under 'Shared With Us' in the staging tenant.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", metavar="REEL_MP4",
                        help="also run the write round-trip using this short mp4")
    args = parser.parse_args(argv)

    print("Forven Video Access API - smoke test")

    destination = check_destination()
    source = check_source()

    if not (destination and source):
        print("\nRead-only checks did not all pass. Fix those before writing anything.")
        return 1

    if args.write:
        if not os.path.exists(args.write):
            _fail(f"no such file: {args.write}")
            return 1
        check_round_trip(destination, source[2], args.write)
    else:
        print("\nRead-only checks passed. Re-run with --write <short.mp4> for the round-trip.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Postgres-backed state for Sizzle Reel.

Replaces the JSON blobs (sizzle_library.json, forven_ingested.json) with real
tables. Object storage keeps the bytes - interview media, cut reels, captions -
and this keeps the facts about them: which reels exist, what they are made of,
what has been ingested, what has been delivered back to Forven.

Every table is prefixed sz_ so Sizzle Reel's data is unmistakable in a database
shared with the platform.

Keyed by interview_ref, not by any internal id. Interviews live in Forven, not
here; the ref is the identifier Forven gives out and the one its reel-register
call wants back as source_interview_refs.

Raw psycopg2 with parameterised SQL, matching this repo's lightweight style -
no ORM, and never string-formatted SQL.
"""

import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

# A connection per operation is fine against a local database and hopeless
# against a remote one - a handful of calls exhausts the instance's connection
# allowance and starts timing out. Pool instead, created lazily so importing
# this module costs nothing.
POOL_MIN = 1
POOL_MAX = int(os.environ.get("SZ_DB_POOL_MAX", "5"))

_pool = None
_pool_url = None
_pool_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sz_reels (
    id                    BIGSERIAL PRIMARY KEY,
    public_id             UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    session_key           TEXT,
    title                 TEXT,
    prompt                TEXT,
    media_key             TEXT,
    captions_key          TEXT,
    duration_seconds      INTEGER,
    clip_count            INTEGER NOT NULL DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'draft',
    forven_reel_ref       TEXT,
    forven_reel_public_id TEXT,
    delivered_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sz_tenant_pairs (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    source_env          TEXT NOT NULL DEFAULT 'production',
    source_tenant_id    TEXT NOT NULL,
    source_tenant_name  TEXT,
    dest_env            TEXT NOT NULL DEFAULT 'staging',
    dest_tenant_id      TEXT NOT NULL,
    dest_tenant_name    TEXT,
    is_default          BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE sz_reels ADD COLUMN IF NOT EXISTS tenant_pair_id BIGINT
    REFERENCES sz_tenant_pairs(id);

CREATE TABLE IF NOT EXISTS sz_reel_clips (
    id             BIGSERIAL PRIMARY KEY,
    reel_id        BIGINT NOT NULL REFERENCES sz_reels(id) ON DELETE CASCADE,
    interview_ref  TEXT NOT NULL,
    position       INTEGER NOT NULL,
    start_seconds  DOUBLE PRECISION NOT NULL,
    end_seconds    DOUBLE PRECISION NOT NULL,
    excerpt        TEXT,
    UNIQUE (reel_id, position)
);

CREATE TABLE IF NOT EXISTS sz_ingested_interviews (
    interview_ref     TEXT PRIMARY KEY,
    tenant_public_id  TEXT NOT NULL,
    session_key       TEXT NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    aligned_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_sz_reel_clips_ref ON sz_reel_clips (interview_ref);
CREATE INDEX IF NOT EXISTS ix_sz_reels_status ON sz_reels (status);
"""

STATUS_DRAFT = "draft"
STATUS_GENERATING = "generating"
STATUS_READY = "ready"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"


class StoreNotConfigured(RuntimeError):
    """DATABASE_URL is not set."""


def database_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def is_configured() -> bool:
    return bool(database_url())


def _get_pool():
    """The shared pool, rebuilt if DATABASE_URL changed under us."""
    global _pool, _pool_url
    url = database_url()
    if not url:
        raise StoreNotConfigured("DATABASE_URL is not set.")
    if _pool is not None and _pool_url == url:
        return _pool
    with _pool_lock:
        if _pool is None or _pool_url != url:
            if _pool is not None:
                _pool.closeall()
            _pool = psycopg2.pool.ThreadedConnectionPool(POOL_MIN, POOL_MAX, url)
            _pool_url = url
    return _pool


def close_pool() -> None:
    """Drop every pooled connection. For shutdown and for tests."""
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
        _pool = None
        _pool_url = None


@contextmanager
def connection():
    """A pooled, committed connection - rolled back if the block raises.

    The connection returns to the pool either way; it is never closed, which is
    the point.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def cursor():
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur


def init_schema() -> None:
    """Create the sz_ tables if they are absent. Safe to run repeatedly."""
    with cursor() as cur:
        cur.execute(SCHEMA)


# --- the ingest index -------------------------------------------------------

def already_ingested() -> set:
    """Refs pulled before, so a sync can skip them."""
    with cursor() as cur:
        cur.execute("SELECT interview_ref FROM sz_ingested_interviews")
        return {row["interview_ref"] for row in cur.fetchall()}


def record_ingested(refs, *, tenant_public_id: str, session_key: str) -> int:
    """Note which interviews were pulled, and where they landed.

    Idempotent: re-ingesting a ref updates where it lives rather than failing,
    because a later pull genuinely supersedes the earlier one.
    """
    rows = [(ref, tenant_public_id, session_key) for ref in refs]
    if not rows:
        return 0
    with cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO sz_ingested_interviews
                   (interview_ref, tenant_public_id, session_key)
               VALUES %s
               ON CONFLICT (interview_ref) DO UPDATE
                   SET session_key = EXCLUDED.session_key,
                       ingested_at = now(),
                       aligned_at  = NULL""",
            rows,
        )
        return len(rows)


def mark_aligned(refs) -> int:
    with cursor() as cur:
        cur.execute(
            "UPDATE sz_ingested_interviews SET aligned_at = now() "
            "WHERE interview_ref = ANY(%s)",
            (list(refs),),
        )
        return cur.rowcount


# --- reels ------------------------------------------------------------------

def create_reel(*, title: str, prompt: str, session_key: str, clips,
                tenant_pair_id: int | None = None) -> dict:
    """Record a reel and its clips. Returns the reel row.

    ``clips`` are dicts with interview_ref, start_seconds, end_seconds and an
    optional excerpt, in the order they appear in the cut. Position is assigned
    here so callers cannot number them inconsistently.
    """
    clips = list(clips)
    if not clips:
        raise ValueError("a reel needs at least one clip")

    with cursor() as cur:
        cur.execute(
            """INSERT INTO sz_reels
                   (title, prompt, session_key, clip_count, status, tenant_pair_id)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (title, prompt, session_key, len(clips), STATUS_DRAFT, tenant_pair_id),
        )
        reel = cur.fetchone()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO sz_reel_clips
                   (reel_id, interview_ref, position, start_seconds, end_seconds, excerpt)
               VALUES %s""",
            [
                (reel["id"], clip["interview_ref"], position,
                 float(clip["start_seconds"]), float(clip["end_seconds"]),
                 clip.get("excerpt"))
                for position, clip in enumerate(clips)
            ],
        )
        return dict(reel)


def set_media(reel_id: int, *, media_key: str, duration_seconds: int | None = None,
              captions_key: str | None = None) -> None:
    """Record where the cut landed in object storage."""
    with cursor() as cur:
        cur.execute(
            """UPDATE sz_reels
                  SET media_key = %s,
                      duration_seconds = COALESCE(%s, duration_seconds),
                      captions_key = COALESCE(%s, captions_key),
                      status = %s
                WHERE id = %s""",
            (media_key, duration_seconds, captions_key, STATUS_READY, reel_id),
        )


def mark_delivered(reel_id: int, *, reel_ref: str, reel_public_id: str) -> None:
    """Record what Forven called this reel once it was registered."""
    with cursor() as cur:
        cur.execute(
            """UPDATE sz_reels
                  SET forven_reel_ref = %s,
                      forven_reel_public_id = %s,
                      delivered_at = now(),
                      status = %s
                WHERE id = %s""",
            (reel_ref, reel_public_id, STATUS_DELIVERED, reel_id),
        )


def set_status(reel_id: int, status: str) -> None:
    with cursor() as cur:
        cur.execute("UPDATE sz_reels SET status = %s WHERE id = %s", (status, reel_id))


def get_reel(reel_id: int) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM sz_reels WHERE id = %s", (reel_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def reel_clips(reel_id: int) -> list:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM sz_reel_clips WHERE reel_id = %s ORDER BY position",
            (reel_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def list_reels(limit: int = 100) -> list:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM sz_reels ORDER BY created_at DESC LIMIT %s", (limit,)
        )
        return [dict(row) for row in cur.fetchall()]


def source_refs(reel_id: int) -> list:
    """The interviews a reel quotes, for register's source_interview_refs.

    Must be EXACTLY the interviews in the finished cut: extra refs wrongly
    restrict where the reel may be shown, missing ones break Forven's
    participant-erasure index.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT interview_ref FROM sz_reel_clips WHERE reel_id = %s "
            "ORDER BY interview_ref",
            (reel_id,),
        )
        return [row["interview_ref"] for row in cur.fetchall()]


def reels_quoting(interview_ref: str) -> list:
    """Reels containing footage from this interview - for erasure."""
    with cursor() as cur:
        cur.execute(
            """SELECT DISTINCT r.* FROM sz_reels r
                 JOIN sz_reel_clips c ON c.reel_id = r.id
                WHERE c.interview_ref = %s""",
            (interview_ref,),
        )
        return [dict(row) for row in cur.fetchall()]


# --- tenant pairs -----------------------------------------------------------
#
# WHOSE interviews and WHERE the reels go is the thing that varies, so it is
# data rather than deployment configuration. Only the API keys stay in the
# environment: those are the application's credentials, one per Forven
# environment, and they are secrets.

def list_tenant_pairs() -> list:
    with cursor() as cur:
        cur.execute("SELECT * FROM sz_tenant_pairs ORDER BY is_default DESC, name")
        return [dict(row) for row in cur.fetchall()]


def get_tenant_pair(pair_id: int) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM sz_tenant_pairs WHERE id = %s", (pair_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def default_tenant_pair() -> dict | None:
    """The pair the UI offers first. Falls back to the only one, if there is one."""
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM sz_tenant_pairs ORDER BY is_default DESC, id LIMIT 1"
        )
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_tenant_pair(*, name: str, source_tenant_id: str, dest_tenant_id: str,
                       source_env: str = "production", dest_env: str = "staging",
                       source_tenant_name: str | None = None,
                       dest_tenant_name: str | None = None,
                       is_default: bool = False) -> dict:
    """Add or update a pair by name. Setting a default clears the others."""
    with cursor() as cur:
        cur.execute(
            """INSERT INTO sz_tenant_pairs
                   (name, source_env, source_tenant_id, source_tenant_name,
                    dest_env, dest_tenant_id, dest_tenant_name, is_default)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET
                   source_env = EXCLUDED.source_env,
                   source_tenant_id = EXCLUDED.source_tenant_id,
                   source_tenant_name = EXCLUDED.source_tenant_name,
                   dest_env = EXCLUDED.dest_env,
                   dest_tenant_id = EXCLUDED.dest_tenant_id,
                   dest_tenant_name = EXCLUDED.dest_tenant_name,
                   is_default = EXCLUDED.is_default
               RETURNING *""",
            (name, source_env, source_tenant_id, source_tenant_name,
             dest_env, dest_tenant_id, dest_tenant_name, is_default),
        )
        pair = dict(cur.fetchone())
        if is_default:
            cur.execute(
                "UPDATE sz_tenant_pairs SET is_default = false WHERE id <> %s",
                (pair["id"],),
            )
        return pair


def delete_tenant_pair(pair_id: int) -> int:
    with cursor() as cur:
        cur.execute("DELETE FROM sz_tenant_pairs WHERE id = %s", (pair_id,))
        return cur.rowcount

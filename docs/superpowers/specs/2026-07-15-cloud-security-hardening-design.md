# Cloud Security Hardening — Design Spec
**Date:** 2026-07-15

> ## ⚠️ STATUS: AUTH + TENANCY DEFERRED (2026-07-15)
>
> This spec describes the **full** vision: per-client login, per-user libraries,
> and prefix-scoped sessions. It was implemented and merged, then **rolled back
> by owner decision** — the product should keep its original flow (no login
> screen, no gating) for now. This document is retained as the blueprint for
> switching it on later.
>
> **What is currently LIVE on `master`** (the login-free, backend-only subset):
> - **Rate limiting** (flask-limiter, keyed by client IP, cloud-only) on
>   `/analyze`, `/upload`, `/upload/prepare`, `/generate`, `/plan`.
> - **`MAX_CONTENT_LENGTH`** 50 MB body cap on both services.
> - **CORS** restricted to `ALLOWED_ORIGINS` on the generator (permissive if unset).
> - **Path-traversal guard** (`_valid_session_folder` in `app.py` / the
>   `sessions/` prefix check in `generator_app.py`) — closes audit finding #5
>   without needing a user identity.
>
> **What is NOT live (deferred, described below):** Bearer-token auth wall
> (`auth.py`), the `/login` endpoint, operator user provisioning
> (`manage_users.py`), per-user libraries, and prefix-scoped *per-user* sessions
> (audit findings #1, #3, and the per-tenant half of #4). All of this is
> preserved in git history under the reverted merge
> `Merge harden/cloud-security …` and its revert commit — re-apply by following
> the implementation plan at
> [../plans/2026-07-15-cloud-security-hardening.md](../plans/2026-07-15-cloud-security-hardening.md)
> (Phases 1 and 2). Findings #2 and the traversal part of #5 are already covered
> by the live subset above.

## Overview

The cloud deployment (`APP_MODE=cloud`, two public Render services `sizzle-app` and
`sizzle-generator`) currently has **no authentication, no tenancy, and no rate
limiting**. Anyone who finds the URLs can spend the Anthropic API budget, fill the R2
bucket, and read/delete/edit every user's reels via one shared library. This spec
covers remediation of the **Critical** and **High** findings from the 2026-07-15
security audit:

- **#1** No authentication on either public service.
- **#2** Unauthenticated, unthrottled Anthropic spend + unbounded uploads.
- **#3** Global shared library → cross-tenant read/delete/edit + IDOR.
- **#4** Wildcard CORS + no auth → cross-site API abuse / CSRF.
- **#5** `folder`/`session_key` isolation bypass (path traversal) in cloud mode.

**Requirement decisions (from the user):**
- **Hard multi-tenancy** — separate clients must not see each other's data,
  including **separate per-user libraries**.
- **In-app rate limiting** — enforced in the Flask apps (flask-limiter), not at a
  proxy.

## Guiding Constraint: local mode stays zero-auth

Everything in this spec activates **only when `APP_MODE=cloud`**. Local mode is a
single-user desktop app (Flask binds to `127.0.0.1`, the operator is the trusted
user). In local mode:
- The auth `before_request` guard returns early (no token required).
- Library and session keys keep their current unprefixed form.
- Rate limits are not applied (or set effectively unlimited).

This keeps the existing test suite (which runs in local/testing mode) and the local
generate/analyze workflow working unchanged. Any signature change (e.g.
`library_key(user_id)`) must default to today's behaviour when `user_id` is absent.

## Architecture

Two separate services on different origins rule out plain Flask session cookies.
Auth is **stateless signed Bearer tokens** validated independently by each service
using a shared `SECRET_KEY`. Tenancy is **key-prefix scoping + per-user library
JSON** — no database is introduced.

```
Browser ──login(user/pass)──▶ sizzle-app  POST /login ──▶ returns signed token
   │                                                        (itsdangerous, SECRET_KEY)
   │  Authorization: Bearer <token>  (every request, both services)
   ├──────────────────────────────▶ sizzle-app       (before_request verifies token)
   └──────────────────────────────▶ sizzle-generator (before_request verifies token)
                                     WS: /ws/job/<id>?token=<token>
```

### New shared module: `auth.py`

Imported by both `app.py` and `generator_app.py`.

- `SECRET_KEY` from env (`SIZZLE_SECRET_KEY`). **Required in cloud** — the app must
  refuse to start in cloud mode if it is unset (fail closed). Both services must be
  configured with the *same* value.
- `make_token(user_id: str) -> str` — `itsdangerous.URLSafeTimedSerializer` signs
  `{"uid": user_id}`. `itsdangerous` is already a Flask dependency (rung 5: no new
  dep).
- `verify_token(token: str) -> str | None` — returns `user_id` or `None`, enforcing a
  max age (`TOKEN_MAX_AGE_SECONDS`, default 7 days).
- `require_auth()` — a `before_request` callback:
  - In local mode (`not storage.is_cloud()`): return `None` (allow).
  - Allowlist a small set of unauthenticated routes: `POST /login`, static assets,
    and any health check. Everything else requires a valid Bearer token.
  - On success, set `g.user_id`. On failure, return `401`.
  - The WebSocket route reads the token from the `?token=` query string (headers are
    impractical for WS) and validates it the same way; invalid → close.
- `current_user_prefix() -> str` — `f"users/{g.user_id}"` in cloud, `""` in local.

### User store + login (main app only)

- Credentials live in storage at `auth/users.json`:
  `{ "<user_id>": "<pbkdf2 hash>" }`, hashed with `werkzeug.security.generate_password_hash`
  (also already installed). Reading it uses the existing `storage.read_json`. The
  generator service **never** reads this file — it only verifies token signatures.
- `POST /login` — body `{user_id, password}`. Verifies with
  `werkzeug.security.check_password_hash`; on success returns `{"token": ...}`.
  Hard rate-limited (see below). Generic error message on failure (no user
  enumeration).
- **Operator provisioning CLI** `manage_users.py`:
  - `add <user_id>` (prompts for password, writes pbkdf2 hash to `auth/users.json`
    via `storage`).
  - `remove <user_id>`.
  - `set-password <user_id>`.
  - `list`.
  - Optional `assign-library <user_id>` — one-time migration that copies the current
    global `library/sizzle_library.json` into `users/<user_id>/library.json`
    (see *Existing data* below).
  - Clients are **operator-provisioned, never self-registered** — there is no public
    signup endpoint. Adding a client does not require a redeploy (the file lives in
    R2 and is read per-login).
- Logout is client-side only (drop the token). No server-side revocation list in v1
  (YAGNI); token expiry bounds exposure.

### Tenancy scoping

**Sessions / uploads.** `storage.new_session_key()` becomes
`new_session_key(user_id=None)`:
- cloud: `users/<user_id>/sessions/<uuid>`
- local / no user_id: `sessions/<uuid>` (unchanged).

Every endpoint that accepts a `folder` or `session_key` from the client must, in
cloud mode, **verify it starts with the caller's prefix** (`users/<g.user_id>/`)
before using it; otherwise return `403`. This single guard closes **#5** (the
traversal/isolation bypass, where passing a real server path skipped the
session-download branch) *and* scopes sessions per user. Applies to: `/load-folder`,
`/transcripts`, `/analyze` (main app) and `/generate`, `/plan` (generator).

**Per-user library.** The single global `sizzle_library.json` /
`library/sizzle_library.json` splits into one file per user:
- `storage.library_key(user_id=None)` → `users/<user_id>/library.json` in cloud;
  the current constant in local.
- `storage.load_library(user_id=None)` and the generator's
  `_load_library(user_id)` / `_save_library(user_id, entries)` take `user_id`,
  sourced from `g.user_id`.
- All library-touching endpoints operate only on the caller's file: `GET /library`,
  `DELETE /library/<id>`, `PATCH /library/<id>`, `GET /library-video/<id>`,
  `GET /library-captions/<id>`, `POST /library`, `POST /library/<id>/download-captioned`.
  An id not present in the caller's library returns `404`. This closes **#3**
  (cross-tenant read/delete/edit and IDOR) — ownership is enforced implicitly by
  "is this id in *your* library file".

**The request-context gotcha (critical implementation detail).** Reels are written to
the library from `_run_generation`, which runs on a **daemon thread with no Flask
request context** — `g.user_id` does not exist there. The `/generate` and `/plan`
handlers (and the async `/load-folder` download thread in the main app) must read
`g.user_id` **at request time** and pass it **explicitly** into the worker
(`_run_generation(..., user_id=...)`, `_build_segment_list` callers, WS token check).
Missing this either crashes the worker or writes reels into the wrong library.

**Existing data.** The current global `library/sizzle_library.json` is shared test
data. Default: leave it orphaned (new writes go per-user). If preservation is wanted,
`manage_users.py assign-library <user_id>` performs a one-time copy into that user's
file. No automatic migration runs.

### Rate limiting + upload caps

- Add `flask-limiter` (new dependency — the only one) to both services.
- Key function: `g.user_id` when authenticated, else remote IP.
- Limits (tunable constants):
  - `POST /login` — strict, e.g. `5/minute` per IP (brute-force + enumeration).
  - `POST /analyze`, `POST /generate`, `POST /plan` — protect the Anthropic bill and
    CPU/OOM, e.g. `10/minute; 100/hour` per user.
  - `POST /upload`, `/upload/prepare`, `/upload/commit` — e.g. `30/minute` per user.
  - A generous global default on everything else.
- `app.config["MAX_CONTENT_LENGTH"]` on both apps caps the app-proxied `/upload` body
  and JSON bodies (returns `413`).
- **Known ceiling (mark with `ponytail:` comment):** the cloud fast-path uploads
  browser→R2 directly via presigned PUT, so the app cannot cap *those* bytes.
  Mitigate abuse with a per-user active-session cap and rely on R2 lifecycle/quota as
  the real backstop; document as the upgrade path.
- **Known ceiling (mark with `ponytail:` comment):** flask-limiter default in-memory
  storage is per-instance and resets on restart — fine on Render's single free-tier
  instance; upgrade path is Redis storage if the service scales to multiple
  instances.

### CORS tightening

- Replace `CORS(app)` (wildcard) on the generator, and any wildcard on the main app,
  with an explicit allowed-origins list (the Render frontend origin, plus
  `http://localhost:*` for dev), allowing the `Authorization` header. Origins come
  from an env var (`ALLOWED_ORIGINS`) so they are configurable without code change.
- Note: Bearer-token auth (not cookies) already neutralises the CSRF vector (#4);
  tightening CORS is defence-in-depth.
- `set_cors.py` R2 rule 1 (`AllowedOrigins: ["*"]` for GET) may stay — R2 objects are
  only reachable via server-minted presigned/signed URLs — but is called out as a
  low-priority narrowing opportunity, not part of this work.

### Frontend (`static/app.js`, `templates/index.html`)

- A login screen shown (cloud mode only) before the folder-picker when no valid token
  is held. `app_mode` is already injected into the page.
- Store the token in `sessionStorage`; attach `Authorization: Bearer <token>` to
  every `fetch` to **both** services, and append `?token=<token>` to the WebSocket
  URL.
- On any `401`, drop the token and return to the login screen.
- Local mode: no login screen; behaviour unchanged.

## Data Flow (cloud, after changes)

1. Browser → `POST /login` → token.
2. Browser → `POST /upload/prepare` (Bearer) → server mints `users/<uid>/sessions/<uuid>`
   presigned PUT URLs → browser uploads to R2 → `POST /upload/commit`.
3. Browser → `POST /load-folder` / `/analyze` (Bearer, `folder` = the user-prefixed
   session key; guard verifies prefix).
4. Browser → `POST /generate` or `/plan` (Bearer). Handler captures `g.user_id`,
   passes it into the worker; finished reel + library entry written to
   `users/<uid>/library.json`.
5. Browser → `GET /library` (Bearer) → only this user's entries. Playback via
   `/library-video/<id>` resolves the id within this user's library.

## Error Handling

- Missing `SIZZLE_SECRET_KEY` in cloud → refuse to start (fail closed).
- Missing/invalid/expired token → `401` (WS: close).
- `folder`/`session_key` outside the caller's prefix → `403`.
- Library id not in caller's library → `404` (indistinguishable from truly missing;
  no cross-tenant existence oracle).
- Over-limit → `429`. Over-size body → `413`.
- Login failure → generic `401`, no user enumeration.

## Testing

New tests (in addition to keeping the existing suite green in local/testing mode):

- **auth.py:** token round-trip, expiry rejection, tampered-token rejection.
- **login:** correct password → token; wrong password → 401; unknown user → 401.
- **guard:** cloud request without token → 401; with valid token → allowed; local
  mode → allowed without token.
- **tenancy — sessions:** `folder`/`session_key` outside caller prefix → 403;
  a real server path in cloud mode → 403 (regression test for #5).
- **tenancy — library:** user A cannot GET/DELETE/PATCH user B's entry (404);
  `/library` returns only the caller's entries; generated reel lands in the caller's
  library (covers the request-context handoff into the worker thread).
- **rate limiting:** exceeding the `/login` and `/analyze` limits → 429.
- **upload cap:** body over `MAX_CONTENT_LENGTH` → 413.

Continue mocking `_library_add` / `_save_library` in generate-flow tests so pytest
never writes real library files, per the existing test invariant.

## Phasing

Each phase is independently shippable and testable.

1. **Auth wall** — `auth.py`, `SECRET_KEY` startup check, `POST /login`,
   `manage_users.py`, `before_request` guards on both services, WS token check,
   frontend login + Bearer plumbing. Fixes **#1**, **#4**, and the anonymous half of
   **#2**.
2. **Tenancy** — prefix-scoped session keys + per-user library files, including the
   worker-thread `user_id` handoff. Fixes **#3**, **#5**.
3. **Rate limits + caps** — flask-limiter + `MAX_CONTENT_LENGTH`. Fixes the remainder
   of **#2**.
4. **CORS tightening** — explicit origins on both services.

## Out of Scope (deferred / lower severity)

- Medium/Low findings from the audit: production WSGI server swap (gunicorn),
  `_jobs` dict eviction, `/find-local-folder` oracle, `debug=True` in `__main__`
  blocks, security headers/CSP, R2 GET-CORS narrowing. Track separately.
- Self-service registration, password reset, MFA, server-side token revocation.

## New / Changed Files

- **New:** `auth.py`, `manage_users.py`, spec + plan docs, new test modules.
- **Changed:** `storage.py` (`library_key`, `load_library`, `new_session_key` take
  `user_id`), `app.py` + `generator_app.py` (guards, login, prefix checks, per-user
  library, worker `user_id` handoff, limiter, CORS, MAX_CONTENT_LENGTH),
  `static/app.js` + `templates/index.html` (login + token), `requirements.txt`
  (`flask-limiter`), `render.yaml` / `docker-compose.yml` / Dockerfiles
  (`SIZZLE_SECRET_KEY`, `ALLOWED_ORIGINS` env vars).

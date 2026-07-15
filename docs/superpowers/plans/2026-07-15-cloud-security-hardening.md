# Cloud Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authentication, per-client tenancy, rate limiting, and CORS lockdown to the cloud deployment so the two public Render services are no longer open to anonymous abuse or cross-tenant data access.

**Architecture:** Stateless signed Bearer tokens (itsdangerous, shared `SIZZLE_SECRET_KEY`) verified independently by both Flask services. Tenancy via key-prefix scoping (`users/<uid>/…`) plus per-user library JSON — no database. flask-limiter for in-process rate limits. Everything activates only when `APP_MODE=cloud`; local mode stays zero-auth and unchanged.

**Tech Stack:** Python 3.11, Flask, itsdangerous + werkzeug.security (ship with Flask), flask-limiter (new dep), boto3/R2, vanilla JS frontend.

**Spec:** [docs/superpowers/specs/2026-07-15-cloud-security-hardening-design.md](../specs/2026-07-15-cloud-security-hardening-design.md)

**Run tests with:** `.\venv\Scripts\python.exe -m pytest tests/ -v` (PowerShell). Run single: `.\venv\Scripts\python.exe -m pytest tests/test_auth.py -v`.

---

## Phase 1 — Auth wall

Fixes audit findings **#1** (no auth), **#4** (CSRF/CORS abuse — Bearer tokens neutralise it), and the anonymous half of **#2**.

### Task 1: `auth.py` — token mint/verify

**Files:**
- Create: `auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
"""Tests for auth.py — stateless signed Bearer tokens."""
import time
import pytest


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "test-secret-key-do-not-use-in-prod")
    import importlib, auth
    importlib.reload(auth)
    return auth


def test_token_roundtrip(_secret):
    token = _secret.make_token("clientA")
    assert _secret.verify_token(token) == "clientA"


def test_tampered_token_rejected(_secret):
    token = _secret.make_token("clientA")
    assert _secret.verify_token(token + "x") is None


def test_expired_token_rejected(_secret, monkeypatch):
    token = _secret.make_token("clientA")
    # Force max age to 0 so any elapsed time expires it.
    monkeypatch.setattr(_secret, "TOKEN_MAX_AGE_SECONDS", 0)
    time.sleep(1)
    assert _secret.verify_token(token) is None


def test_verify_none_and_garbage(_secret):
    assert _secret.verify_token("") is None
    assert _secret.verify_token("not.a.token") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 3: Write minimal implementation**

```python
# auth.py
"""Stateless signed Bearer-token auth, shared by app.py and generator_app.py.

Local mode (APP_MODE != cloud) is a single-user desktop app and needs no auth;
the request guard returns early there. In cloud mode every request outside a small
allowlist must carry a valid `Authorization: Bearer <token>` header (or, for the
WebSocket, a `?token=` query param). Both services validate tokens with the same
SIZZLE_SECRET_KEY — no shared session store.
"""
import os
from flask import g, request, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import storage

TOKEN_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days
_SALT = "sizzle-auth-token"


def _secret_key() -> str:
    key = os.environ.get("SIZZLE_SECRET_KEY")
    if not key:
        # Fail closed in cloud; local mode never mints/verifies tokens.
        if storage.is_cloud():
            raise RuntimeError(
                "SIZZLE_SECRET_KEY is required when APP_MODE=cloud (auth cannot start)"
            )
        return "local-mode-unused"
    return key


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt=_SALT)


def make_token(user_id: str) -> str:
    return _serializer().dumps({"uid": user_id})


def verify_token(token: str) -> str | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired, Exception):
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return uid or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_auth.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add auth.py tests/test_auth.py
git commit -m "feat(auth): signed Bearer token mint/verify (auth.py)"
```

---

### Task 2: `auth.py` — request guard + user prefix

**Files:**
- Modify: `auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_auth.py
from flask import Flask, g


def _guarded_app(auth):
    app = Flask(__name__)
    app.before_request(auth.require_auth)

    @app.get("/login")           # allowlisted route name check uses path
    def login():
        return "login-ok"

    @app.get("/secret")
    def secret():
        return g.user_id

    return app


def test_guard_allows_local_mode(monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)  # local
    import importlib, storage, auth
    importlib.reload(storage); importlib.reload(auth)
    app = _guarded_app(auth)
    with app.test_client() as c:
        assert c.get("/secret").status_code == 200  # no token needed locally


def test_guard_blocks_missing_token_in_cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth
    importlib.reload(storage); importlib.reload(auth)
    app = _guarded_app(auth)
    with app.test_client() as c:
        assert c.get("/secret").status_code == 401
        assert c.get("/login").status_code == 200  # allowlisted


def test_guard_accepts_valid_token_in_cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth
    importlib.reload(storage); importlib.reload(auth)
    app = _guarded_app(auth)
    token = auth.make_token("clientA")
    with app.test_client() as c:
        r = c.get("/secret", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.data.decode() == "clientA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_auth.py -k guard -v`
Expected: FAIL with `AttributeError: module 'auth' has no attribute 'require_auth'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to auth.py

# Paths reachable without a token (exact match on request.path).
# "/" is the public page shell (no data) — it must load so the login form can
# render; "/login" issues the token. Everything else requires auth in cloud mode.
_ALLOWLIST = {"/", "/login"}


def _bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    # WebSocket upgrade requests can't set headers conveniently — accept ?token=
    return request.args.get("token")


def require_auth():
    """before_request callback. Returns None to allow, or a 401 response to block."""
    if not storage.is_cloud():
        return None
    if request.path in _ALLOWLIST or request.path.startswith("/static/"):
        return None
    if request.method == "OPTIONS":   # CORS preflight carries no auth header
        return None
    uid = verify_token(_bearer_token())
    if not uid:
        return jsonify({"error": "authentication required"}), 401
    g.user_id = uid
    return None


def current_user_prefix() -> str:
    """`users/<uid>` in cloud (with a resolved g.user_id), else ''."""
    if not storage.is_cloud():
        return ""
    return f"users/{g.user_id}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_auth.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add auth.py tests/test_auth.py
git commit -m "feat(auth): request guard + current_user_prefix"
```

---

### Task 3: `manage_users.py` — provisioning CLI + user store

**Files:**
- Create: `manage_users.py`
- Test: `tests/test_manage_users.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manage_users.py
"""Tests for the operator user-provisioning helpers."""
import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, manage_users
    importlib.reload(storage); importlib.reload(manage_users)
    return manage_users


def test_add_and_verify(cloud, monkeypatch):
    store = {}
    monkeypatch.setattr(cloud.storage, "read_json", lambda k: store.get(k, {}))
    monkeypatch.setattr(cloud.storage, "write_json",
                        lambda k, d: store.__setitem__(k, d))
    cloud.add_user("clientA", "s3cret")
    assert cloud.verify_user("clientA", "s3cret") is True
    assert cloud.verify_user("clientA", "wrong") is False
    assert cloud.verify_user("ghost", "x") is False


def test_hash_is_not_plaintext(cloud, monkeypatch):
    store = {}
    monkeypatch.setattr(cloud.storage, "read_json", lambda k: store.get(k, {}))
    monkeypatch.setattr(cloud.storage, "write_json",
                        lambda k, d: store.__setitem__(k, d))
    cloud.add_user("clientA", "s3cret")
    assert "s3cret" not in str(store)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_manage_users.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manage_users'`

- [ ] **Step 3: Write minimal implementation**

```python
# manage_users.py
"""Operator CLI + helpers for the client user store (auth/users.json in storage).

Clients are provisioned by the operator, never self-registered. Passwords are
stored as werkzeug pbkdf2 hashes. Usage:

    python manage_users.py add <user_id>
    python manage_users.py set-password <user_id>
    python manage_users.py remove <user_id>
    python manage_users.py list
    python manage_users.py assign-library <user_id>   # one-time legacy migration
"""
import getpass
import sys

from werkzeug.security import generate_password_hash, check_password_hash

import storage

USERS_KEY = "auth/users.json"


def _load() -> dict:
    data = storage.read_json(USERS_KEY)
    return data if isinstance(data, dict) else {}


def _save(users: dict) -> None:
    storage.write_json(USERS_KEY, users)


def add_user(user_id: str, password: str) -> None:
    users = _load()
    users[user_id] = generate_password_hash(password)
    _save(users)


def remove_user(user_id: str) -> None:
    users = _load()
    users.pop(user_id, None)
    _save(users)


def verify_user(user_id: str, password: str) -> bool:
    users = _load()
    h = users.get(user_id)
    return bool(h) and check_password_hash(h, password)


def assign_library(user_id: str) -> None:
    """Copy the legacy global library into this user's per-user library file."""
    legacy = storage.read_json("library/sizzle_library.json")
    storage.write_json(f"users/{user_id}/library.json", legacy or [])


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__); return 1
    cmd, *rest = argv
    if cmd in ("add", "set-password") and rest:
        pw = getpass.getpass(f"Password for {rest[0]}: ")
        add_user(rest[0], pw)
        print(f"✓ {cmd} {rest[0]}")
    elif cmd == "remove" and rest:
        remove_user(rest[0]); print(f"✓ removed {rest[0]}")
    elif cmd == "list":
        for uid in _load():
            print(uid)
    elif cmd == "assign-library" and rest:
        assign_library(rest[0]); print(f"✓ assigned legacy library to {rest[0]}")
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_manage_users.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add manage_users.py tests/test_manage_users.py
git commit -m "feat(auth): manage_users CLI + pbkdf2 user store"
```

---

### Task 4: `POST /login` on the main app

**Files:**
- Modify: `app.py` (add import near line 37; add route inside `create_app`, e.g. after `index` at line 438)
- Test: `tests/test_login.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_login.py
import pytest
from unittest.mock import patch


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, app as app_mod
    importlib.reload(storage); importlib.reload(auth); importlib.reload(app_mod)
    application = app_mod.create_app(testing=True)
    with application.test_client() as c:
        yield c


def test_login_success_returns_token(cloud_client):
    with patch("app.manage_users.verify_user", return_value=True):
        r = cloud_client.post("/login", json={"user_id": "clientA", "password": "pw"})
    assert r.status_code == 200
    assert r.get_json()["token"]


def test_login_bad_password_401(cloud_client):
    with patch("app.manage_users.verify_user", return_value=False):
        r = cloud_client.post("/login", json={"user_id": "clientA", "password": "x"})
    assert r.status_code == 401


def test_protected_route_requires_token(cloud_client):
    # /recent-folders is a normal GET; without a token it must 401 in cloud mode.
    assert cloud_client.get("/recent-folders").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_login.py -v`
Expected: FAIL (404 on `/login`, and `/recent-folders` returns 200 — no guard yet)

- [ ] **Step 3: Write minimal implementation**

In `app.py`, add to the imports block (after line 37 `import storage`):

```python
import auth
import manage_users
```

Inside `create_app`, register the guard and login route. Immediately after `app.config["TESTING"] = testing` (line 430), add:

```python
    app.before_request(auth.require_auth)

    @app.post("/login")
    def login():
        body = request.get_json(silent=True) or {}
        user_id = (body.get("user_id") or "").strip()
        password = body.get("password") or ""
        if not user_id or not password or not manage_users.verify_user(user_id, password):
            return jsonify({"error": "invalid credentials"}), 401
        return jsonify({"token": auth.make_token(user_id)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_login.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite to catch fallout**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: PASS — existing tests run in local mode (`APP_MODE` unset), where the guard returns early, so no 401s. If any cloud-mode test now 401s, add `SIZZLE_SECRET_KEY` + a `Authorization: Bearer` header to that test's client (see Task 6).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_login.py
git commit -m "feat(auth): POST /login + before_request guard on main app"
```

---

### Task 5: Guard the generator service + WebSocket

**Files:**
- Modify: `generator_app.py` (import after line 40; guard inside `create_app` after line 846; WS token check in `job_ws` at line 850)
- Test: `tests/test_generator_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generator_auth.py
import pytest


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c, auth


def test_library_requires_token(cloud_client):
    c, _ = cloud_client
    assert c.get("/library").status_code == 401


def test_library_ok_with_token(cloud_client, monkeypatch):
    c, auth = cloud_client
    import generator_app
    monkeypatch.setattr(generator_app, "_load_library", lambda user_id=None: [])
    token = auth.make_token("clientA")
    r = c.get("/library", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_auth.py -v`
Expected: FAIL — `/library` returns 200 without a token (no guard yet)

- [ ] **Step 3: Write minimal implementation**

In `generator_app.py` imports (after line 40 `import storage`):

```python
import auth
```

Inside `create_app`, after `app.config["TESTING"] = testing` (line 846), add:

```python
    app.before_request(auth.require_auth)
```

The WebSocket route bypasses `before_request` in some setups, so validate explicitly. Replace the body of `job_ws` (line 850-852):

```python
    @sock.route("/ws/job/<job_id>")
    def job_ws(ws, job_id):
        from flask import request as _req
        if storage.is_cloud() and not auth.verify_token(_req.args.get("token")):
            try:
                ws.send(json.dumps({"type": "done", "status": "error",
                                    "error": "authentication required", "result": None}))
            except Exception:
                pass
            return
        _job_ws_impl(ws, job_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_auth.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add generator_app.py tests/test_generator_auth.py
git commit -m "feat(auth): guard generator service + WebSocket token check"
```

---

### Task 6: Fix existing cloud-mode tests to send a token

**Files:**
- Modify: `tests/test_generator_cloud.py`, `tests/test_browser_endpoints.py`, `tests/test_upload_endpoint.py`, and any other test whose fixture sets `APP_MODE=cloud`.

- [ ] **Step 1: Find cloud-mode test fixtures**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v` and note every FAIL that is now a `401`.

Also: `grep -rl 'APP_MODE.*cloud' tests/` to list affected files.

- [ ] **Step 2: Add SECRET + auth header helper to each cloud fixture**

In each cloud fixture, add `monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")` and give the test client a default auth header. Pattern (apply to `cloud_client` fixtures):

```python
    import importlib, storage, auth
    importlib.reload(storage)
    # ... existing reloads ...
    importlib.reload(auth)
    app = generator_app.create_app(testing=True)
    token = auth.make_token("testuser")
    c = app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    with c:
        yield c
```

`environ_base["HTTP_AUTHORIZATION"]` sets the header on every request the client makes, so existing cloud test bodies need no per-call change.

- [ ] **Step 3: Run the full suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: PASS (all green). Local-mode tests unaffected; cloud tests now authenticate.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: authenticate existing cloud-mode tests"
```

---

### Task 7: Frontend login + Bearer plumbing

**Files:**
- Modify: `static/app.js` (config block line 1-2; add auth helpers; wrap `fetch`)
- Modify: `templates/index.html` (add a login screen; it already injects `window.__CONFIG__`)
- Test: manual (browser preview) — documented in Step 4.

- [ ] **Step 1: Add a token-aware fetch wrapper and login flow to `app.js`**

At the top of `static/app.js` (after line 2), add:

```javascript
// ─── Auth (cloud mode only) ─────────────────────────────────────────────────
let AUTH_TOKEN = (APP_MODE === 'cloud') ? sessionStorage.getItem('sizzle_token') : null;

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (AUTH_TOKEN) h['Authorization'] = 'Bearer ' + AUTH_TOKEN;
  return h;
}

// Single choke point: every fetch in the app goes through here so the Bearer
// header is always attached and a 401 bounces the user back to login.
const _rawFetch = window.fetch.bind(window);
window.fetch = function (url, opts) {
  opts = opts || {};
  opts.headers = authHeaders(opts.headers);
  return _rawFetch(url, opts).then(r => {
    if (r.status === 401 && APP_MODE === 'cloud') {
      AUTH_TOKEN = null;
      sessionStorage.removeItem('sizzle_token');
      showLoginScreen();
    }
    return r;
  });
};

function wsUrl(base) {   // append token for the generator WebSocket
  return AUTH_TOKEN ? base + (base.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(AUTH_TOKEN) : base;
}

async function doLogin(userId, password) {
  const r = await _rawFetch('/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, password }),
  });
  if (!r.ok) return false;
  AUTH_TOKEN = (await r.json()).token;
  sessionStorage.setItem('sizzle_token', AUTH_TOKEN);
  return true;
}

function showLoginScreen() {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const el = document.getElementById('screen-login');
  if (el) el.classList.add('active');
}
```

- [ ] **Step 2: Add the login screen markup to `index.html`**

After the opening `<body>`-level content (before the first `.screen` block), add:

```html
<section id="screen-login" class="screen">
  <div class="login-card">
    <h2>Sign in</h2>
    <label>Client ID <input id="login-user" autocomplete="username"></label>
    <label>Password <input id="login-pass" type="password" autocomplete="current-password"></label>
    <button id="login-btn">Sign in</button>
    <p id="login-error" class="login-error" hidden>Invalid credentials.</p>
  </div>
</section>
```

- [ ] **Step 3: Wire the login button and startup gate**

In `app.js`, find the app's init/bootstrap (where the first screen is shown on load) and gate it:

```javascript
document.getElementById('login-btn')?.addEventListener('click', async () => {
  const ok = await doLogin(
    document.getElementById('login-user').value.trim(),
    document.getElementById('login-pass').value,
  );
  const err = document.getElementById('login-error');
  if (ok) { err.hidden = true; startApp(); }     // startApp() = existing entry that shows folder-picker
  else { err.hidden = false; }
});

// On load: cloud mode with no token → login; otherwise proceed as today.
if (APP_MODE === 'cloud' && !AUTH_TOKEN) {
  showLoginScreen();
} else {
  startApp();
}
```

Replace `startApp()` with the app's real existing bootstrap call. If the current code runs bootstrap inline at load, extract it into a `startApp()` function first, then call it from both branches above.

Update the generator WebSocket connection (search `new WebSocket(` in `app.js`) to wrap its URL with `wsUrl(...)`.

- [ ] **Step 4: Verify in the browser**

Start the app in cloud-simulated mode is heavy; instead verify local mode is unbroken and the login screen renders:
- `preview_start` the main app, load `/`, confirm the folder-picker still appears in local mode (no login screen, `APP_MODE=local`).
- Temporarily set `window.__CONFIG__.mode='cloud'` in devtools and reload the login screen via `showLoginScreen()` to confirm markup/styles render.
- Check console for errors.

- [ ] **Step 5: Commit**

```bash
git add static/app.js templates/index.html
git commit -m "feat(auth): frontend login screen + Bearer/WS token plumbing"
```

---

### Task 8: Wire `SIZZLE_SECRET_KEY` into deploy config

**Files:**
- Modify: `render.yaml`, `docker-compose.yml`

- [ ] **Step 1: Add the env var to both Render services**

In `render.yaml`, under `envVars` for **both** `sizzle-app` and `sizzle-generator`, add (same value on both — that's what lets them validate each other's tokens):

```yaml
      - key: SIZZLE_SECRET_KEY
        sync: false
```

- [ ] **Step 2: Add it to docker-compose for local cloud-repro**

In `docker-compose.yml`, add to both `app` and `generator` `environment:` blocks:

```yaml
      - SIZZLE_SECRET_KEY=${SIZZLE_SECRET_KEY}
```

- [ ] **Step 3: Commit**

```bash
git add render.yaml docker-compose.yml
git commit -m "chore(auth): SIZZLE_SECRET_KEY env var for both services"
```

- [ ] **Step 4: Operator runbook note (do, don't commit secrets)**

In the Render dashboard, set `SIZZLE_SECRET_KEY` to the **same** strong random value on both services (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Then provision the first client: run `manage_users.py add <client>` against the cloud storage (locally with cloud env vars, or a one-off Render shell).

---

**✅ Phase 1 gate:** Both services now reject anonymous requests in cloud mode; login issues tokens; frontend gates on login. Ship before starting Phase 2.

---

## Phase 2 — Tenancy (per-user sessions + libraries)

Fixes findings **#3** (cross-tenant library access/IDOR) and **#5** (folder/session_key isolation bypass).

### Task 9: `storage.py` — user-scoped keys

**Files:**
- Modify: `storage.py` (`new_session_key` line 58, `library_key` line 63, `load_library` line 245)
- Test: `tests/test_storage_tenancy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_tenancy.py
import pytest


def _cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage
    importlib.reload(storage)
    return storage


def test_session_key_scoped_by_user(monkeypatch):
    s = _cloud(monkeypatch)
    key = s.new_session_key("clientA")
    assert key.startswith("users/clientA/sessions/")


def test_session_key_unscoped_without_user(monkeypatch):
    s = _cloud(monkeypatch)
    assert s.new_session_key().startswith("sessions/")


def test_library_key_scoped_by_user(monkeypatch):
    s = _cloud(monkeypatch)
    assert s.library_key("clientA") == "users/clientA/library.json"


def test_library_key_legacy_without_user(monkeypatch):
    s = _cloud(monkeypatch)
    assert s.library_key() == "library/sizzle_library.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_storage_tenancy.py -v`
Expected: FAIL — `new_session_key()` takes no args / `library_key()` ignores arg

- [ ] **Step 3: Implement**

Replace `new_session_key` (line 58-60):

```python
def new_session_key(user_id: str | None = None) -> str:
    """Fresh unique upload-session prefix, scoped under the user in cloud tenancy."""
    suffix = f"sessions/{uuid.uuid4().hex}"
    return f"users/{user_id}/{suffix}" if user_id else suffix
```

Replace `library_key` (line 63-65):

```python
def library_key(user_id: str | None = None) -> str:
    """Per-user library key in tenancy mode; legacy global key when user_id is None."""
    if user_id:
        return f"users/{user_id}/library.json"
    return "library/sizzle_library.json"
```

Replace `load_library` (line 245-255) to accept `user_id`:

```python
def load_library(user_id: str | None = None) -> list:
    """Load a library JSON (per-user in tenancy mode). Returns [] on missing/corrupt."""
    if is_cloud():
        return read_json(library_key(user_id))
    path = _data_root() / "sizzle_library.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_storage_tenancy.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add storage.py tests/test_storage_tenancy.py
git commit -m "feat(tenancy): user-scoped session + library keys in storage"
```

---

### Task 10: Per-user library in the generator + ownership 404s

**Files:**
- Modify: `generator_app.py` — `_load_library` (line 75), `_save_library` (line 79), `_library_add` (line 87), and every endpoint that reads/writes the library.
- Test: `tests/test_library_tenancy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_library_tenancy.py
import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    return generator_app, auth


def _client(generator_app, auth, user):
    app = generator_app.create_app(testing=True)
    c = app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token(user)
    return c


def test_library_is_per_user(cloud, monkeypatch):
    generator_app, auth = cloud
    stores = {"clientA": [{"id": "a1", "filename": "a.mp4"}],
              "clientB": [{"id": "b1", "filename": "b.mp4"}]}
    monkeypatch.setattr(generator_app, "_load_library",
                        lambda user_id=None: stores.get(user_id, []))
    a = _client(generator_app, auth, "clientA")
    ids = [e["id"] for e in a.get("/library").get_json()]
    assert ids == ["a1"]          # clientA never sees clientB's entry


def test_cross_tenant_delete_404(cloud, monkeypatch):
    generator_app, auth = cloud
    monkeypatch.setattr(generator_app, "_load_library",
                        lambda user_id=None: [] if user_id == "clientB" else [{"id": "a1"}])
    saved = {}
    monkeypatch.setattr(generator_app, "_save_library",
                        lambda entries, user_id=None: saved.__setitem__(user_id, entries))
    b = _client(generator_app, auth, "clientB")
    assert b.delete("/library/a1").status_code == 404   # not in clientB's library
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_library_tenancy.py -v`
Expected: FAIL — `_load_library` ignores `user_id`; `/library` returns everything

- [ ] **Step 3: Implement**

Add a helper near the top of `create_app` in `generator_app.py` (after the `before_request` guard from Task 5):

```python
    def _uid():
        from flask import g
        return getattr(g, "user_id", None)
```

Update the library helpers (lines 75-91) to thread `user_id`:

```python
def _load_library(user_id: str | None = None) -> list:
    return storage.load_library(user_id)


def _save_library(entries: list, user_id: str | None = None) -> None:
    if storage.is_cloud():
        storage.write_json(storage.library_key(user_id), entries)
        return
    with LIBRARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _library_add(entry: dict, user_id: str | None = None) -> None:
    with _library_lock:
        entries = _load_library(user_id)
        entries.insert(0, entry)
        _save_library(entries, user_id)
```

Now update **every endpoint** to pass `_uid()` and 404 on missing ownership. Exact edits:

- `GET /library` (line 1216): `entries = _load_library(_uid())`
- `GET /library-video/<entry_id>` (line 1112): `entries = _load_library(_uid())`
- `GET /library-captions/<entry_id>` (line 1156): `entries = _load_library(_uid())`
- `POST /library` add endpoint (line 1040): `_library_add(entry, _uid())`
- `DELETE /library/<entry_id>` (line 1229): `entries = _load_library(_uid())` and `_save_library(entries, _uid())`
- `PATCH /library/<entry_id>` (line 1248): `entries = _load_library(_uid())` and `_save_library(entries, _uid())`
- `POST /library/<entry_id>/download-captioned` (line 1186): `entries = _load_library(_uid())`

Because each endpoint now loads only the caller's library, the existing `next((e for e in entries if e["id"] == entry_id), None)` → `404` lines already enforce ownership. No extra checks needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_library_tenancy.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Update the two legacy library unit tests**

`tests/test_generator_cloud.py::test_load_library_uses_storage_in_cloud_mode` and `test_save_library_uses_storage_in_cloud_mode` assert the no-arg (legacy) signature — they still pass because `library_key(None)` is the legacy key and `_load_library()`/`_save_library(data)` default `user_id=None`. Run them:

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py -k library -v`
Expected: PASS. If `_save_library`'s new signature (`entries, user_id=None`) broke the call `_save_library(data)`, it won't — `data` binds to `entries`. Leave as-is.

- [ ] **Step 6: Commit**

```bash
git add generator_app.py tests/test_library_tenancy.py
git commit -m "feat(tenancy): per-user libraries + ownership 404s"
```

---

### Task 11: Worker-thread `user_id` handoff (generation)

**Files:**
- Modify: `generator_app.py` — `_run_generation` (line 391), `_run_generation_impl` (line 425), `_library_add` call (line 778), `/generate` handler (line 854), `/plan` handler (line 953).
- Test: `tests/test_generate_user_handoff.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate_user_handoff.py
"""The generation worker runs on a thread with no request context, so /generate
must capture g.user_id and pass it explicitly; the reel must land in that user's
library."""
import pytest
from unittest.mock import patch


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    return generator_app, auth


def test_generate_passes_user_id_to_worker(cloud, monkeypatch):
    generator_app, auth = cloud
    captured = {}

    def fake_run(job_id, folder, selections, prompt, output_filename, **kw):
        captured["user_id"] = kw.get("user_id")

    monkeypatch.setattr(generator_app, "_run_generation", fake_run)
    monkeypatch.setattr(generator_app.storage, "list_keys", lambda p: [])
    monkeypatch.setattr(generator_app, "check_ffmpeg", lambda: None)
    app = generator_app.create_app(testing=True)
    c = app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token("clientA")
    c.post("/generate", json={"session_key": "users/clientA/sessions/x",
                              "selections": {}, "prompt": "p"})
    assert captured["user_id"] == "clientA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generate_user_handoff.py -v`
Expected: FAIL — `_run_generation` receives no `user_id`

- [ ] **Step 3: Implement**

Add `user_id: str | None = None` to the signatures of `_run_generation` (line 391) and `_run_generation_impl` (line 425), and forward it in the `_run_generation` → `_run_generation_impl` call (line 406):

```python
def _run_generation(job_id, folder, selections, prompt, output_filename,
                    session_key=None, video_paths=None, video_urls=None,
                    user_id=None):
    try:
        _run_generation_impl(job_id, folder, selections, prompt, output_filename,
                             session_key=session_key, video_paths=video_paths,
                             video_urls=video_urls, user_id=user_id)
    except Exception as exc:
        ...  # unchanged
```

In `_run_generation_impl`, change the `_library_add(library_entry)` call (line 778):

```python
    _library_add(library_entry, user_id)
```

In the `/generate` handler, capture the uid before spawning the thread (near line 862, after `session_key` is parsed):

```python
        from flask import g
        gen_user_id = getattr(g, "user_id", None)
```

Pass `user_id=gen_user_id` into **both** `_run_generation(...)` calls (the testing-synchronous one at line 925 and the threaded `_run_with_cleanup` one at line 937).

In `/plan` (line 953) the reel is recorded later by `POST /library` (browser-driven), which already scopes via `_uid()` from Task 10 — no worker handoff needed there. Confirm `/plan` itself only reads transcripts (no library write) — it does; leave it.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generate_user_handoff.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add generator_app.py tests/test_generate_user_handoff.py
git commit -m "feat(tenancy): pass user_id into generation worker thread"
```

---

### Task 12: Prefix-scope `folder`/`session_key` (close #5)

**Files:**
- Modify: `app.py` — `/load-folder` (582), `/transcripts` (737), `/analyze` (766); `generator_app.py` — `/generate` (854), `/plan` (953).
- Test: `tests/test_folder_scoping.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_folder_scoping.py
"""In cloud mode a user may only reference session keys under their own prefix.
Passing an arbitrary/real server path or another user's key must 403 (regression
for audit finding #5)."""
import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, app as app_mod
    importlib.reload(storage); importlib.reload(auth); importlib.reload(app_mod)
    application = app_mod.create_app(testing=True)
    c = application.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token("clientA")
    return c


def test_analyze_rejects_foreign_prefix(cloud):
    r = cloud.post("/analyze", json={"folder": "users/clientB/sessions/x",
                                     "prompt": "hi"})
    assert r.status_code == 403


def test_analyze_rejects_real_server_path(cloud):
    r = cloud.post("/analyze", json={"folder": "/etc", "prompt": "hi"})
    assert r.status_code == 403


def test_analyze_allows_own_prefix(cloud, monkeypatch):
    import app as app_mod
    # Own-prefix folder passes the guard; downstream then 404s on no session — the
    # point is it is NOT 403.
    r = cloud.post("/analyze", json={"folder": "users/clientA/sessions/x",
                                     "prompt": "hi"})
    assert r.status_code != 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_folder_scoping.py -v`
Expected: FAIL — foreign prefix and `/etc` are not rejected

- [ ] **Step 3: Implement a shared guard**

In `auth.py`, add:

```python
def owns_session(session_key: str) -> bool:
    """True if session_key is under the current user's prefix (cloud), or always in local."""
    if not storage.is_cloud():
        return True
    if not session_key:
        return False
    return session_key.startswith(current_user_prefix() + "/")
```

In `app.py`, at the start of `/load-folder`, `/transcripts`, `/analyze` — right after `folder` is parsed and before any `Path(folder).exists()` / `_ensure_cloud_session` call — add:

```python
        if storage.is_cloud() and not auth.owns_session(folder):
            return jsonify({"error": "forbidden"}), 403
```

(For `/transcripts`, `folder` comes from `request.args`; add the same guard after it's read at line 739.)

In `generator_app.py`, at the start of `/generate` (after `session_key` parsed, line 862) and `/plan` (after `session_key` parsed, line 960):

```python
        if storage.is_cloud() and not auth.owns_session(session_key):
            return jsonify({"error": "forbidden"}), 403
```

This runs **before** the `not session_key` checks; a foreign or empty key is rejected. Because the guard requires the `users/<uid>/` prefix, a real server path like `/etc` never matches → 403, closing #5.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_folder_scoping.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Scope session creation to the user**

Update `/upload` (line 467) and `/upload/prepare` (line 534) in `app.py` so new sessions are created under the caller:

```python
        session_key = storage.new_session_key(getattr(g, "user_id", None))
```

(add `from flask import g` if not already imported in `app.py` — it is via `from flask import ...`; add `g` to that import line 29.)

Run the upload tests: `.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py tests/test_browser_endpoints.py -v` — Expected: PASS (fixtures now send a token from Task 6; keys are user-prefixed).

- [ ] **Step 6: Commit**

```bash
git add app.py generator_app.py auth.py tests/test_folder_scoping.py
git commit -m "feat(tenancy): prefix-scope folder/session_key, close isolation bypass (#5)"
```

---

**✅ Phase 2 gate:** Libraries are per-user; a client cannot read/delete/edit another's reels; `folder`/`session_key` is confined to the caller's prefix. Ship before Phase 3.

---

## Phase 3 — Rate limiting + upload caps

Fixes the remainder of finding **#2**.

### Task 13: Add flask-limiter dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dep**

Append to `requirements.txt`:

```
flask-limiter
```

- [ ] **Step 2: Install it**

Run: `.\venv\Scripts\python.exe -m pip install flask-limiter`
Expected: installs cleanly.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add flask-limiter dependency"
```

---

### Task 14: Rate limits on the main app

**Files:**
- Modify: `app.py`
- Test: `tests/test_rate_limits.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rate_limits.py
import pytest


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, app as app_mod
    importlib.reload(storage); importlib.reload(auth); importlib.reload(app_mod)
    application = app_mod.create_app(testing=True)
    with application.test_client() as c:
        yield c


def test_login_is_rate_limited(cloud_client):
    from unittest.mock import patch
    with patch("app.manage_users.verify_user", return_value=False):
        codes = [cloud_client.post("/login",
                 json={"user_id": "x", "password": "y"}).status_code
                 for _ in range(12)]
    assert 429 in codes    # the 6th+ attempt within the window is throttled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_rate_limits.py -v`
Expected: FAIL — all 12 return 401, no 429

- [ ] **Step 3: Implement**

In `app.py` imports (after `import auth`):

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
```

Inside `create_app`, after `app.before_request(auth.require_auth)`:

```python
    def _rate_key():
        from flask import g
        return getattr(g, "user_id", None) or get_remote_address()

    # ponytail: in-memory limiter storage — per-instance, resets on restart.
    # Fine on Render's single free-tier instance; move to Redis storage_uri if
    # the service ever scales to multiple instances.
    limiter = Limiter(key_func=_rate_key, app=app, default_limits=["600 per hour"])
    app.limiter = limiter
```

Decorate the sensitive routes. Add above `def login()`:

```python
    @app.post("/login")
    @limiter.limit("5 per minute")
    def login():
        ...
```

Add above `def analyze()` (line 766):

```python
    @app.post("/analyze")
    @limiter.limit("10 per minute;100 per hour")
    def analyze():
        ...
```

Add above `def upload()` (line 443) and `def upload_prepare()` (line 499): `@limiter.limit("30 per minute")`.

Note: `limiter.limit` decorators go **between** the `@app.post(...)` line and the `def`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_rate_limits.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_rate_limits.py
git commit -m "feat(limits): rate-limit login/analyze/upload on main app"
```

---

### Task 15: Rate limits on the generator + MAX_CONTENT_LENGTH

**Files:**
- Modify: `generator_app.py`, `app.py`
- Test: `tests/test_upload_cap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_upload_cap.py
import pytest


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, app as app_mod
    importlib.reload(storage); importlib.reload(auth); importlib.reload(app_mod)
    application = app_mod.create_app(testing=True)
    c = application.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token("clientA")
    return c


def test_oversize_body_rejected(cloud_client):
    # Body larger than MAX_CONTENT_LENGTH → 413 before the handler runs.
    big = b"x" * (60 * 1024 * 1024)   # 60 MB
    r = cloud_client.post("/upload/prepare", data=big,
                          content_type="application/json")
    assert r.status_code == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_upload_cap.py -v`
Expected: FAIL — no cap, returns 400/500 not 413

- [ ] **Step 3: Implement**

In `app.py` `create_app`, after the limiter setup:

```python
    # 50 MB cap on request bodies the host actually buffers. Large video bytes go
    # browser→R2 via presigned PUT and never hit this host (see /upload/prepare).
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
```

In `generator_app.py`, mirror the limiter (imports + setup identical to Task 14) and add its own `MAX_CONTENT_LENGTH`. Add limits to `/generate` and `/plan`:

```python
    @app.post("/generate")
    @limiter.limit("10 per minute;100 per hour")
    def generate():
        ...
```

```python
    @app.post("/plan")
    @limiter.limit("20 per minute")
    def plan():
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_upload_cap.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Full suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add app.py generator_app.py tests/test_upload_cap.py
git commit -m "feat(limits): generator rate limits + MAX_CONTENT_LENGTH cap"
```

---

**✅ Phase 3 gate:** Login brute-force, Anthropic-spend, and body-size abuse are bounded per user/IP.

---

## Phase 4 — CORS lockdown

Defence-in-depth for **#4** (Bearer tokens already neutralise the CSRF vector).

### Task 16: Explicit CORS origins on both services

**Files:**
- Modify: `generator_app.py` (line 845 `CORS(app)`), `app.py` (add CORS if the frontend is cross-origin), `render.yaml`, `docker-compose.yml`
- Test: `tests/test_cors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cors.py
import pytest


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://sizzle-app-q1p9.onrender.com")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_allowed_origin_echoed(cloud_client):
    r = cloud_client.get("/library",
                         headers={"Origin": "https://sizzle-app-q1p9.onrender.com"})
    assert r.headers.get("Access-Control-Allow-Origin") == \
        "https://sizzle-app-q1p9.onrender.com"


def test_foreign_origin_not_allowed(cloud_client):
    r = cloud_client.get("/library", headers={"Origin": "https://evil.example.com"})
    assert r.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_cors.py -v`
Expected: FAIL — `CORS(app)` echoes `*`, so the foreign-origin assertion fails

- [ ] **Step 3: Implement**

In `generator_app.py`, replace `CORS(app)` (line 845):

```python
    _origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if _origins:
        CORS(app, origins=_origins, allow_headers=["Authorization", "Content-Type"])
    else:
        CORS(app)   # local/dev: permissive when ALLOWED_ORIGINS is unset
```

(The main app `app.py` is same-origin with its own frontend, so it needs CORS only if you serve the frontend from a different host — if so, mirror this block in `app.py` `create_app`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_cors.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Add ALLOWED_ORIGINS to deploy config**

In `render.yaml`, add under both services' `envVars`:

```yaml
      - key: ALLOWED_ORIGINS
        sync: false
```

Set its value in the Render dashboard to the frontend origin(s), comma-separated.

- [ ] **Step 6: Commit**

```bash
git add generator_app.py app.py render.yaml docker-compose.yml tests/test_cors.py
git commit -m "feat(cors): restrict origins to ALLOWED_ORIGINS in cloud mode"
```

---

**✅ Phase 4 gate:** Cross-origin API access is limited to the configured frontend origin(s).

---

## Final verification

- [ ] **Full suite green:** `.\venv\Scripts\python.exe -m pytest tests/ -v`
- [ ] **Manual cloud smoke (docker-compose):** set `SIZZLE_SECRET_KEY`, `ALLOWED_ORIGINS`, provision a user via `manage_users.py add`, then confirm: unauthenticated `/library` → 401; login → token; authenticated flow works; a second user cannot see the first's library.
- [ ] **Local mode unchanged:** run the app locally (`APP_MODE` unset), confirm no login screen and the full generate flow still works.

## Notes for the implementer

- **Test isolation:** these tests `importlib.reload(storage, auth, app/generator_app)` after setting env vars because module-level state (`storage.is_cloud()`, `auth` secret) is read at import. Always reload in the order storage → auth → app module.
- **Local mode is the safety net:** every guard early-returns when `not storage.is_cloud()`, so the entire existing local test suite and desktop workflow are untouched. If a local test starts failing, a guard is missing its `is_cloud()` check.
- **Out of scope (track separately):** gunicorn swap, `_jobs` dict eviction, `/find-local-folder` oracle, `debug=True` in `__main__`, CSP/security headers, R2 GET-CORS narrowing.
```

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


# Paths reachable without a token (exact match on request.path).
# "/" is the public page shell (no data) — it must load so the login form can
# render; "/login" issues the token. Everything else requires auth in cloud mode.
_ALLOWLIST = {"/", "/login"}


def _bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return request.args.get("token")


def require_auth():
    """before_request callback. Returns None to allow, or a 401 response to block."""
    if not storage.is_cloud():
        return None
    if request.path in _ALLOWLIST or request.path.startswith("/static/"):
        return None
    if request.method == "OPTIONS":
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

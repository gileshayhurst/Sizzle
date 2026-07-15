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

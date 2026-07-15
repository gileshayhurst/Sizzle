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

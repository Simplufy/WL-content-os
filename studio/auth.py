"""Team accounts for the public deployment (your public URL).

- Requests that arrive through the Cloudflare tunnel must be signed in.
- Requests made directly on this machine (127.0.0.1, no Cloudflare headers) are trusted, so the
  owner can always get in locally to create the first account.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

SESSION_COOKIE = "studio_session"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 310_000
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);
"""


def ensure_schema() -> None:
    db.conn().executescript(SCHEMA)
    db.conn().commit()


# ---------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(dk.hex(), digest)


def validate_new_password(pw: str) -> None:
    if len(pw) < 10:
        raise ValueError("Use at least 10 characters")


# ---------------------------------------------------------------- users

def create_user(email: str, password: str, *, name: str | None = None, is_admin: bool = False) -> int:
    ensure_schema()
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("Enter a valid email")
    validate_new_password(password)
    if db.row("SELECT 1 FROM users WHERE email = ?", (email,)):
        raise ValueError("That email already has an account")
    return db.execute("INSERT INTO users(email, name, password_hash, is_admin, created_at) VALUES(?,?,?,?,?)",
                      (email, name, hash_password(password), int(is_admin), db.now()))


def list_users() -> list[dict[str, Any]]:
    ensure_schema()
    return db.rows("SELECT id, email, name, is_admin, created_at, last_login_at FROM users ORDER BY id")


def delete_user(uid: int) -> None:
    db.execute("DELETE FROM users WHERE id = ?", (uid,))


def set_password(uid: int, password: str) -> None:
    validate_new_password(password)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), uid))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))


# ---------------------------------------------------------------- sessions

_DUMMY_HASH = hash_password("not-a-real-password")


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(email: str, password: str, user_agent: str = "") -> tuple[str, dict[str, Any]] | None:
    ensure_schema()
    u = db.row("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
    if not u:
        verify_password(password, _DUMMY_HASH)  # same work either way: don't leak which emails exist
        return None
    if not verify_password(password, u["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).replace(microsecond=0).isoformat()
    db.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent) VALUES(?,?,?,?,?)",
               (_h(token), u["id"], db.now(), exp, user_agent[:200]))
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (db.now(), u["id"]))
    return token, {k: u[k] for k in ("id", "email", "name", "is_admin")}


def user_for(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    ensure_schema()
    r = db.row("SELECT u.id, u.email, u.name, u.is_admin, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
               "WHERE s.token_hash = ?", (_h(token),))
    if not r or r["expires_at"] < db.now():
        return None
    return {k: r[k] for k in ("id", "email", "name", "is_admin")}


def logout(token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))


# ---------------------------------------------------------------- request classification

def is_local(host: str | None, headers: dict[str, str]) -> bool:
    """Direct request on this machine. Anything relayed by Cloudflare carries cf-* headers and the
    public hostname, so it can never look local."""
    if any(k.lower().startswith("cf-") for k in headers):
        return False
    h = (host or "").lower()
    h = h.split("]")[0] + "]" if h.startswith("[") else h.split(":")[0]
    return h in LOCAL_HOSTS


# ---------------------------------------------------------------- brute-force guard

_attempts: dict[str, deque] = defaultdict(deque)
WINDOW_S, MAX_ATTEMPTS = 15 * 60, 10


def allow_attempt(key: str) -> bool:
    now = time.monotonic()
    q = _attempts[key]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        return False
    q.append(now)
    return True

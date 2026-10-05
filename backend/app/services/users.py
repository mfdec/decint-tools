"""Accounts: creation, lookup, grouping, sessions, audit.

Users are grouped along three axes that are deliberately independent:

    role    admin | operator | user      what they may DO
    tier    starter | professional | custom   what they've PAID for
    status  active | suspended | pending  whether they may sign in at all

Keeping these separate matters: downgrading someone's plan must never
accidentally strip their admin rights, and suspending an account for
non-payment must not require rewriting their tier. Feature gating reads `tier`,
authorisation reads `role`, and the login path reads `status`.

Passwords are Argon2id. Sessions live in the database (not in a signed cookie)
so that "revoke this session" is a real operation rather than a hope.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .. import db
from ..config import settings
from .billing import plans as _plans

_ph = PasswordHasher()

ROLES = ("admin", "operator", "user")
# `free` is where an account sits having bought nothing — including every new
# signup. It is a real tier rather than a null so that "has no plan" and "has
# lapsed" are the same state, with the same gates, and nothing has to special-
# case an empty string.
# Derived from the catalogue rather than restated: `plans.Plan.key` doubles as
# the tier, so listing them again here is a second source of truth that goes
# stale the first time a plan is renamed.
TIERS = tuple(p.key for p in _plans.PLANS)
STATUSES = ("active", "suspended", "pending")

# Which console apps each tier may reach. `visitors` and `users` are role-gated
# rather than tier-gated, so they are not listed here.
#
# Every tier sees the whole toolkit, deliberately: the pricing page promises
# tiers differ by volume and depth rather than by locking tools away, and this
# table is what has to keep that promise true.
_ALL_APPS = ["recon", "leaks", "darkweb"]
TIER_APPS: dict[str, list[str]] = {t: list(_ALL_APPS) for t in TIERS}

# Monthly query allowances. Derived from the billing catalogue rather than
# restated here — two lists of numbers claiming to be the same prices is how a
# pricing page ends up disagreeing with what the card is charged.
TIER_QUOTA: dict[str, int | None] = {p.key: p.quota for p in _plans.PLANS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────── passwords ───────────────────────────

def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hash_: str, pw: str) -> bool:
    try:
        _ph.verify(hash_, pw)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


# A real Argon2 hash of a throwaway value. Verifying against this when the
# account doesn't exist makes the "no such user" path cost the same as the
# "wrong password" path — without it, response latency is an account-existence
# oracle regardless of how carefully the error messages are matched.
_DUMMY_HASH = _ph.hash("no-such-account-timing-equaliser")


def verify_dummy(pw: str) -> bool:
    """Burn the same CPU as a real verification, then fail."""
    verify_password(_DUMMY_HASH, pw)
    return False


def username_taken(username: str) -> bool:
    if not username:
        return False
    return db.one(
        "SELECT 1 AS x FROM users WHERE lower(username) = ?", (username.strip().lower(),)
    ) is not None


def password_problem(pw: str) -> str | None:
    """Return a human-readable reason the password is unacceptable, or None."""
    if len(pw) < 12:
        return "Password must be at least 12 characters."
    if pw.lower() in {"password123", "changeme1234", "decinttools"}:
        return "That password is too common."
    return None


# ─────────────────────────── users ───────────────────────────

PUBLIC_COLS = (
    "id, email, username, role, tier, status, email_verified, phone, "
    "totp_enabled, email_otp_enabled, sms_otp_enabled, "
    "created_at, updated_at, last_login_at, last_login_ip, notes, locked_until"
)


def count() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM users")
    return row["n"] if row else 0


def get(user_id: int) -> dict | None:
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_by_email(email: str) -> dict | None:
    return db.one("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))


def get_public(user_id: int) -> dict | None:
    return db.one(f"SELECT {PUBLIC_COLS} FROM users WHERE id = ?", (user_id,))


def create(
    email: str,
    password: str,
    *,
    role: str = "user",
    tier: str = "free",
    status: str = "active",
    username: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
) -> dict:
    email = email.strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if get_by_email(email):
        raise ValueError("An account with that email already exists.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)

    uid = db.execute(
        "INSERT INTO users (email, username, password_hash, role, tier, status, "
        "phone, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (email, username, hash_password(password), role, tier, status,
         phone, notes, _now(), _now()),
    )
    return get_public(uid)  # type: ignore[return-value]


def create_oauth(
    email: str,
    *,
    tier: str = "free",
    status: str = "active",
    username: str | None = None,
) -> dict:
    """Create an account that authenticates via a social provider, not a
    password. We still store a password_hash because the column is NOT NULL —
    but it's the hash of a throwaway random secret nobody holds, so the
    email+password login path can never match it. The provider already
    asserted the email, so email_verified is set.
    """
    import secrets

    email = email.strip().lower()
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if get_by_email(email):
        raise ValueError("An account with that email already exists.")

    uid = db.execute(
        "INSERT INTO users (email, username, password_hash, role, tier, status, "
        "email_verified, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (email, username, hash_password(secrets.token_urlsafe(32)), "user",
         tier, status, 1, _now(), _now()),
    )
    return get(uid)  # type: ignore[return-value]


def update(user_id: int, **fields: Any) -> dict | None:
    allowed = {
        "email", "username", "role", "tier", "status", "phone", "notes",
        "email_verified",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "role" and v not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if k == "tier" and v not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}")
        if k == "status" and v not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        sets.append(f"{k} = ?")
        vals.append(v.strip().lower() if k == "email" else v)
    if not sets:
        return get_public(user_id)
    sets.append("updated_at = ?")
    vals.append(_now())
    vals.append(user_id)
    db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    return get_public(user_id)


def set_password(user_id: int, password: str) -> None:
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(password), _now(), user_id),
    )


# Every table with a row per account. The schema declares ON DELETE CASCADE on
# all of them, but SQLite only honours that with PRAGMA foreign_keys=ON, which
# this database has never had — so the cascade is done here, by hand.
_USER_TABLES = (
    "sessions", "otp_codes", "login_tokens", "password_resets",
    "billing_customers", "billing_orders", "subscriptions", "entitlements",
    "usage_counters", "phone_counters",
)


def delete(user_id: int) -> None:
    """Remove the account and everything stored about it, in one transaction.

    The audit log is the exception: it is the security record of what happened
    to the account (including this deletion), and holds an address, not a link.
    """
    p = (user_id,)
    db.transaction([
        *((f"DELETE FROM {t} WHERE user_id = ?", p) for t in _USER_TABLES),
        ("DELETE FROM ticket_messages WHERE ticket_id IN "
         "(SELECT id FROM tickets WHERE user_id = ?)", p),
        ("DELETE FROM tickets WHERE user_id = ?", p),
        # Replies this account wrote on someone else's ticket (staff answers)
        # stay with that ticket; they just stop pointing at a person.
        ("UPDATE ticket_messages SET author_id = NULL WHERE author_id = ?", p),
        ("DELETE FROM users WHERE id = ?", p),
    ])


def listing(
    q: str | None = None,
    role: str | None = None,
    tier: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    where, params = ["1=1"], []
    if q:
        where.append("(email LIKE ? OR username LIKE ? OR notes LIKE ?)")
        params += [f"%{q}%"] * 3
    for col, val in (("role", role), ("tier", tier), ("status", status)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    clause = " AND ".join(where)

    total = db.one(f"SELECT COUNT(*) AS n FROM users WHERE {clause}", tuple(params))
    rows = db.query(
        f"SELECT {PUBLIC_COLS} FROM users WHERE {clause} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )
    return {"total": total["n"] if total else 0, "users": rows}


def stats() -> dict:
    def group(col: str) -> dict[str, int]:
        return {
            r["k"]: r["n"]
            for r in db.query(f"SELECT {col} AS k, COUNT(*) AS n FROM users GROUP BY {col}")
        }

    active_sessions = db.one(
        "SELECT COUNT(*) AS n FROM sessions WHERE revoked = 0 AND expires_at > ?",
        (_now(),),
    )
    mfa = db.one(
        "SELECT COUNT(*) AS n FROM users WHERE totp_enabled = 1 "
        "OR email_otp_enabled = 1 OR sms_otp_enabled = 1"
    )
    return {
        "total": count(),
        "by_role": group("role"),
        "by_tier": group("tier"),
        "by_status": group("status"),
        "with_mfa": mfa["n"] if mfa else 0,
        "active_sessions": active_sessions["n"] if active_sessions else 0,
    }


# ─────────────────────────── lockout ───────────────────────────

def note_failed_login(user_id: int) -> None:
    row = db.one("SELECT failed_logins FROM users WHERE id = ?", (user_id,))
    n = (row["failed_logins"] if row else 0) + 1
    locked = None
    if n >= settings.login_max_attempts:
        locked = (
            datetime.now(timezone.utc)
            + timedelta(minutes=settings.login_lockout_minutes)
        ).isoformat(timespec="seconds")
        n = 0
    db.execute(
        "UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
        (n, locked, user_id),
    )


def clear_failed_logins(user_id: int, ip: str = "") -> None:
    db.execute(
        "UPDATE users SET failed_logins = 0, locked_until = NULL, "
        "last_login_at = ?, last_login_ip = ? WHERE id = ?",
        (_now(), ip, user_id),
    )


def is_locked(user: dict) -> bool:
    lu = user.get("locked_until")
    return bool(lu and lu > _now())


# ─────────────────────────── sessions ───────────────────────────

def create_session(user_id: int, ip: str = "", ua: str = "") -> str:
    sid = secrets.token_urlsafe(32)
    expires = (
        datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds)
    ).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO sessions (id, user_id, created_at, expires_at, last_seen, ip, user_agent) "
        "VALUES (?,?,?,?,?,?,?)",
        (sid, user_id, _now(), expires, _now(), ip, ua[:512]),
    )
    return sid


def session_user(sid: str) -> dict | None:
    """Resolve a session id to its user, or None if invalid/expired/revoked."""
    if not sid:
        return None
    row = db.one(
        "SELECT s.id AS sid, s.expires_at, s.revoked, u.* "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (sid,),
    )
    if not row or row["revoked"] or row["expires_at"] <= _now():
        return None
    if row["status"] != "active":
        return None
    db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (_now(), sid))
    return row


def revoke_session(sid: str) -> None:
    db.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (sid,))


def revoke_all_sessions(user_id: int) -> int:
    return db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))


def revoke_other_sessions(user_id: int, keep_sid: str) -> int:
    """Sign out everywhere except the session that is making the request — the
    one that just proved it knows the current password."""
    return db.execute(
        "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND id != ?",
        (user_id, keep_sid),
    )


def sessions_for(user_id: int) -> list[dict]:
    return db.query(
        "SELECT id, created_at, expires_at, last_seen, ip, user_agent, revoked "
        "FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
        (user_id,),
    )


def purge_expired_sessions() -> int:
    return db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))


# ─────────────────────────── recovery codes ───────────────────────────

def issue_recovery_codes(user_id: int, n: int = 10) -> list[str]:
    """Generate fresh codes, store only their hashes, return the plaintext once."""
    codes = ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(n)]
    db.execute(
        "UPDATE users SET recovery_codes = ? WHERE id = ?",
        (json.dumps([hash_password(c) for c in codes]), user_id),
    )
    return codes


def consume_recovery_code(user_id: int, code: str) -> bool:
    row = db.one("SELECT recovery_codes FROM users WHERE id = ?", (user_id,))
    if not row or not row["recovery_codes"]:
        return False
    hashes = json.loads(row["recovery_codes"])
    for i, h in enumerate(hashes):
        if verify_password(h, code.strip()):
            hashes.pop(i)
            db.execute(
                "UPDATE users SET recovery_codes = ? WHERE id = ?",
                (json.dumps(hashes), user_id),
            )
            return True
    return False


def recovery_codes_left(user_id: int) -> int:
    row = db.one("SELECT recovery_codes FROM users WHERE id = ?", (user_id,))
    if not row or not row["recovery_codes"]:
        return 0
    return len(json.loads(row["recovery_codes"]))


# ─────────────────────────── audit ───────────────────────────

def audit(action: str, *, actor: dict | None = None, target: str = "",
          detail: str = "", ip: str = "") -> None:
    db.execute(
        "INSERT INTO audit_log (ts, actor_id, actor, action, target, detail, ip) "
        "VALUES (?,?,?,?,?,?,?)",
        (_now(), (actor or {}).get("id"), (actor or {}).get("email"),
         action, target, detail, ip),
    )


def audit_log(limit: int = 200) -> list[dict]:
    return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

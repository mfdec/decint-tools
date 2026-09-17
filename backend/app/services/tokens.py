"""Login tokens — per-account bearer credentials.

A token looks like `K7P4M-2QRST-9WXYZ`: 15 characters from an unambiguous
alphabet (no O/0, I/1, etc.), grouped 5-5-5 by dashes so it survives being read
aloud or copied. Pasted on the login page it signs the holder in as the owning
account, with no email required — so it is exactly as sensitive as a password.

Storage: we keep only an HMAC-SHA256 of the normalized token, keyed by the
server's session secret. That's deliberate and differs from how *passwords* are
stored (Argon2):

* A token carries ~74 bits of entropy, so offline brute force is infeasible —
  the slow-hash tax that protects weak human passwords buys nothing here.
* A keyed digest is deterministic, so login is a single indexed lookup instead
  of an Argon2 verification against every token in the table.

Normalization means the user can type it lower-case, with or without dashes.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from .. import db
from ..config import settings
from ..services.users import _now, get as _get_user

# Crockford-style alphabet: no 0/O/1/I/L/U to avoid misreads and rude words.
_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_GROUPS = 3
_GROUP_LEN = 5


def _normalize(raw: str) -> str:
    """Uppercase, strip everything but the alphabet — so 'k7p4m 2qrst-9wxyz'
    and 'K7P4M-2QRST-9WXYZ' hash identically."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def _digest(raw: str) -> str:
    key = settings.session_secret.encode()
    return hmac.new(key, _normalize(raw).encode(), hashlib.sha256).hexdigest()


def _format(chars: str) -> str:
    return "-".join(chars[i:i + _GROUP_LEN] for i in range(0, len(chars), _GROUP_LEN))


def generate() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_GROUPS * _GROUP_LEN))
    return _format(body)


# ─────────────────────────── management ───────────────────────────

def create(user_id: int, label: str | None = None) -> str:
    """Mint a token for an account and return the PLAINTEXT once. Only the hash
    is persisted; the caller must show this to the user immediately."""
    for _ in range(5):  # retry on the astronomically unlikely hash collision
        token = generate()
        try:
            db.execute(
                "INSERT INTO login_tokens (user_id, token_hash, label, created_at) "
                "VALUES (?,?,?,?)",
                (user_id, _digest(token), (label or None), _now()),
            )
            return token
        except Exception:
            continue
    raise RuntimeError("could not allocate a unique token")


def list_for(user_id: int) -> list[dict]:
    return db.query(
        "SELECT id, label, created_at, last_used_at, revoked FROM login_tokens "
        "WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )


def revoke(token_id: int) -> bool:
    return db.execute(
        "UPDATE login_tokens SET revoked = 1 WHERE id = ? AND revoked = 0",
        (token_id,),
    ) > 0


def revoke_all(user_id: int) -> int:
    return db.execute(
        "UPDATE login_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
        (user_id,),
    )


# ─────────────────────────── authentication ───────────────────────────

def verify(raw: str) -> dict | None:
    """Resolve a presented token to its owning account, or None. Updates
    last_used_at on success. Revoked tokens never match."""
    if not _normalize(raw):
        return None
    row = db.one(
        "SELECT id, user_id FROM login_tokens WHERE token_hash = ? AND revoked = 0",
        (_digest(raw),),
    )
    if not row:
        return None
    db.execute(
        "UPDATE login_tokens SET last_used_at = ? WHERE id = ?",
        (_now(), row["id"]),
    )
    return _get_user(row["user_id"])

"""Password reset by emailed link.

Until now the only way back into a locked-out account was an operator running
`accounts.py set-password` by hand, which does not scale past the operator.

The design follows the same rules the rest of the auth code already holds to:

* **The link is a bearer credential**, so only an HMAC-SHA256 digest of it is
  stored — keyed by the session secret, exactly as `tokens.py` argues for login
  tokens. 256 bits of `urandom` makes offline guessing pointless, and a keyed
  digest keeps the lookup a single indexed query.
* **Single use, short life.** Requesting a new link invalidates any outstanding
  one, using it burns it, and it expires on its own regardless.
* **Requesting a reset must not confirm an address exists.** That decision
  lives in the router, which returns an identical response either way; this
  module simply does nothing when handed an address it does not know.
* **A completed reset ends every session.** If someone else was already inside
  the account, changing the password is what should evict them — otherwise the
  reset restores access without removing the intruder.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import settings
from . import users


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(raw: str) -> str:
    key = settings.session_secret.encode()
    return hmac.new(key, (raw or "").strip().encode(), hashlib.sha256).hexdigest()


def create(user_id: int, ip: str = "") -> str:
    """Mint a reset token and return the PLAINTEXT once — it is emailed and
    never recoverable afterwards. Any outstanding link for this account dies
    here, so a second request cannot leave two working links in two inboxes."""
    db.execute(
        "UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0",
        (user_id,),
    )
    raw = secrets.token_urlsafe(32)
    expires = (
        datetime.now(timezone.utc)
        + timedelta(minutes=settings.password_reset_ttl_minutes)
    ).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO password_resets (user_id, token_hash, expires_at, created_at, "
        "requested_ip) VALUES (?,?,?,?,?)",
        (user_id, _digest(raw), expires, _now(), ip),
    )
    return raw


def link(raw: str) -> str:
    """The URL to put in the email. Falls back to a bare path when
    PUBLIC_BASE_URL is unset, which is wrong in an email but at least visible."""
    return f"{settings.public_base_url.rstrip('/')}/reset?token={raw}"


def peek(raw: str) -> dict | None:
    """Is this token currently usable? Used by the reset page to fail early
    instead of after the visitor has typed a new password twice."""
    if not raw:
        return None
    row = db.one(
        "SELECT user_id FROM password_resets WHERE token_hash = ? AND used = 0 "
        "AND expires_at > ?",
        (_digest(raw), _now()),
    )
    return users.get(row["user_id"]) if row else None


def complete(raw: str, new_password: str, ip: str = "") -> dict | None:
    """Burn the token and set the new password. Returns the account on success.

    Raises ValueError if the password itself is rejected — and deliberately
    does NOT burn the token in that case, or a weak first attempt would cost
    the visitor the link."""
    row = db.one(
        "SELECT id, user_id FROM password_resets WHERE token_hash = ? AND used = 0 "
        "AND expires_at > ?",
        (_digest(raw), _now()),
    )
    if not row:
        return None
    user = users.get(row["user_id"])
    if not user:
        return None

    users.set_password(user["id"], new_password)  # ValueError → weak password

    db.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
    # A reset is also the way out of a lockout, so clear the counter — but not
    # via clear_failed_logins(), which would stamp a last_login that never
    # happened.
    db.execute(
        "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?",
        (user["id"],),
    )
    users.revoke_all_sessions(user["id"])
    users.audit("password.reset", target=user["email"], detail="via email link", ip=ip)
    return user


def purge_expired() -> int:
    """Housekeeping — used links and dead ones have no reason to persist."""
    return db.execute(
        "DELETE FROM password_resets WHERE used = 1 OR expires_at <= ?", (_now(),)
    )

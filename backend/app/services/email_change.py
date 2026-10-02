"""Changing the account email, confirmed from the new mailbox.

An account's email is two things at once: how its owner signs in, and where a
forgotten-password link is sent. Swapping it on the strength of a typed string
would let a typo lock someone out of their own account, and an address nobody
proved they hold would be a place to send other people's reset links. So the
address changes only when someone opens a link that was mailed *to* it.

Design, in the same spirit as `activation.py`:

* **The link is a signed token, not a stored one.** It grants exactly one
  thing — "this account may now use that address" — to someone who has already
  proved the current password to get it issued, so a self-expiring signed token
  is enough and needs no table. It carries the account id, the address being
  left and the address being claimed.
* **Single use without bookkeeping.** The token names the *old* address, and
  it only works while the account still has it. Once the swap happens the
  token no longer matches anything, so a second click (or a mail scanner
  prefetching the link) cannot replay it.
* **It never confirms whether an address is taken.** Asking for an address that
  belongs to someone else sends nothing and returns exactly what a free one
  does; that decision lives in the router. The check is repeated at
  confirmation time, so two people racing for one address can't both win.
* **The old address is told afterwards.** If someone else made the change, the
  notice to the old mailbox is the only way the real owner finds out.
* **Outstanding reset links die.** A reset link was mailed to the address being
  left; if it survived the swap, whoever can still read that mailbox could use
  it to take the account.
"""

from __future__ import annotations

import sqlite3

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import db
from ..config import settings
from . import mail, users

_tokens = URLSafeTimedSerializer(settings.session_secret, salt="decint-email-change")


def _ttl_seconds() -> int:
    return max(1, int(settings.email_change_ttl_minutes)) * 60


def create(user: dict, new_email: str) -> str:
    return _tokens.dumps(
        {"uid": int(user["id"]), "old": user["email"], "new": new_email.strip().lower()}
    )


def link(token: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/confirm-email?token={token}"


def request(user: dict, new_email: str, ip: str = "") -> bool:
    """Mail the confirmation link to `new_email` if it is free.

    Returns whether one went out; the caller must not let that reach the
    client, or this becomes a way to ask "is this address registered?".
    """
    new = new_email.strip().lower()
    if users.get_by_email(new):
        users.audit(
            "email.change_requested", actor=user, target=new,
            detail="address already in use", ip=ip,
        )
        return False
    subject, text = mail.email_change_confirm(
        link(create(user, new)), settings.email_change_ttl_minutes, ip
    )
    mail.send_soon(new, subject, text)
    users.audit("email.change_requested", actor=user, target=new, ip=ip)
    return True


def _load(token: str) -> tuple[dict, str] | None:
    """The account and the address the token claims, or None if the link is
    bad, expired, already used, or the account is no longer in a state to
    change (suspended, deleted, or its email moved on some other way)."""
    if not token:
        return None
    try:
        data = _tokens.loads(token, max_age=_ttl_seconds())
        uid, old, new = int(data["uid"]), data["old"], data["new"]
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
    user = users.get(uid)
    if not user or user["email"] != old or user["status"] != "active":
        return None
    return user, new


def complete(token: str, ip: str = "") -> dict | None:
    """Swap the address. Returns {"user", "old", "new"}, or None for anything
    the router should answer with one generic "that link isn't valid"."""
    loaded = _load(token)
    if not loaded:
        return None
    user, new = loaded
    old = user["email"]

    # Re-checked here, not just at request time: the address may have been
    # taken in the minutes since the link went out.
    if users.get_by_email(new):
        return None
    try:
        users.update(user["id"], email=new, email_verified=1)
    except sqlite3.IntegrityError:
        # Lost a race between the check above and the write.
        return None

    db.execute(
        "UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0",
        (user["id"],),
    )
    users.audit(
        "email.changed", actor=user, target=new, detail=f"was {old}", ip=ip
    )
    subject, text = mail.email_changed_notice(new, ip)
    mail.send_soon(old, subject, text)
    return {"user": users.get(user["id"]), "old": old, "new": new}

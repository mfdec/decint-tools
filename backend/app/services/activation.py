"""Account activation by emailed link.

With `SIGNUP_DEFAULT_STATUS=pending` every new account waits for an operator
to press "active" in the console. That is the right default for a private
tool and the wrong one for a shop: the only thing standing between a visitor
and a paid plan should be their card, not the operator's inbox. Proof of the
mailbox is a fair substitute for the operator's glance — it is what the
approval was mostly checking anyway.

Design, kept deliberately lighter than `reset.py`:

* **The link is a signed token, not a stored one.** A reset link grants entry
  to an account, so only a digest of it is stored. An activation link grants
  one thing — flipping *this* account from pending to active — which the
  holder already wants, so a signed, self-expiring token is enough and needs
  no table. The token carries the account id and the address it was mailed
  to; if the address on the account changes before it is clicked, it dies.
* **Idempotent.** Clicking twice, or a mail scanner prefetching the link,
  activates once and reports success both times. Nothing here is worth
  protecting from a double click.
* **It never confirms an address exists.** Resending is answered identically
  whether the address is pending, active or unknown; that decision lives in
  the router, this module simply does nothing for the latter two.
* **Operators keep their override.** The console's "status: active" still
  works — the link is an alternative to it, not a replacement.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import settings
from . import mail, users

_tokens = URLSafeTimedSerializer(settings.session_secret, salt="decint-activation")


def enabled() -> bool:
    """Is self-activation on for this deployment right now?

    All three legs are needed: the switch, a pending default (an account that
    starts active has nothing to activate), and a relay to send the link with.
    """
    return bool(
        settings.signup_email_activation
        and settings.signup_default_status == "pending"
        and mail.available()
    )


def _ttl_seconds() -> int:
    return max(1, int(settings.signup_activation_ttl_hours)) * 3600


def safe_next(path: str | None) -> str:
    """A local path to continue to after signing in, or "". The same rule the
    login page applies to `?next=`: an absolute URL or a protocol-relative one
    would turn our own activation mail into an open redirect."""
    p = (path or "").strip()
    if p.startswith("/") and not p.startswith("//") and not p.startswith("/\\"):
        return p[:200]
    return ""


def create(user: dict, next_path: str = "") -> str:
    return _tokens.dumps(
        {"uid": int(user["id"]), "email": user["email"], "next": safe_next(next_path)}
    )


def link(token: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/activate?token={token}"


def _load(token: str) -> dict | None:
    if not token:
        return None
    try:
        data = _tokens.loads(token, max_age=_ttl_seconds())
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "uid" not in data:
        return None
    user = users.get(int(data["uid"]))
    if not user or user["email"] != data.get("email"):
        return None
    return {"user": user, "next": safe_next(data.get("next"))}


def peek(token: str) -> dict | None:
    """Is this link good? Lets the activation page fail early with a reason
    instead of a generic error."""
    return _load(token)


def complete(token: str, ip: str = "") -> dict | None:
    """Activate the account the link names.

    Returns {"outcome": "activated" | "already", "user", "next"}, or None for
    a link that is bad, expired or points at an account that has since been
    suspended — a suspension is an operator's decision, and an old email must
    not be a way around it.
    """
    loaded = _load(token)
    if not loaded:
        return None
    user = loaded["user"]
    if user["status"] == "active":
        return {"outcome": "already", "user": user, "next": loaded["next"]}
    if user["status"] != "pending":
        return None
    users.update(user["id"], status="active", email_verified=1)
    users.audit(
        "account.activated", target=user["email"], detail="via email link", ip=ip
    )
    name = user.get("username") or user["email"]
    subject, text = mail.account_activated(name)
    mail.send_soon(user["email"], subject, text)
    mail.notify_admins(*mail.account_activated_alert(user["email"], user.get("username") or "", ip))
    return {"outcome": "activated", "user": users.get(user["id"]), "next": loaded["next"]}


def send(user: dict, next_path: str = "", ip: str = "") -> None:
    """Mail the account its link. Called at signup, and again on request."""
    url = link(create(user, next_path))
    subject, text = mail.signup_activate(
        user.get("username") or user["email"], url, settings.signup_activation_ttl_hours
    )
    mail.send_soon(user["email"], subject, text)
    users.audit("account.activation_sent", target=user["email"], ip=ip)


def resend(email: str, ip: str = "") -> bool:
    """Send a fresh link if — and only if — the address belongs to a pending
    account. Returns whether one went out; the caller must not let that
    reach the client."""
    user = users.get_by_email(email)
    if not user or user["status"] != "pending":
        return False
    send(user, ip=ip)
    return True

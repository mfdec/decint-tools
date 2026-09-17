"""Second factor: authenticator app (TOTP), email code, SMS code.

Availability differs and that is deliberate:

* **TOTP works out of the box** — no external service, no credentials, no cost.
  It is also the strongest of the three. Treat it as the default.
* **Email** needs SMTP settings. Without them `email_available()` is False and
  the method cannot be enabled, rather than silently failing at login.
* **SMS** needs a Twilio account. Same fail-closed behaviour. Note SMS is the
  weakest second factor — SIM-swap attacks defeat it — so it is offered because
  it was asked for, not because it should be anyone's first choice.

Codes are stored hashed with the same Argon2 hasher as passwords, are
single-use, and expire.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pyotp

from .. import db
from ..config import settings
from . import mail, users

ISSUER = "DECINT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────── availability ───────────────────────────

def email_available() -> bool:
    return mail.available()


def sms_available() -> bool:
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_from
    )


def methods_for(user: dict) -> list[str]:
    out = []
    if user.get("totp_enabled"):
        out.append("totp")
    if user.get("email_otp_enabled") and email_available():
        out.append("email")
    if user.get("sms_otp_enabled") and sms_available():
        out.append("sms")
    return out


def enrolled(user: dict) -> bool:
    return bool(methods_for(user))


# ─────────────────────────── TOTP ───────────────────────────

def totp_begin(user: dict) -> dict:
    """Create (but don't yet enable) a TOTP secret. Returns provisioning data."""
    secret = pyotp.random_base32()
    db.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user["id"]))
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user["email"], issuer_name=ISSUER
    )
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": _qr_svg(uri)}


def totp_confirm(user: dict, code: str) -> bool:
    """Verify the first code and switch TOTP on."""
    row = db.one("SELECT totp_secret FROM users WHERE id = ?", (user["id"],))
    if not row or not row["totp_secret"]:
        return False
    if not pyotp.TOTP(row["totp_secret"]).verify(code.strip(), valid_window=1):
        return False
    db.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (user["id"],))
    return True


def totp_verify(user: dict, code: str) -> bool:
    if not user.get("totp_enabled") or not user.get("totp_secret"):
        return False
    # valid_window=1 accepts the adjacent 30s step, covering clock drift.
    return pyotp.TOTP(user["totp_secret"]).verify(code.strip(), valid_window=1)


def _qr_svg(uri: str) -> str:
    """Render the otpauth URI as an inline SVG so the frontend needs no QR lib."""
    try:
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
        import io

        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return ""


# ─────────────────────────── one-time codes ───────────────────────────

def _issue_code(user_id: int, method: str) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (
        datetime.now(timezone.utc) + timedelta(seconds=settings.otp_ttl_seconds)
    ).isoformat(timespec="seconds")
    # Invalidate any outstanding code for this method first.
    db.execute(
        "UPDATE otp_codes SET used = 1 WHERE user_id = ? AND method = ? AND used = 0",
        (user_id, method),
    )
    db.execute(
        "INSERT INTO otp_codes (user_id, method, code_hash, expires_at, created_at) "
        "VALUES (?,?,?,?,?)",
        (user_id, method, users.hash_password(code), expires, _now()),
    )
    return code


def verify_code(user_id: int, method: str, code: str) -> bool:
    rows = db.query(
        "SELECT id, code_hash FROM otp_codes WHERE user_id = ? AND method = ? "
        "AND used = 0 AND expires_at > ? ORDER BY id DESC LIMIT 5",
        (user_id, method, _now()),
    )
    for r in rows:
        if users.verify_password(r["code_hash"], code.strip()):
            db.execute("UPDATE otp_codes SET used = 1 WHERE id = ?", (r["id"],))
            return True
    return False


# ─────────────────────────── delivery ───────────────────────────

def send_email_code(user: dict, background: bool = True) -> bool:
    """Issue a code and mail it.

    The code row is always written synchronously — it must exist before the
    caller tells the user to go and read their inbox. Only the SMTP round-trip
    is deferred, because these run inside `async def` handlers where a slow
    relay would otherwise stall every other request in the process.

    With background=True the return value means "queued", not "delivered".
    Pass background=False (off the event loop) when the answer matters."""
    if not email_available():
        return False
    code = _issue_code(user["id"], "email")
    body = mail.signin_code(user["email"], code, settings.otp_ttl_seconds)
    subject = "DECINT sign-in code"
    if background:
        mail.send_soon(user["email"], subject, body)
        return True
    return mail.send(user["email"], subject, body)


def send_sms_code(user: dict, background: bool = True) -> bool:
    if not sms_available() or not user.get("phone"):
        return False
    code = _issue_code(user["id"], "sms")
    if background:
        # Same reasoning as the email path: an HTTP call to Twilio inside
        # an async handler blocks the loop for as long as Twilio takes.
        threading.Thread(
            target=_post_sms, args=(user["phone"], code), daemon=True
        ).start()
        return True
    return _post_sms(user["phone"], code)


def _post_sms(phone: str, code: str) -> bool:
    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{settings.twilio_account_sid}/Messages.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={
                "From": settings.twilio_from,
                "To": phone,
                "Body": f"DECINT sign-in code: {code}",
            },
            timeout=15.0,
        )
        return r.status_code < 300
    except httpx.HTTPError:
        return False


def send_challenge(user: dict, method: str, background: bool = True) -> bool:
    if method == "email":
        return send_email_code(user, background=background)
    if method == "sms":
        return send_sms_code(user, background=background)
    return method == "totp"  # nothing to send


# ─────────────────────────── verification entry point ───────────────────────────

def verify(user: dict, method: str, code: str) -> bool:
    if method == "totp":
        return totp_verify(user, code)
    if method in ("email", "sms"):
        return verify_code(user["id"], method, code)
    if method == "recovery":
        return users.consume_recovery_code(user["id"], code)
    return False


def disable_all(user_id: int) -> None:
    db.execute(
        "UPDATE users SET totp_enabled = 0, totp_secret = NULL, "
        "email_otp_enabled = 0, sms_otp_enabled = 0, recovery_codes = NULL "
        "WHERE id = ?",
        (user_id,),
    )
    db.execute("DELETE FROM otp_codes WHERE user_id = ?", (user_id,))

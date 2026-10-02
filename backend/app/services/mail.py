"""Outbound email — the one place in the app that talks SMTP.

Everything the sign-in flow sends goes through here: second-factor codes,
signup and approval notices, and password resets. It is its own module for two
reasons.

**Fail loudly, not silently.** Without SMTP_HOST and SMTP_FROM `available()` is
False, and callers check it so a feature that needs email is never *offered*
rather than being offered and then failing at the moment someone depends on it.
When a send does fail it is logged — the previous inline SMTP helper swallowed
every exception, which made a wrong password, a blocked port and a delivered
message all look identical from the outside.

**Never block the event loop.** The auth routes are `async def`, so a blocking
`smtplib` call inside one stalls *every* request in the process for as long as
the far end takes to answer — up to TIMEOUT seconds. `send_soon()` hands the
message to a worker thread and returns immediately; use it wherever the caller
does not report the outcome to the user, which is nearly everywhere. Reserve
`send()` for the paths that do.
"""

from __future__ import annotations

import logging
import smtplib
import threading
import html as _html
import re
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from ..config import settings

log = logging.getLogger("decint.mail")

TIMEOUT = 15  # seconds per SMTP operation


def available() -> bool:
    """True when the app has both a relay to send through and an address to
    send as. Both are required — a From header alone delivers nothing."""
    return bool(settings.smtp_host and settings.smtp_from)


def _from_header() -> str:
    return formataddr((settings.smtp_from_name or "", settings.smtp_from))


def _domain() -> str | None:
    return settings.smtp_from.split("@")[-1] if "@" in settings.smtp_from else None


def send(to: str, subject: str, body: str) -> bool:
    """Deliver one message, blocking until the relay accepts or refuses it."""
    if not available():
        log.warning("mail not configured — dropped %r to %s", subject, to)
        return False
    if not to:
        return False

    msg = EmailMessage()
    msg["From"] = _from_header()
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=_domain())
    # Transactional, not bulk: ask well-behaved receivers not to send vacation
    # replies to an address nobody reads.
    msg["Auto-Submitted"] = "auto-generated"
    # Plain text is the canonical copy — every template is written as text —
    # and the HTML alternative wraps that same text in the site's theme, so a
    # client that shows either gets the same words.
    msg.set_content(body)
    msg.add_alternative(render_html(subject, body), subtype="html")

    try:
        if settings.smtp_ssl:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT)
        with server:
            if not settings.smtp_ssl and settings.smtp_starttls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        log.info("mail sent: %r → %s", subject, to)
        return True
    except Exception as e:  # noqa: BLE001 — report, never raise
        # Deliberately broad. A notification that cannot be delivered must not
        # become a 500 on someone's login or signup; the log line is what makes
        # it debuggable instead of invisible.
        log.error("mail failed: %r → %s — %s: %s", subject, to, type(e).__name__, e)
        return False


def send_soon(to: str, subject: str, body: str) -> None:
    """Queue a message on a worker thread and return immediately.

    Use this on request paths. The caller gets no delivery result, which is the
    trade: a slow or dead relay costs the user nothing."""
    if not available() or not to:
        return
    threading.Thread(
        target=send, args=(to, subject, body), name="decint-mail", daemon=True
    ).start()


def admin_recipients() -> list[str]:
    """Where operational alerts go: ADMIN_ALERT_EMAIL if set, otherwise every
    admin account's own address."""
    if settings.admin_alert_email:
        return [a.strip() for a in settings.admin_alert_email.split(",") if a.strip()]
    from . import users  # local import keeps this module free of app imports

    return [
        r["email"] for r in users.listing(role="admin", limit=50)["users"] if r.get("email")
    ]


def notify_admins(subject: str, body: str) -> None:
    if not settings.email_notifications:
        return
    for addr in admin_recipients():
        send_soon(addr, subject, body)


def _site() -> str:
    return settings.public_base_url.rstrip("/") or "the site"


# ─────────────────────────── the theme ───────────────────────────
#
# Every message the site sends wears the console's lockup: the violet shield,
# the tracked DECINT wordmark on the near-black plate, and the mono eyebrow
# beneath it — the same header the landing page opens with. Table layout and
# inline styles only, because that is what mail clients actually render; the
# colours are the tokens from frontend/app/globals.css, copied rather than
# imported so this module stays free of the frontend.

_BG = "#0b0c13"          # --color-bg
_SURFACE = "#10121c"     # --color-surface
_TEXT = "#e9e9ed"        # --color-text
_MUTED = "#aba7be"       # --color-neutral-400
_DIM = "#6d6980"         # --color-neutral-600
_ACCENT = "#8d5bf6"      # --color-accent
_DIVIDER = "#23242f"     # --color-divider flattened onto the surface
_SANS = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
_EYEBROW = "OSINT &amp; NETWORK INTELLIGENCE"

_URL = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)])")


def _linkify(escaped: str) -> str:
    return _URL.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:{_ACCENT};text-decoration:underline;'
                  f'word-break:break-all">{m.group(1)}</a>',
        escaped,
    )


def _paragraphs(body: str) -> str:
    """Turn a template's text into paragraphs.

    Blank lines separate paragraphs. A paragraph whose every line is indented
    (the `  email     …` tables in the alerts) is a code block, kept in mono
    with its spacing; a lone indented line is a link or a code presented on
    its own, which gets the same treatment. Everything else is prose, with
    single newlines kept as line breaks.
    """
    out = []
    for chunk in re.split(r"\n\s*\n", body.strip("\n")):
        lines = chunk.split("\n")
        if lines and all(ln.startswith("  ") or not ln.strip() for ln in lines):
            text = _linkify(_html.escape("\n".join(ln[2:] for ln in lines)))
            out.append(
                f'<pre style="margin:0 0 18px;padding:14px 16px;background:{_BG};'
                f'border:1px solid {_DIVIDER};border-radius:8px;font-family:{_MONO};'
                f'font-size:12.5px;line-height:1.6;color:{_TEXT};white-space:pre-wrap;'
                f'word-break:break-all">{text}</pre>'
            )
        else:
            text = _linkify(_html.escape(chunk)).replace("\n", "<br>")
            out.append(
                f'<p style="margin:0 0 16px;font-family:{_SANS};font-size:15px;'
                f'line-height:1.65;color:{_TEXT}">{text}</p>'
            )
    return "".join(out)


def render_html(subject: str, body: str) -> str:
    """The HTML alternative for one message: the lockup, then the text."""
    base = settings.public_base_url.rstrip("/")
    icon = (
        f'<img src="{base}/icon-192.png" width="22" height="22" alt="" '
        f'style="display:block;width:22px;height:22px;border:0">'
        if base else ""
    )
    site = base or "https://decint.tools"
    host = site.replace("https://", "").replace("http://", "")
    preheader = _html.escape(body.strip().split("\n", 1)[0][:140])
    title = _html.escape(subject)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{_BG};color:{_TEXT}" bgcolor="{_BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{_BG}">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_BG}" style="background:{_BG}">
<tr><td align="center" style="padding:32px 16px">
  <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%">
    <!-- lockup: shield + tracked wordmark on the plate, as on the site -->
    <tr><td bgcolor="{_SURFACE}" style="background:{_SURFACE};border:1px solid {_DIVIDER};border-bottom:0;border-radius:12px 12px 0 0;padding:22px 28px">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="padding-right:10px;vertical-align:middle">{icon}</td>
        <td style="vertical-align:middle;font-family:{_SANS};font-size:16px;font-weight:600;letter-spacing:0.18em;color:{_TEXT}">DECINT</td>
      </tr></table>
    </td></tr>
    <!-- eyebrow -->
    <tr><td bgcolor="{_BG}" style="background:{_BG};border-left:1px solid {_DIVIDER};border-right:1px solid {_DIVIDER};padding:22px 28px 6px">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="width:24px;border-top:1px solid {_ACCENT};font-size:0;line-height:0">&nbsp;</td>
        <td style="padding-left:10px;font-family:{_MONO};font-size:11px;letter-spacing:0.16em;text-transform:uppercase;color:{_ACCENT}">{_EYEBROW}</td>
      </tr></table>
    </td></tr>
    <!-- subject as the heading, then the text -->
    <tr><td bgcolor="{_BG}" style="background:{_BG};border-left:1px solid {_DIVIDER};border-right:1px solid {_DIVIDER};padding:12px 28px 8px">
      <h1 style="margin:0 0 18px;font-family:{_SANS};font-size:22px;font-weight:600;line-height:1.25;letter-spacing:-0.01em;color:{_TEXT}">{title}</h1>
      {_paragraphs(body)}
    </td></tr>
    <!-- footer -->
    <tr><td bgcolor="{_SURFACE}" style="background:{_SURFACE};border:1px solid {_DIVIDER};border-top:1px solid {_DIVIDER};border-radius:0 0 12px 12px;padding:16px 28px;font-family:{_MONO};font-size:11px;letter-spacing:0.08em;color:{_DIM}">
      <a href="{site}" style="color:{_DIM};text-decoration:none">{_html.escape(host)}</a> &nbsp;·&nbsp; every signal, one console
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


# ─────────────────────────── messages ───────────────────────────
#
# Plain text on purpose. These are transactional, they are short, and a
# text/plain body renders identically everywhere while attracting none of the
# spam scoring that a marketing-shaped HTML mail does.


def signin_code(to: str, code: str, ttl_seconds: int) -> str:
    return (
        f"Your DECINT sign-in code is {code}\n\n"
        f"It expires in {ttl_seconds // 60} minutes and can be used once.\n\n"
        "If you did not try to sign in, someone else has your password — "
        f"change it at {_site()} as soon as you can.\n"
    )


def signup_received(username: str, pending: bool) -> tuple[str, str]:
    if pending:
        return (
            "Your DECINT account is awaiting approval",
            f"Thanks for signing up, {username}.\n\n"
            "Your account has been created but needs to be approved by an "
            "operator before you can sign in. You'll get another email the "
            "moment that happens — there's nothing else for you to do.\n",
        )
    return (
        "Welcome to DECINT",
        f"Your DECINT account is ready, {username}.\n\n"
        f"Sign in at {_site()}/login\n\n"
        "Once you're in, we strongly recommend turning on two-factor "
        "authentication under Settings — an authenticator app is the "
        "strongest of the options offered.\n",
    )


def signup_activate(username: str, url: str, ttl_hours: int) -> tuple[str, str]:
    """The link that stands in for an operator's approval. Plain and short:
    the whole point is that nobody reads it twice."""
    return (
        "Activate your DECINT account",
        f"Thanks for signing up, {username}.\n\n"
        "One click activates your account:\n\n"
        f"  {url}\n\n"
        f"The link works for {ttl_hours} hours. If it has expired, request a "
        f"new one from {_site()}/activate\n\n"
        "If you didn't create this account, ignore this email and it will "
        "never be activated.\n",
    )


def account_activated(username: str) -> tuple[str, str]:
    return (
        "Your DECINT account is active",
        f"You're in, {username}.\n\n"
        f"Sign in at {_site()}/login\n\n"
        "Once you're in, we strongly recommend turning on two-factor "
        "authentication under Settings — an authenticator app is the "
        "strongest of the options offered.\n",
    )


def account_activated_alert(email: str, username: str, ip: str) -> tuple[str, str]:
    return (
        f"DECINT account activated: {username or email}",
        f"An account activated itself from its emailed link.\n\n"
        f"  email     {email}\n"
        f"  username  {username or '—'}\n"
        f"  from IP   {ip or 'unknown'}\n\n"
        "Nothing to do — this is for your records.\n",
    )


def signup_alert(
    email: str, username: str, status: str, ip: str, self_activates: bool = False
) -> tuple[str, str]:
    action = (
        "The account activates itself from the link we emailed it; you'll get "
        "another note when that happens. Nothing to do.\n"
        if status == "pending" and self_activates
        else "This account cannot sign in until you approve it.\n"
        f"Approve it in the console at {_site()}/console — the `users` app\n"
        "(admin only, Ctrl+7) ▸ find the account ▸ manage ▸ status: active.\n"
        if status == "pending"
        else "The account is active and can sign in now.\n"
    )
    return (
        f"New DECINT signup: {username}",
        f"A new account was created.\n\n"
        f"  email     {email}\n"
        f"  username  {username}\n"
        f"  status    {status}\n"
        f"  from IP   {ip or 'unknown'}\n\n" + action,
    )


def subscription_alert(
    email: str, username: str, plan_name: str, previous_tier: str, until: str
) -> tuple[str, str]:
    """Sent to the operators when an account starts paying — a first paid plan
    or a move between paid tiers. A quiet renewal of the same plan does not
    reach here, so this alert always means a new paying customer."""
    first_time = previous_tier in ("", "free")
    headline = (
        f"subscribed to {plan_name}"
        if first_time
        else f"changed to {plan_name} (was {previous_tier})"
    )
    return (
        f"💳 Paid plan: {username or email} {headline}",
        f"An account {headline}.\n\n"
        f"  email     {email}\n"
        f"  username  {username or '—'}\n"
        f"  plan      {plan_name}\n"
        f"  until     {until or 'no expiry set'}\n\n"
        "Nothing to do — this is for your records.\n",
    )


def account_approved(username: str) -> tuple[str, str]:
    return (
        "Your DECINT account is approved",
        f"Good news, {username} — your account has been approved.\n\n"
        f"You can sign in now at {_site()}/login\n",
    )


def password_reset(url: str, ttl_minutes: int, ip: str) -> tuple[str, str]:
    return (
        "Reset your DECINT password",
        "Someone asked to reset the password on your DECINT account.\n\n"
        f"{url}\n\n"
        f"The link works once and expires in {ttl_minutes} minutes.\n"
        f"It was requested from {ip or 'an unknown address'}.\n\n"
        "If that wasn't you, ignore this email — your password has not been "
        "changed and the link above expires on its own.\n",
    )


def access_expiring(plan_name: str, days_left: int, tier: str) -> tuple[str, str]:
    """Only the prepaid rail needs this.

    A card subscription renews itself, so warning about it would be noise. A
    crypto payment cannot renew itself — nothing on-chain lets us pull one — so
    without this email the first thing a customer learns is that their access
    stopped.
    """
    when = "today" if days_left <= 0 else (
        "tomorrow" if days_left == 1 else f"in {days_left} days"
    )
    return (
        f"Your DECINT {plan_name} access ends {when}",
        f"Your prepaid {plan_name} access ends {when}.\n\n"
        "Crypto payments buy a fixed block of time and cannot renew themselves, "
        "so nothing will be charged and nothing will happen automatically.\n\n"
        f"Extend it at {_site()}/pricing\n\n"
        f"Paying by card instead sets up a subscription that renews on its own: "
        f"{_site()}/billing\n\n"
        "When the period ends your account drops to the free tier — your data "
        "and settings stay exactly where they are.\n",
    )


def access_lapsed(plan_name: str) -> tuple[str, str]:
    return (
        "Your DECINT access has ended",
        f"Your {plan_name} access has ended and the account is now on the free "
        "tier.\n\n"
        "Nothing has been deleted — your account, settings and history are all "
        "still there, on the free tier's lower query allowance.\n\n"
        f"Pick a plan again at {_site()}/pricing\n",
    )


def password_changed(ip: str) -> tuple[str, str]:
    return (
        "Your DECINT password was changed",
        "The password on your DECINT account was just changed, and every "
        f"active session was signed out.\n\nRequested from {ip or 'an unknown address'}.\n\n"
        "If this wasn't you, reset your password immediately at "
        f"{_site()}/forgot and contact your operator.\n",
    )


def password_changed_self(ip: str) -> tuple[str, str]:
    """The in-session counterpart of `password_changed`: the person was signed
    in and typed the old password, and the session they did it from stays."""
    return (
        "Your DECINT password was changed",
        "The password on your DECINT account was just changed from your account "
        "page, and every other session was signed out.\n\n"
        f"Requested from {ip or 'an unknown address'}.\n\n"
        "If this wasn't you, reset your password immediately at "
        f"{_site()}/forgot and contact your operator.\n",
    )


def email_change_confirm(url: str, ttl_minutes: int, ip: str) -> tuple[str, str]:
    """Goes to the NEW address. Clicking the link is the proof that it's theirs."""
    return (
        "Confirm your new DECINT email address",
        "Someone asked to use this address for a DECINT account.\n\n"
        f"{url}\n\n"
        f"The link works for {ttl_minutes} minutes. Until you open it, nothing "
        "changes — the account keeps signing in with its current address.\n"
        f"It was requested from {ip or 'an unknown address'}.\n\n"
        "If that wasn't you, ignore this email.\n",
    )


def email_changed_notice(new_email: str, ip: str) -> tuple[str, str]:
    """Goes to the OLD address, after the swap. It is the only warning the real
    owner gets if someone else made the change."""
    return (
        "Your DECINT email address was changed",
        f"The email address on your DECINT account was just changed to {new_email}.\n\n"
        f"Confirmed from {ip or 'an unknown address'}.\n\n"
        "From now on you sign in, and receive password resets, at the new address.\n\n"
        "If this wasn't you, contact your operator immediately.\n",
    )


# ─────────────────────────── support tickets ───────────────────────────
# There is no separate staff inbox: every one of these links lands on the
# same thread page the customer sees, so replying there is how staff answer.

def support_ticket_opened(ticket_id: int, reason: str, subject: str, user: dict) -> tuple[str, str]:
    who = user.get("username") or user.get("email")
    return (
        f"🎫 New {reason} ticket: {subject}",
        f"A new support ticket was opened.\n\n"
        f"  from     {who}\n"
        f"  reason   {reason}\n"
        f"  subject  {subject}\n\n"
        f"{_site()}/support/{ticket_id}\n",
    )


def support_ticket_followup(ticket_id: int, subject: str, user: dict) -> tuple[str, str]:
    who = user.get("username") or user.get("email")
    return (
        f"🎫 Reply on ticket: {subject}",
        f"{who} replied to their support ticket.\n\n"
        f"{_site()}/support/{ticket_id}\n",
    )


def support_ticket_reply(ticket_id: int, subject: str) -> tuple[str, str]:
    return (
        f"Reply on your ticket: {subject}",
        "An admin replied to your ticket.\n\n"
        f"{_site()}/support/{ticket_id}\n",
    )


def support_ticket_status(ticket_id: int, subject: str, status: str) -> tuple[str, str]:
    verb = {"resolved": "marked resolved", "closed": "closed", "open": "reopened"}.get(
        status, status
    )
    return (
        f"Your ticket was {verb}: {subject}",
        f"An admin {verb} your ticket.\n\n"
        f"{_site()}/support/{ticket_id}\n",
    )

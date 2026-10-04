"""Login, second factor, session, and self-serve MFA enrolment."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

import threading
import time

from .. import auth
from ..config import settings
from ..services import (
    activation, captcha, email_change, mail, moderation, reset, tokens,
    twofactor, usage, users,
)
from ..services.analytics import client_ip
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/auth", tags=["auth"])

# Signup throttle, per IP. In-process like the captcha risk counter.
_signups: dict[str, list[float]] = {}
_signup_lock = threading.Lock()


def _signup_allowed(ip: str) -> bool:
    now = time.time()
    with _signup_lock:
        window = [t for t in _signups.get(ip, []) if t > now - 3600]
        _signups[ip] = window
        return len(window) < settings.signup_max_per_ip_per_hour


def _note_signup(ip: str) -> None:
    with _signup_lock:
        _signups.setdefault(ip, []).append(time.time())


# Reset requests, per IP. Unlike signup this checks and records in one step:
# there is no later validation that could legitimately refund the quota, and an
# unauthenticated endpoint that emails a stranger is exactly the one you do not
# want someone able to fire in a loop.
_reset_requests: dict[str, list[float]] = {}
_reset_lock = threading.Lock()


def _reset_allowed(ip: str) -> bool:
    now = time.time()
    with _reset_lock:
        window = [t for t in _reset_requests.get(ip, []) if t > now - 3600]
        _reset_requests[ip] = window
        if len(window) >= settings.password_reset_max_per_ip_per_hour:
            return False
        window.append(now)
        return True


# ─────────────────────────── schemas ───────────────────────────

class LoginRequest(BaseModel):
    email: str = ""
    password: str = ""
    token: str = ""          # legacy break-glass operator token
    captcha: str = ""


class SignupRequest(BaseModel):
    email: str
    username: str
    password: str
    captcha: str = ""
    # Where to continue after signing in — the pricing page hands people here
    # with the plan they were about to buy, and the activation link carries it
    # so they land back on it afterwards. Local paths only; see activation.safe_next.
    next: str = ""


class ActivateRequest(BaseModel):
    token: str


class ResendActivationRequest(BaseModel):
    email: str
    captcha: str = ""


class LoginTokenRequest(BaseModel):
    token: str = ""


class MfaVerifyRequest(BaseModel):
    challenge: str
    method: str
    code: str


class MfaEnableRequest(BaseModel):
    method: str
    code: str = ""
    phone: str = ""


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class EmailChangeRequest(BaseModel):
    new_email: str
    current_password: str


class EmailConfirmRequest(BaseModel):
    token: str


class AccountDeleteRequest(BaseModel):
    current_password: str
    # Typed by the person, not filled in by the page: the one irreversible
    # action on the profile page should not be one stray click away.
    confirm: str = ""


class ForgotPasswordRequest(BaseModel):
    email: str
    captcha: str = ""


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


def _email_problem(email: str) -> str | None:
    """Why this address can't be used, or None. Expects it already stripped and
    lowercased. Shared by signup and the profile page's email change, so the two
    can't drift into accepting different things."""
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
        return "Enter a valid email address."
    # The local part is displayed in places, so it gets the same treatment.
    if moderation.is_profane(email.split("@")[0]):
        return "That email address isn't accepted."
    return None


def _require_current_password(user: dict, password: str, ip: str) -> None:
    """Gate for sensitive changes made from inside a session.

    A stolen session cookie is not the same thing as knowing the password, so it
    must not be enough to change the password or the email. Wrong guesses count
    against the same lockout the login uses — otherwise these endpoints would be
    an unthrottled way to grind at the password from a hijacked session.
    """
    if users.is_locked(user):
        raise HTTPException(
            status_code=423,
            detail="Too many failed attempts. Try again in a few minutes.",
        )
    if not users.verify_password(user["password_hash"], password):
        users.note_failed_login(user["id"])
        users.audit("password.check_failed", actor=user, ip=ip)
        raise HTTPException(status_code=401, detail="Current password is incorrect.")


def _require_active(user: dict, ip: str) -> None:
    """A correct password on an inactive account is still a refusal — but the
    old message ("Account is pending.") told people neither what had happened
    nor what to do about it, which is most of what made a held-back signup look
    like a broken login."""
    if user["status"] == "active":
        return
    if user["status"] == "pending" and activation.enabled():
        detail = (
            "Your account isn't activated yet. Use the link in the email we "
            "sent you — or request a new one below."
        )
    elif user["status"] == "pending":
        detail = (
            "Your account is waiting for an operator to approve it. You'll get an "
            "email as soon as it's active."
        )
    else:
        detail = "This account has been suspended. Contact your operator."
    users.audit("login.blocked", target=user["email"], detail=user["status"], ip=ip)
    raise HTTPException(status_code=403, detail=detail)


def _public(user: dict) -> dict:
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "username": user.get("username"),
        "role": user.get("role"),
        "tier": user.get("tier"),
        "can_reveal_secrets": usage.reveals_secrets(user),
        "status": user.get("status"),
        "mfa": twofactor.methods_for(user),
        "break_glass": bool(user.get("break_glass")),
    }


# ─────────────────────────── login ───────────────────────────

@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")

    # Break-glass: only while the system has no accounts at all.
    if users.count() == 0:
        if settings.operator_token and body.token != settings.operator_token:
            raise HTTPException(status_code=401, detail="Invalid operator token.")
        auth.issue_break_glass(response)
        users.audit("login.break_glass", ip=ip)
        return {
            "authenticated": True,
            "user": {"id": 0, "email": "operator@localhost", "role": "admin",
                     "tier": "enterprise", "status": "active", "mfa": [],
                     "can_reveal_secrets": True, "break_glass": True},
            "warning": "No accounts exist yet. Create an admin account — this "
                       "bootstrap login closes as soon as one does.",
        }

    # Risk gate before touching the password, so a bot that hasn't solved a
    # captcha never gets to spend our CPU on Argon2.
    if captcha.required_for_login(ip):
        ok, reason = captcha.verify(body.captcha, ip)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=reason,
                headers={"X-Captcha-Required": "1"},
            )

    user = users.get_by_email(body.email)

    # Lockout MUST be evaluated before the password is checked. Checking it
    # afterwards (as this originally did) means a locked account still verifies
    # every guess and only refuses once the attacker has already succeeded —
    # which is not a lockout at all.
    #
    # The IP-level counter is included so the response is identical whether or
    # not the address is registered: an account-only rule would let an attacker
    # tell real addresses from fake ones by seeing which ones eventually lock.
    too_many = captcha.attempt_count(ip, body.email) >= settings.login_max_attempts
    if (user and users.is_locked(user)) or too_many:
        users.audit("login.locked", target=body.email, ip=ip)
        raise HTTPException(
            status_code=423,
            detail="Too many failed attempts. Try again in a few minutes.",
        )

    # Same response whether the account is missing or the password is wrong, so
    # this endpoint can't be used to enumerate which emails are registered.
    # verify_password runs even when there is no such user, against a dummy
    # hash, so the two paths cost the same wall-clock time — otherwise the
    # response latency itself reveals whether the address exists.
    valid = (
        users.verify_password(user["password_hash"], body.password)
        if user
        else users.verify_dummy(body.password)
    )
    if not user or not valid:
        captcha.note_failure(ip)                          # drives the captcha
        captcha.note_attempt_failure(ip, body.email)      # drives the lockout
        if user:
            users.note_failed_login(user["id"])
            users.audit("login.failed", target=user["email"], ip=ip)
        else:
            users.audit("login.failed", target=body.email, detail="no such account", ip=ip)
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password.",
            headers={"X-Captcha-Required": "1"} if captcha.required_for_login(ip) else None,
        )

    _require_active(user, ip)

    methods = twofactor.methods_for(user)
    if methods:
        # Password was right, but it isn't a session yet.
        preferred = methods[0]
        twofactor.send_challenge(user, preferred)
        return {
            "authenticated": False,
            "mfa_required": True,
            "methods": methods,
            "sent": preferred if preferred in ("email", "sms") else None,
            "challenge": auth.issue_challenge(user["id"]),
        }

    users.clear_failed_logins(user["id"], ip)
    captcha.clear_failures(ip)
    captcha.clear_attempts(ip, body.email)
    auth.issue_session(response, user["id"], ip=ip, ua=ua)
    users.audit("login.success", actor=user, ip=ip)
    return {"authenticated": True, "user": _public(user)}


@router.post("/login-token")
async def login_token(body: LoginTokenRequest, request: Request, response: Response) -> dict:
    """Sign in with a per-account login token instead of email + password.

    The token identifies the account on its own, so there's no email to protect
    against enumeration here — but we still rate-limit by IP so the endpoint
    can't be used to grind at the token space, and we still honour the account's
    second factor if one is enrolled."""
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")

    if not settings.login_token_enabled:
        raise HTTPException(status_code=403, detail="Token sign-in is disabled.")

    # IP-level brute-force gate (tokens are high-entropy, so this is belt-and-
    # braces — but a locked door still beats an open one).
    if captcha.failure_count(ip) >= settings.login_max_attempts:
        users.audit("login_token.locked", ip=ip)
        raise HTTPException(
            status_code=423,
            detail="Too many failed attempts. Try again in a few minutes.",
        )

    user = tokens.verify(body.token)
    if not user:
        captcha.note_failure(ip)
        users.audit("login_token.failed", ip=ip)
        raise HTTPException(status_code=401, detail="That login token isn't valid.")

    _require_active(user, ip)

    methods = twofactor.methods_for(user)
    if methods:
        preferred = methods[0]
        twofactor.send_challenge(user, preferred)
        return {
            "authenticated": False,
            "mfa_required": True,
            "methods": methods,
            "sent": preferred if preferred in ("email", "sms") else None,
            "challenge": auth.issue_challenge(user["id"]),
        }

    captcha.clear_failures(ip)
    auth.issue_session(response, user["id"], ip=ip, ua=ua)
    users.audit("login_token.success", actor=user, ip=ip)
    return {"authenticated": True, "user": _public(user)}


@router.get("/signup-info")
async def signup_info(request: Request) -> dict:
    """Everything the signup/login pages need before rendering."""
    return {
        "signup_enabled": settings.signup_enabled,
        "captcha_site_key": captcha.site_key(),
        # Which widget to render: "hcaptcha" or "recaptcha" ("" when off).
        "captcha_provider": captcha.provider(),
        "captcha_on_signup": captcha.required_for_signup(),
        "captcha_on_login": captcha.required_for_login(client_ip(request)),
        "oauth_providers": settings.oauth_providers,
        "login_token_enabled": settings.login_token_enabled,
        # Without a relay there is nowhere to send a link, so the login page
        # hides "Forgot password?" rather than offering a dead end.
        "password_reset_enabled": mail.available(),
        # Tells the signup page what to promise: an activation email, an
        # operator's approval, or immediate access.
        "email_activation": activation.enabled(),
        "activation_ttl_hours": settings.signup_activation_ttl_hours,
    }


@router.post("/signup", status_code=201)
async def signup(body: SignupRequest, request: Request, response: Response) -> dict:
    if not settings.signup_enabled:
        raise HTTPException(status_code=403, detail="Registration is closed.")
    ip = client_ip(request)

    if not _signup_allowed(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many accounts created from this address. Try again later.",
        )

    if captcha.required_for_signup():
        ok, reason = captcha.verify(body.captcha, ip)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

    problem = moderation.check_username(body.username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    email = body.email.strip().lower()
    email_problem = _email_problem(email)
    if email_problem:
        raise HTTPException(status_code=400, detail=email_problem)

    pw_problem = users.password_problem(body.password)
    if pw_problem:
        raise HTTPException(status_code=400, detail=pw_problem)

    _note_signup(ip)

    self_activates = activation.enabled()
    status = settings.signup_default_status
    # One note for every outcome of this call. The wording must not depend on
    # whether the address was new, or signup becomes the account-enumeration
    # oracle that login refuses to be.
    note = (
        "If that address is new, an activation link is on its way. Click it "
        "and you're in — check your spam folder if it doesn't arrive."
        if self_activates
        else "Check your email — your account needs an operator's approval before "
             "you can sign in."
        if status != "active"
        else "If that address is new, the account is ready. Sign in to continue."
    )

    existing = users.get_by_email(email)
    if existing:
        users.audit("signup.duplicate", target=email, ip=ip)
        # Someone re-registering an address they never activated most likely
        # lost the first email. Sending another costs nothing and reveals
        # nothing — the response is the same one a new address gets.
        if self_activates and existing["status"] == "pending":
            activation.send(existing, body.next, ip=ip)
        return {"created": True, "note": note}

    if users.username_taken(body.username):
        raise HTTPException(status_code=400, detail="That username is taken.")

    try:
        users.create(
            email,
            body.password,
            username=body.username,
            role="user",
            tier=settings.signup_default_tier,
            status=settings.signup_default_status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    users.audit("signup.created", target=email, detail=f"username={body.username}", ip=ip)

    # All of these are fire-and-forget. A signup must not fail, or even slow
    # down, because a mail relay is having a bad day.
    if self_activates:
        activation.send(users.get_by_email(email), body.next, ip=ip)  # type: ignore[arg-type]
    else:
        subject, text = mail.signup_received(body.username, pending=status != "active")
        mail.send_soon(email, subject, text)
    # Operators are no longer alerted on every signup or email verification —
    # that noise drowned out the alerts that matter. A new *paid* plan is what
    # raises a flag now; see billing.store._alert_if_new_paid.

    return {"created": True, "note": note}


# ─────────────────────────── activation ───────────────────────────
# The self-serve counterpart to the console's "status: active". Both routes
# answer without a session: the person using them cannot sign in yet, which
# is the whole point.

@router.get("/activate-check")
async def activate_check(token: str = "") -> dict:
    """Is this link good? Lets the page explain a dead link before trying."""
    found = activation.peek(token)
    return {"valid": found is not None, "next": found["next"] if found else ""}


@router.post("/activate")
async def activate(body: ActivateRequest, request: Request) -> dict:
    ip = client_ip(request)
    result = activation.complete(body.token, ip=ip)
    if not result:
        captcha.note_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="That activation link has expired or isn't valid. "
                   "Request a new one and use it within the time shown.",
        )
    already = result["outcome"] == "already"
    return {
        "activated": True,
        "already": already,
        "next": result["next"],
        "note": (
            "This account was already active. Sign in to continue."
            if already
            else "Your account is active. Sign in to continue."
        ),
    }


@router.post("/activate/resend")
async def activate_resend(body: ResendActivationRequest, request: Request) -> dict:
    ip = client_ip(request)
    if not activation.enabled():
        raise HTTPException(
            status_code=503,
            detail="Activation by email isn't enabled on this server. "
                   "An operator has to approve your account.",
        )
    # Shares the reset limiter: both are unauthenticated endpoints that email a
    # stranger, and one budget for the pair is simpler to reason about.
    if not _reset_allowed(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests from this address. Try again later.",
        )
    if captcha.required_for_login(ip):
        ok, reason = captcha.verify(body.captcha, ip)
        if not ok:
            raise HTTPException(status_code=400, detail=reason,
                                headers={"X-Captcha-Required": "1"})

    email = (body.email or "").strip().lower()
    sent = activation.resend(email, ip=ip)
    users.audit(
        "account.activation_requested", target=email,
        detail="sent" if sent else "no pending account", ip=ip,
    )
    return {
        "sent": True,
        "note": "If that address has an account waiting to be activated, a new "
                "link is on its way. Check your spam folder if it doesn't arrive.",
    }


@router.post("/mfa/send")
async def mfa_send(body: MfaVerifyRequest) -> dict:
    """Re-send a code, or switch to a different delivery method."""
    uid = auth.read_challenge(body.challenge)
    if not uid:
        raise HTTPException(status_code=400, detail="Challenge expired. Sign in again.")
    user = users.get(uid)
    if not user:
        raise HTTPException(status_code=400, detail="Challenge expired. Sign in again.")
    # This endpoint's whole purpose is to answer "did it go?", so it waits for
    # the real result — off the event loop, so one slow relay doesn't freeze
    # every other request in the worker.
    ok = await run_in_threadpool(
        twofactor.send_challenge, user, body.method, False
    )
    return {"sent": ok, "method": body.method}


@router.post("/mfa/verify")
async def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response) -> dict:
    ip = client_ip(request)
    uid = auth.read_challenge(body.challenge)
    if not uid:
        raise HTTPException(status_code=400, detail="Challenge expired. Sign in again.")
    user = users.get(uid)
    if not user or user["status"] != "active":
        raise HTTPException(status_code=400, detail="Challenge expired. Sign in again.")
    if users.is_locked(user):
        raise HTTPException(status_code=423, detail="Too many failed attempts.")

    if not twofactor.verify(user, body.method, body.code):
        users.note_failed_login(user["id"])
        users.audit("mfa.failed", target=user["email"], detail=body.method, ip=ip)
        raise HTTPException(status_code=401, detail="Incorrect code.")

    users.clear_failed_logins(user["id"], ip)
    auth.issue_session(response, user["id"], ip=ip,
                       ua=request.headers.get("user-agent", ""))
    users.audit("login.success", actor=user, detail=f"mfa:{body.method}", ip=ip)
    return {"authenticated": True, "user": _public(users.get(uid))}  # type: ignore[arg-type]


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    auth.clear_session(request, response)
    return {"authenticated": False}


@router.get("/session")
async def session(request: Request) -> dict:
    user = auth.current_user(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": _public(user)}


@router.get("/bootstrap")
async def bootstrap() -> dict:
    """Does the system have any accounts yet? Drives the login page's first-run
    state — public because the login page needs it before authenticating."""
    return {
        "has_users": users.count() > 0,
        "email_2fa_available": twofactor.email_available(),
        "sms_2fa_available": twofactor.sms_available(),
        "password_reset_enabled": mail.available(),
    }


# ─────────────────────────── self-serve MFA ───────────────────────────

@router.get("/mfa/status")
async def mfa_status(user: dict = Depends(auth.require_session)) -> dict:
    return {
        "methods": twofactor.methods_for(user),
        "totp_enabled": bool(user.get("totp_enabled")),
        "email_enabled": bool(user.get("email_otp_enabled")),
        "sms_enabled": bool(user.get("sms_otp_enabled")),
        "email_available": twofactor.email_available(),
        "sms_available": twofactor.sms_available(),
        "phone": user.get("phone"),
        "recovery_codes_left": users.recovery_codes_left(user["id"]) if user.get("id") else 0,
    }


@router.post("/mfa/totp/begin")
async def totp_begin(user: dict = Depends(auth.require_session)) -> dict:
    if user.get("break_glass"):
        raise HTTPException(status_code=400, detail="Create a real account first.")
    return twofactor.totp_begin(user)


@router.post("/mfa/enable")
async def mfa_enable(
    body: MfaEnableRequest, request: Request,
    user: dict = Depends(auth.require_session),
) -> dict:
    if user.get("break_glass"):
        raise HTTPException(status_code=400, detail="Create a real account first.")
    ip = client_ip(request)

    if body.method == "totp":
        if not twofactor.totp_confirm(user, body.code):
            raise HTTPException(status_code=400, detail="That code didn't match.")
        codes = users.issue_recovery_codes(user["id"])
        users.audit("mfa.enabled", actor=user, detail="totp", ip=ip)
        return {"enabled": True, "recovery_codes": codes}

    if body.method == "email":
        if not twofactor.email_available():
            raise HTTPException(status_code=400, detail="Email delivery is not configured.")
        from .. import db
        db.execute("UPDATE users SET email_otp_enabled = 1 WHERE id = ?", (user["id"],))
        users.audit("mfa.enabled", actor=user, detail="email", ip=ip)
        return {"enabled": True}

    if body.method == "sms":
        if not twofactor.sms_available():
            raise HTTPException(status_code=400, detail="SMS delivery is not configured.")
        if not body.phone:
            raise HTTPException(status_code=400, detail="A phone number is required.")
        from .. import db
        db.execute(
            "UPDATE users SET sms_otp_enabled = 1, phone = ? WHERE id = ?",
            (body.phone, user["id"]),
        )
        users.audit("mfa.enabled", actor=user, detail="sms", ip=ip)
        return {"enabled": True}

    raise HTTPException(status_code=400, detail="Unknown method.")


@router.post("/mfa/disable")
async def mfa_disable(
    body: MfaEnableRequest, request: Request,
    user: dict = Depends(auth.require_session),
) -> dict:
    from .. import db

    col = {"totp": "totp_enabled", "email": "email_otp_enabled", "sms": "sms_otp_enabled"}
    if body.method not in col:
        raise HTTPException(status_code=400, detail="Unknown method.")
    db.execute(f"UPDATE users SET {col[body.method]} = 0 WHERE id = ?", (user["id"],))
    users.audit("mfa.disabled", actor=user, detail=body.method, ip=client_ip(request))
    return {"disabled": True}


@router.post("/mfa/recovery-codes")
async def regenerate_recovery(user: dict = Depends(auth.require_session)) -> dict:
    return {"recovery_codes": users.issue_recovery_codes(user["id"])}


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest, request: Request,
    user: dict = Depends(auth.require_session),
) -> dict:
    if user.get("break_glass"):
        raise HTTPException(status_code=400, detail="Create a real account first.")
    ip = client_ip(request)
    _require_current_password(user, body.current_password, ip)
    try:
        users.set_password(user["id"], body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Changing a password invalidates every *other* session. The one making the
    # request just proved it knows the old password, and signing it out too
    # would bounce someone to the login page the instant they press Save.
    users.revoke_other_sessions(user["id"], user["sid"])
    users.audit("password.changed", actor=user, ip=ip)
    # Out-of-band, like the reset flow: if this wasn't the owner, this is the
    # message that says so.
    subject, text = mail.password_changed_self(ip)
    mail.send_soon(user["email"], subject, text)
    return {"changed": True, "note": "Your other sessions were signed out."}


# ─────────────────────────── change email ───────────────────────────
#
# The address changes only when a link mailed to the NEW one is opened — see
# services/email_change.py. Like /password/forgot, the request must not become
# an account-enumeration oracle: asking for an address that already belongs to
# someone else gets the same answer as asking for a free one.


@router.post("/email/change")
async def email_change_request(
    body: EmailChangeRequest, request: Request,
    user: dict = Depends(auth.require_session),
) -> dict:
    if user.get("break_glass"):
        raise HTTPException(status_code=400, detail="Create a real account first.")
    # A property of the server, not of any account, so saying so leaks nothing.
    if not mail.available():
        raise HTTPException(
            status_code=503,
            detail="Changing your email needs email delivery, which isn't "
                   "configured on this server. Ask an operator to change it for you.",
        )
    ip = client_ip(request)
    _require_current_password(user, body.current_password, ip)

    new = body.new_email.strip().lower()
    problem = _email_problem(new)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if new == user["email"]:
        raise HTTPException(status_code=400, detail="That is already your email address.")

    # Shares the reset limiter: this too sends mail to an address the caller
    # chose, and one budget for all of them is simpler to reason about.
    if not _reset_allowed(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests from this address. Try again later.",
        )

    email_change.request(user, new, ip=ip)
    return {
        "sent": True,
        "note": "If that address can be used, a confirmation link is on its way "
                "to it. Your email changes only once you open the link.",
    }


@router.post("/email/confirm")
async def email_change_confirm(body: EmailConfirmRequest, request: Request) -> dict:
    """Opened from the email, so — like /activate — it answers without a
    session: the link is mailed to a different address than the one the account
    currently has, and may well be opened in a different browser."""
    ip = client_ip(request)
    result = email_change.complete(body.token, ip=ip)
    if not result:
        captcha.note_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="That confirmation link has expired or isn't valid. "
                   "Request a new one from your account page.",
        )
    return {
        "changed": True,
        "email": result["new"],
        "note": "Your email address has been updated.",
    }


# ─────────────────────────── delete account ───────────────────────────
#
# Self-service, because app stores require it and because asking an operator
# to delete you is not a choice anyone should have to make by email. The card
# subscription is cancelled first, and if Stripe cannot be reached nothing is
# deleted: an account that is gone while its card keeps being charged is the
# one outcome that cannot be fixed afterwards.


@router.post("/account/delete")
async def delete_account(
    body: AccountDeleteRequest, request: Request, response: Response,
    user: dict = Depends(auth.require_session),
) -> dict:
    if user.get("break_glass"):
        raise HTTPException(status_code=400, detail="Create a real account first.")
    if body.confirm.strip() != "DELETE":
        raise HTTPException(status_code=400, detail='Type DELETE to confirm.')
    ip = client_ip(request)
    _require_current_password(user, body.current_password, ip)
    if user["role"] == "admin" and users.stats()["by_role"].get("admin", 0) <= 1:
        raise HTTPException(
            status_code=400,
            detail="This is the last administrator account. Make someone else "
                   "an administrator before deleting it.",
        )

    from ..services.billing import stripe_provider as cards

    try:
        cancelled = await run_in_threadpool(cards.cancel_now, user["id"])
    except Exception as e:
        log_detail = f"{type(e).__name__}: {e}"[:300]
        users.audit("account.delete_failed", actor=user, detail=log_detail, ip=ip)
        raise HTTPException(
            status_code=502,
            detail="Your card subscription could not be cancelled, so the account "
                   "was not deleted. Try again in a few minutes, or contact support.",
        ) from e

    email = user["email"]
    users.delete(user["id"])
    response.delete_cookie(settings.session_cookie, path="/")
    users.audit("account.self_deleted", target=email,
                detail=f"subscription {cancelled} cancelled" if cancelled else "", ip=ip)
    subject, text = mail.account_deleted(ip)
    mail.send_soon(email, subject, text)
    return {"deleted": True}


# ─────────────────────────── forgotten password ───────────────────────────
#
# Before this existed the only route back into a locked account was an operator
# running `accounts.py set-password`. That is fine for one person and useless
# for customers.
#
# The hard requirement here is that /password/forgot must not become the
# account-enumeration oracle that /login and /signup both go out of their way
# not to be: one response body, one status code, identical timing-insensitive
# work, whether or not the address is real.


@router.post("/password/forgot")
async def password_forgot(body: ForgotPasswordRequest, request: Request) -> dict:
    ip = client_ip(request)

    # Not a leak: whether a relay is configured is a property of the server,
    # not of any account. Saying so beats claiming to have sent an email.
    if not mail.available():
        raise HTTPException(
            status_code=503,
            detail="Password reset by email isn't configured on this server. "
                   "Ask an operator to reset it for you.",
        )

    if not _reset_allowed(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many reset requests from this address. Try again later.",
        )

    if captcha.required_for_login(ip):
        ok, reason = captcha.verify(body.captcha, ip)
        if not ok:
            raise HTTPException(status_code=400, detail=reason,
                                headers={"X-Captcha-Required": "1"})

    email = (body.email or "").strip().lower()
    user = users.get_by_email(email)

    # A suspended or pending account deliberately gets no link — a reset must
    # not be a way around an operator's decision to switch someone off.
    if user and user["status"] == "active":
        raw = reset.create(user["id"], ip)
        subject, text = mail.password_reset(
            reset.link(raw), settings.password_reset_ttl_minutes, ip
        )
        mail.send_soon(user["email"], subject, text)
        users.audit("password.reset_requested", target=user["email"], ip=ip)
    else:
        detail = f"no active account ({user['status']})" if user else "no such account"
        users.audit("password.reset_requested", target=email, detail=detail, ip=ip)

    return {
        "sent": True,
        "note": "If that address has an active account, a reset link is on its way. "
                "Check your spam folder if it doesn't arrive.",
    }


@router.get("/password/reset-check")
async def password_reset_check(token: str = "") -> dict:
    """Is this link still good? Lets the reset page say so up front instead of
    after someone has typed a new password twice."""
    return {"valid": reset.peek(token) is not None}


@router.post("/password/reset")
async def password_reset(body: ResetPasswordRequest, request: Request) -> dict:
    ip = client_ip(request)
    try:
        user = reset.complete(body.token, body.password, ip=ip)
    except ValueError as e:
        # Weak password. The link is intact — reset.complete only burns it on
        # success — so the visitor gets to try again.
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not user:
        captcha.note_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="That reset link has expired or has already been used. "
                   "Request a new one.",
        )

    # Tell the account holder out-of-band. If the reset wasn't theirs, this is
    # the message that tells them something is wrong.
    subject, text = mail.password_changed(ip)
    mail.send_soon(user["email"], subject, text)
    return {
        "reset": True,
        "note": "Password changed and every session signed out. Sign in with the new one.",
    }

"""Social-login endpoints: /auth/oauth/{provider}/{start,callback}.

These are hit by the browser directly (a full-page navigation, not fetch), so
they speak in redirects rather than JSON. The heavy lifting — token exchange,
verified-email extraction, account resolution — lives in services/oauth.py;
this module is the HTTP shell plus the security decisions about what to do once
an identity is known (pending accounts, MFA, session issuance).
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from .. import auth
from ..config import settings
from ..services import oauth, twofactor, users
from ..services.analytics import client_ip

router = APIRouter(prefix="/auth/oauth", tags=["auth"])


def _safe_next(raw: str | None) -> str:
    """Only same-origin relative paths — never an absolute URL (open redirect)."""
    if raw and raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return "/console"


def _frontend(path: str, *, query: dict | None = None, fragment: str = "") -> str:
    base = settings.public_base_url.rstrip("/")
    url = f"{base}{path}"
    if query:
        url += "?" + urlencode(query)
    if fragment:
        url += "#" + fragment
    return url


def _require_enabled(provider: str) -> None:
    if provider not in oauth.PROVIDERS or not oauth.is_enabled(provider):
        raise HTTPException(status_code=404, detail="This login method is not enabled.")


@router.get("/{provider}/start")
async def start(provider: str, next: str = Query("/console")) -> Response:
    _require_enabled(provider)

    nonce = secrets.token_urlsafe(16)
    state = oauth.sign_state(provider, _safe_next(next), nonce)

    resp = RedirectResponse(oauth.authorize_url(provider, state), status_code=302)
    # Mirror the state in a cookie so the callback can prove this browser is the
    # one that began the flow. Lax so it survives the top-level redirect back.
    resp.set_cookie(
        key=oauth.STATE_COOKIE,
        value=state,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/api/v1/auth/oauth",
    )
    return resp


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
) -> Response:
    _require_enabled(provider)
    ip = client_ip(request)

    def fail(msg: str) -> RedirectResponse:
        r = RedirectResponse(_frontend("/login", query={"oauth_error": msg}), status_code=303)
        r.delete_cookie(oauth.STATE_COOKIE, path="/api/v1/auth/oauth")
        return r

    # The visitor declined consent, or the provider errored out.
    if error or not code:
        return fail("Social login was cancelled.")

    # CSRF: the state in the URL must match the one we set as a cookie, and the
    # signed payload must still be valid and name this provider.
    cookie_state = request.cookies.get(oauth.STATE_COOKIE, "")
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        return fail("Login session expired. Please try again.")
    payload = oauth.read_state(state)
    if not payload or payload.get("p") != provider:
        return fail("Login session expired. Please try again.")

    try:
        email, suggested = await oauth.fetch_identity(provider, code)
        user, created = oauth.resolve_account(email)
    except oauth.OAuthError as e:
        users.audit("oauth.failed", detail=f"{provider}: {e}", ip=ip)
        return fail(str(e))
    except Exception:  # network, provider outage, malformed response
        users.audit("oauth.error", detail=provider, ip=ip)
        return fail("Could not complete social login. Please try again.")

    # A brand-new account that landed in the manual-approval queue: don't sign
    # them in, tell them why.
    if created and user.get("status") == "pending":
        users.audit("oauth.signup_pending", target=email, detail=provider, ip=ip)
        return RedirectResponse(
            _frontend("/login", query={"oauth_notice": "Account created — awaiting approval."}),
            status_code=303,
        )

    next_path = _safe_next(payload.get("n"))

    # OAuth authenticated the email, but it is NOT a second factor. If this
    # account enrolled MFA, hand off to the normal challenge instead of issuing
    # a session — otherwise social login would be an MFA bypass.
    methods = twofactor.methods_for(user)
    if methods:
        challenge = auth.issue_challenge(user["id"])
        preferred = methods[0]
        if preferred in ("email", "sms"):
            try:
                twofactor.send_challenge(user, preferred)
            except Exception:
                pass
        users.audit("oauth.mfa_required", actor=user, detail=provider, ip=ip)
        # Challenge rides in the fragment so it never lands in a server log or
        # the analytics query-string store.
        frag = urlencode({"mfa": challenge, "methods": ",".join(methods), "next": next_path})
        r = RedirectResponse(_frontend("/login", fragment=frag), status_code=303)
        r.delete_cookie(oauth.STATE_COOKIE, path="/api/v1/auth/oauth")
        return r

    resp = RedirectResponse(_frontend(next_path), status_code=303)
    resp.delete_cookie(oauth.STATE_COOKIE, path="/api/v1/auth/oauth")
    auth.issue_session(resp, user["id"], ip=ip, ua=request.headers.get("user-agent", ""))
    users.audit(
        "oauth.signup" if created else "oauth.login",
        actor=user, detail=provider, ip=ip,
    )
    return resp

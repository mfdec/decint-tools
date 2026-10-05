"""Social login (GitHub + Google OAuth 2.0).

Flow, per provider:

    /auth/oauth/{p}/start     -> sign a `state`, set it as a cookie, 302 to the
                                 provider's consent screen
    /auth/oauth/{p}/callback  -> verify state against the cookie, exchange the
                                 code for an access token, read the profile,
                                 resolve it to a local account, issue a session

Security posture:

* We only ever trust a **verified** email from the provider. GitHub can return
  several addresses, so we pick the primary verified one; Google must report
  `email_verified`. An unverified email is rejected — otherwise someone could
  register a provider account with your email and inherit your DECINT account.
* `state` is high-entropy, signed, short-lived, AND mirrored in an httpOnly
  cookie that must match on return. That binds the round-trip to this browser,
  which is what actually stops OAuth login CSRF.
* OAuth proves the email; it is NOT a second factor. If the resolved account
  has MFA enrolled, the router still forces the normal challenge.
* OAuth proves the mailbox, not an operator's approval. A `pending` account is
  only let in when accounts activate by emailed link (which the provider has
  just satisfied); under manual approval it stays out, as on the password path.
* An account that existed with an *unverified* email gets its password,
  sessions and login tokens revoked when it is first claimed through a
  provider, so a pre-registered squatter cannot keep access (see
  `_claim_unverified`).

Config lives in settings (`*_oauth_client_id/secret`, `public_base_url`).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import settings
from . import activation, moderation, tokens, users

_state = URLSafeTimedSerializer(settings.session_secret, salt="decint-oauth")

# The cookie that mirrors `state`. Scoped to the callback path so it isn't sent
# with ordinary requests.
STATE_COOKIE = "decint_oauth"


class OAuthError(Exception):
    """Why the social login could not complete.

    `str(e)` is for the audit log. `code` is what the visitor's browser is sent
    back with: the login page maps it to its own wording, so a crafted
    `/login?oauth_error=...` link cannot put arbitrary text on our login page.
    """

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Provider:
    key: str
    authorize_url: str
    token_url: str
    scope: str


PROVIDERS: dict[str, Provider] = {
    "github": Provider(
        key="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
    ),
    "google": Provider(
        key="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
    ),
}


def _creds(provider: str) -> tuple[str, str]:
    cid = getattr(settings, f"{provider}_oauth_client_id", "")
    secret = getattr(settings, f"{provider}_oauth_client_secret", "")
    return cid, secret


def is_enabled(provider: str) -> bool:
    return provider in settings.oauth_providers


def redirect_uri(provider: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/auth/oauth/{provider}/callback"


# ─────────────────────────── state ───────────────────────────

def sign_state(provider: str, next_path: str, nonce: str) -> str:
    return _state.dumps({"p": provider, "n": next_path, "x": nonce})


def read_state(token: str) -> dict | None:
    try:
        return _state.loads(token, max_age=settings.oauth_state_ttl_seconds)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


# ─────────────────────────── authorize URL ───────────────────────────

def authorize_url(provider: str, state: str) -> str:
    p = PROVIDERS[provider]
    cid, _ = _creds(provider)
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri(provider),
        "scope": p.scope,
        "state": state,
        "response_type": "code",
    }
    if provider == "google":
        # Ask for a fresh consent-less token but still force account choice so a
        # shared machine doesn't silently reuse the last Google session.
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    if provider == "github":
        params["allow_signup"] = "true"
    return str(httpx.URL(p.authorize_url, params=params))


# ─────────────────────────── token + profile ───────────────────────────

async def _exchange_code(provider: str, code: str) -> str:
    p = PROVIDERS[provider]
    cid, secret = _creds(provider)
    data = {
        "client_id": cid,
        "client_secret": secret,
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(p.token_url, data=data, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise OAuthError("The provider rejected the login. Please try again.", "failed")
    tok = r.json().get("access_token")
    if not tok:
        raise OAuthError("The provider did not return an access token.", "failed")
    return tok


async def _github_identity(token: str) -> tuple[str, str | None]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "decint-tools",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        prof = await client.get("https://api.github.com/user")
        emails = await client.get("https://api.github.com/user/emails")
    if prof.status_code != 200:
        raise OAuthError("Could not read your GitHub profile.", "failed")
    login = prof.json().get("login")

    verified = None
    if emails.status_code == 200 and isinstance(emails.json(), list):
        for e in emails.json():
            if e.get("primary") and e.get("verified") and e.get("email"):
                verified = e["email"]
                break
        if not verified:  # no primary-verified, take any verified
            for e in emails.json():
                if e.get("verified") and e.get("email"):
                    verified = e["email"]
                    break
    if not verified:
        raise OAuthError(
            "Your GitHub account has no verified email. Verify one on GitHub, "
            "then try again.",
            "unverified",
        )
    return verified, login


async def _google_identity(token: str) -> tuple[str, str | None]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        raise OAuthError("Could not read your Google profile.", "failed")
    info = r.json()
    email = info.get("email")
    # Google returns this as a real bool or the string "true" depending on path.
    verified = info.get("email_verified") in (True, "true")
    if not email or not verified:
        raise OAuthError("Your Google email is not verified.", "unverified")
    return email, info.get("given_name") or info.get("name")


async def fetch_identity(provider: str, code: str) -> tuple[str, str | None]:
    """Return (verified_email, suggested_username) for the consenting user."""
    token = await _exchange_code(provider, code)
    if provider == "github":
        return await _github_identity(token)
    return await _google_identity(token)


# ─────────────────────────── account resolution ───────────────────────────

def _claim_unverified(user: dict) -> None:
    """The provider has just proven this mailbox is the visitor's. Anything
    set up on the account *before* that proof is not theirs to trust.

    Without this, signing up with someone else's address and a password of your
    own, then waiting for them to use "Sign in with Google", leaves you holding
    a working password to their account. So the password is replaced with a
    throwaway nobody knows, and every session and login token that already
    exists is revoked. The owner sets a new password through "Forgot your
    password?" if they ever want one.

    Operator and admin accounts are skipped: self-signup only ever creates
    role "user", so the attack above cannot have produced one, and silently
    rotating an administrator's password would only lock them out.
    """
    if user.get("role") == "user":
        users.set_password(user["id"], secrets.token_urlsafe(32))
        users.revoke_all_sessions(user["id"])
        tokens.revoke_all(user["id"])
    users.update(user["id"], email_verified=1)
    users.audit("oauth.claimed_unverified", target=user["email"], detail=f"role={user.get('role')}")


def resolve_account(email: str) -> tuple[dict, bool]:
    """Find the local account for a provider-verified email, creating one if
    registration is open. Returns (user, created).

    Raises OAuthError for the cases the caller must surface to the visitor:
    a suspended account, or a new email when signup is closed.

    A returned account can still be `pending` (operator approval is on). The
    provider proving the mailbox only replaces the *emailed-link* check, never
    an operator's approval, so the caller must not sign a pending account in.
    """
    email = email.strip().lower()
    existing = users.get_by_email(email)
    if existing:
        if existing.get("status") == "suspended":
            raise OAuthError("This account is suspended.", "suspended")
        if not existing.get("email_verified"):
            _claim_unverified(existing)
        # Proof of the mailbox is exactly what the activation email asks for,
        # so when that is how accounts go live here, Google has just done it.
        if existing.get("status") == "pending" and activation.enabled():
            users.update(existing["id"], status="active")
            users.audit("account.activated", target=email, detail="via social login")
        return users.get(existing["id"]), False  # type: ignore[return-value]

    if not settings.signup_enabled:
        raise OAuthError("Registration is closed, and no account uses this email.", "closed")
    # Same rule the signup form applies to the address (it is displayed as the
    # account's name when there is no username, which social accounts lack).
    if moderation.is_profane(email.split("@")[0]):
        raise OAuthError("Email address not accepted.", "email")

    status = settings.signup_default_status
    if status == "pending" and activation.enabled():
        status = "active"  # see above: the provider already verified the mailbox
    user = users.create_oauth(email, tier=settings.signup_default_tier, status=status)
    return user, True

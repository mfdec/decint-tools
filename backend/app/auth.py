"""Authentication: database-backed sessions, roles, and the MFA challenge.

The cookie carries only an opaque session id. Everything about the caller —
who they are, their role, their tier, whether they're suspended — is read from
the database on each request, so revoking a session or suspending an account
takes effect immediately rather than at cookie expiry.

Break-glass: while the users table is empty, OPERATOR_TOKEN still works so a
fresh deployment is never locked out of itself. The moment the first account
exists, that path is closed.
"""

from __future__ import annotations

import time

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings
from .services import users
from .services.billing import store as billing_store

_sessions = URLSafeTimedSerializer(settings.session_secret, salt="decint-session")
_challenge = URLSafeTimedSerializer(settings.session_secret, salt="decint-mfa")


# ─────────────────────────── cookies ───────────────────────────

def issue_session(response: Response, user_id: int, ip: str = "", ua: str = "") -> str:
    sid = users.create_session(user_id, ip=ip, ua=ua)
    response.set_cookie(
        key=settings.session_cookie,
        value=sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return sid


def clear_session(request: Request, response: Response) -> None:
    sid = request.cookies.get(settings.session_cookie)
    if sid:
        users.revoke_session(sid)
    response.delete_cookie(settings.session_cookie, path="/")


# ─────────────────────────── MFA challenge ───────────────────────────

def issue_challenge(user_id: int) -> str:
    """Short-lived token proving the password step passed. Not a session."""
    return _challenge.dumps({"uid": user_id, "iat": int(time.time())})


def read_challenge(token: str) -> int | None:
    try:
        data = _challenge.loads(token, max_age=settings.mfa_challenge_ttl_seconds)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


# ─────────────────────────── current user ───────────────────────────

def _break_glass(request: Request) -> dict | None:
    """Only while no accounts exist at all."""
    if users.count() > 0 or not settings.operator_token:
        return None
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        return None
    try:
        _sessions.loads(raw, max_age=settings.session_ttl_seconds)
    except (BadSignature, SignatureExpired):
        return None
    return {
        "id": 0, "email": "operator@localhost", "role": "admin",
        "tier": "enterprise", "status": "active", "break_glass": True,
    }


def issue_break_glass(response: Response) -> None:
    token = _sessions.dumps({"op": "bootstrap", "iat": int(time.time())})
    response.set_cookie(
        key=settings.session_cookie, value=token,
        max_age=settings.session_ttl_seconds, httponly=True,
        samesite="lax", secure=settings.cookie_secure, path="/",
    )


def current_user(request: Request) -> dict | None:
    sid = request.cookies.get(settings.session_cookie)
    if sid:
        u = users.session_user(sid)
        if u:
            # Lapsed plans expire here rather than on a cron: the sweep is
            # throttled to one pass a minute across all traffic, so the cost is
            # negligible and there is no scheduler to forget to install. The
            # user row was already read above, so a downgrade lands on the very
            # next request rather than this one — which is the right side to
            # err on for a customer whose renewal is a minute late.
            billing_store.sweep_throttled()
            return u
    return _break_glass(request)


def get_current_user_optional(request: Request) -> dict | None:
    """Get current user if authenticated, otherwise return None."""
    return current_user(request)


def is_authenticated(request: Request) -> bool:
    return current_user(request) is not None


# ─────────────────────────── dependencies ───────────────────────────

def require_session(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    return u


def require_admin(request: Request, user: dict = Depends(require_session)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required."
        )
    return user


def require_operator(request: Request, user: dict = Depends(require_session)) -> dict:
    """admin or operator — for tools that aren't user-facing features."""
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operator access required."
        )
    return user

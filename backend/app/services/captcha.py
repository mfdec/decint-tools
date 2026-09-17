"""hCaptcha verification and risk-based challenge decisions.

hCaptcha is the provider Discord uses, so the visitor-facing experience is the
one people already recognise.

Two rules, matching how Discord behaves:

* **Signup always requires a solved captcha.** Registration is the expensive
  endpoint to abuse.
* **Login requires one adaptively** — only once the *IP* has accumulated failed
  attempts. Deliberately keyed on IP rather than on the account's own
  failed-login counter: an account-keyed rule would let an attacker probe
  whether an address is registered simply by watching for the captcha to
  appear.

Fail-closed: if `HCAPTCHA_SECRET` is unset the feature is *off* and no captcha
is demanded (so local development works), but if it is set and verification
cannot be completed, the request is rejected rather than waved through.
"""

from __future__ import annotations

import threading
import time

import httpx

from ..config import settings

VERIFY_URL = "https://api.hcaptcha.com/siteverify"

# In-process record of recent failed auth attempts per IP. Not persisted: a
# restart forgiving the counter is an acceptable trade for not writing a row on
# every failed password.
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def configured() -> bool:
    return bool(settings.hcaptcha_secret and settings.hcaptcha_site_key)


def site_key() -> str:
    return settings.hcaptcha_site_key if configured() else ""


# ─────────────────────────── risk ───────────────────────────

def note_failure(ip: str) -> None:
    if not ip:
        return
    now = time.time()
    with _lock:
        window = _failures.setdefault(ip, [])
        window.append(now)
        cutoff = now - settings.captcha_window_seconds
        _failures[ip] = [t for t in window if t > cutoff]


def clear_failures(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)


# Failures keyed on (ip, email). This is what drives LOCKOUT, deliberately
# separate from the IP-wide counter above which only drives the captcha.
#
# Locking the whole IP would be a denial-of-service gift: anyone could lock a
# shared office or NAT address out of the product by failing a few logins.
# Locking (ip, email) stops brute force against a specific account and behaves
# identically whether or not that account exists, which is what keeps lockout
# from becoming an account-enumeration oracle.
_attempts: dict[tuple[str, str], list[float]] = {}


def note_attempt_failure(ip: str, email: str) -> None:
    if not ip:
        return
    key = (ip, (email or "").strip().lower())
    now = time.time()
    with _lock:
        window = _attempts.setdefault(key, [])
        window.append(now)
        _attempts[key] = [t for t in window if t > now - settings.captcha_window_seconds]


def attempt_count(ip: str, email: str) -> int:
    key = (ip, (email or "").strip().lower())
    now = time.time()
    with _lock:
        window = [t for t in _attempts.get(key, []) if t > now - settings.captcha_window_seconds]
        _attempts[key] = window
        return len(window)


def clear_attempts(ip: str, email: str) -> None:
    with _lock:
        _attempts.pop((ip, (email or "").strip().lower()), None)


def reset_all() -> None:
    """Test helper — drop every counter."""
    with _lock:
        _failures.clear()
        _attempts.clear()


def failure_count(ip: str) -> int:
    now = time.time()
    with _lock:
        window = _failures.get(ip, [])
        cutoff = now - settings.captcha_window_seconds
        window = [t for t in window if t > cutoff]
        _failures[ip] = window
        return len(window)


def required_for_login(ip: str) -> bool:
    """Has this IP failed enough recently that we should make it prove it's human?"""
    if not configured():
        return False
    if settings.captcha_always_on_login:
        return True
    return failure_count(ip) >= settings.captcha_login_threshold


def required_for_signup() -> bool:
    return configured()


# ─────────────────────────── verification ───────────────────────────

def verify(token: str, remote_ip: str = "") -> tuple[bool, str]:
    """Check a solved captcha with hCaptcha. Returns (ok, reason)."""
    if not configured():
        # Feature disabled entirely — nothing to check.
        return True, "captcha not configured"
    if not token:
        return False, "Captcha required."
    data = {"secret": settings.hcaptcha_secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    if settings.hcaptcha_site_key:
        data["sitekey"] = settings.hcaptcha_site_key
    try:
        r = httpx.post(VERIFY_URL, data=data, timeout=10.0)
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError):
        # Configured but unreachable: refuse rather than let bots through.
        return False, "Could not verify the captcha. Please try again."
    if body.get("success"):
        return True, "ok"
    codes = ", ".join(body.get("error-codes", []) or [])
    return False, f"Captcha failed{f' ({codes})' if codes else ''}."

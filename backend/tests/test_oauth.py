"""Social login: the Google round-trip, and what happens to the account after it.

The provider's own endpoints are never called — `fetch_identity` is replaced
with a stub that returns whichever verified email the test wants. What is under
test is everything on our side of that line: the CSRF binding, the redirects the
login page depends on, and the account decisions (pending, suspended, MFA, and a
squatter who registered the address first).
"""

import os
import tempfile
from urllib.parse import parse_qs, urlparse

import pytest

_TMP = tempfile.mkdtemp()
os.environ["ANALYTICS_DB"] = os.path.join(_TMP, "oauth.db")
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "pending"
os.environ["SIGNUP_EMAIL_ACTIVATION"] = "true"
os.environ["SIGNUP_MAX_PER_IP_PER_HOUR"] = "1000"
os.environ["SMTP_HOST"] = "smtp.example.test"       # a relay "exists", so activation is on
os.environ["SMTP_FROM"] = "noreply@example.test"
os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-client-secret"
os.environ["GITHUB_OAUTH_CLIENT_ID"] = ""
os.environ["GITHUB_OAUTH_CLIENT_SECRET"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.routers import oauth as oauth_router  # noqa: E402
from app.services import activation, mail, oauth, tokens, users  # noqa: E402

for mod in (activation, mail, oauth, oauth_router, users, tokens):
    mod.settings = cfg.settings

PASSWORD = "a-perfectly-adequate-passphrase"
SESSION = cfg.settings.session_cookie
_seq = [0]


@pytest.fixture(autouse=True)
def _never_the_live_db():
    # Guard the whole module: a path outside our temp dir means the env above
    # lost the race with another import, and these tests write accounts.
    assert str(db._db_path()).startswith(_TMP), db._db_path()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    db.get_conn()
    return TestClient(app)


@pytest.fixture()
def provider(monkeypatch):
    """What the stubbed provider will report for the next callback."""
    box: dict = {"email": None, "error": None}

    async def fake(_provider, _code):
        if box["error"]:
            raise box["error"]
        return box["email"], "Given"

    monkeypatch.setattr(oauth, "fetch_identity", fake)
    return box


def _fresh() -> str:
    _seq[0] += 1
    return f"person{_seq[0]}@example.test"


def _make(email, *, status="active", verified=1, role="user"):
    u = users.create(email, PASSWORD, status=status, role=role)
    db.execute("UPDATE users SET email_verified = ? WHERE id = ?", (verified, u["id"]))
    return users.get(u["id"])


def _go(client, *, next_path="/console", **cb):
    """Run /start then /callback the way a browser would, without following
    the final redirect. `cb` overrides the callback's query."""
    r = client.get("/api/v1/auth/oauth/google/start", params={"next": next_path},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    params = {"code": "abc", "state": state, **cb}
    return client.get("/api/v1/auth/oauth/google/callback", params=params,
                      follow_redirects=False)


def _where(resp):
    u = urlparse(resp.headers["location"])
    return u.path, parse_qs(u.query), u.fragment


def _signed_in(client) -> bool:
    return SESSION in client.cookies


# ─────────────────────────── start ───────────────────────────

def test_start_sends_the_visitor_to_google_and_binds_the_browser(client):
    r = client.get("/api/v1/auth/oauth/google/start", follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "accounts.google.com"
    assert q["client_id"] == ["test-client-id"]
    assert q["redirect_uri"] == ["https://example.test/api/v1/auth/oauth/google/callback"]
    assert q["scope"] == ["openid email profile"]
    # The state is also a cookie scoped to the callback path: that pairing is
    # what stops someone else's authorisation code being replayed into this
    # browser.
    assert client.cookies.get(oauth.STATE_COOKIE) == q["state"][0]


def test_unconfigured_provider_is_a_404(client):
    # GitHub keys are empty above, so its routes must not exist.
    assert client.get("/api/v1/auth/oauth/github/start", follow_redirects=False).status_code == 404
    assert client.get("/api/v1/auth/oauth/nonsense/start", follow_redirects=False).status_code == 404


def test_signup_info_advertises_only_configured_providers(client):
    assert client.get("/api/v1/auth/signup-info").json()["oauth_providers"] == ["google"]


# ─────────────────────────── callback: CSRF and errors ───────────────────────────

def test_callback_without_the_state_cookie_is_refused(client, provider):
    provider["email"] = _fresh()
    # A callback from a browser that never ran /start has no state cookie.
    r = client.get("/api/v1/auth/oauth/google/callback",
                   params={"code": "abc", "state": "whatever"}, follow_redirects=False)
    assert _where(r)[:2] == ("/login", {"oauth_error": ["expired"]})
    assert not _signed_in(client)


def test_callback_with_a_mismatched_state_is_refused(client, provider):
    provider["email"] = _fresh()
    client.get("/api/v1/auth/oauth/google/start", follow_redirects=False)
    r = client.get("/api/v1/auth/oauth/google/callback",
                   params={"code": "abc", "state": "forged"}, follow_redirects=False)
    assert _where(r)[1] == {"oauth_error": ["expired"]}
    assert not _signed_in(client)


def test_declining_consent_comes_back_as_a_code_not_prose(client, provider):
    r = _go(client, code="", error="access_denied")
    assert _where(r)[1] == {"oauth_error": ["cancelled"]}


def test_unverified_provider_email_creates_nothing(client, provider):
    email = _fresh()
    provider["error"] = oauth.OAuthError("Your Google email is not verified.", "unverified")
    r = _go(client)
    assert _where(r)[1] == {"oauth_error": ["unverified"]}
    assert users.get_by_email(email) is None
    assert not _signed_in(client)


def test_provider_outage_is_a_generic_failure(client, provider):
    provider["error"] = RuntimeError("boom")
    assert _where(_go(client))[1] == {"oauth_error": ["failed"]}


# ─────────────────────────── callback: accounts ───────────────────────────

def test_new_account_is_active_and_signed_in_when_activation_is_by_email(client, provider):
    email = _fresh()
    provider["email"] = email.upper()  # providers don't promise the casing we store
    r = _go(client, next_path="/billing")

    assert r.status_code == 303 and _where(r)[0] == "/billing"
    assert _signed_in(client)
    u = users.get_by_email(email)
    # Google proved the mailbox, which is the one thing the activation link
    # checks — so the account must not be left waiting for a link nobody sent.
    assert u["status"] == "active" and u["email_verified"] == 1 and u["role"] == "user"


def test_second_login_reuses_the_account(client, provider):
    email = _fresh()
    provider["email"] = email
    _go(client)
    before = users.count()
    client.cookies.clear()
    _go(client)
    assert users.count() == before and _signed_in(client)


def test_existing_verified_account_keeps_its_password(client, provider):
    email = _fresh()
    _make(email)
    provider["email"] = email
    _go(client)
    assert _signed_in(client)
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["authenticated"]


def test_open_redirect_in_next_is_neutralised(client, provider):
    provider["email"] = _fresh()
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        client.cookies.clear()
        assert _where(_go(client, next_path=evil))[0] == "/console"


def test_closed_registration_turns_new_emails_away(client, provider, monkeypatch):
    monkeypatch.setattr(cfg.settings, "signup_enabled", False)
    email = _fresh()
    provider["email"] = email
    r = _go(client)
    assert _where(r)[1] == {"oauth_error": ["closed"]}
    assert users.get_by_email(email) is None


def test_suspended_account_cannot_sign_in(client, provider):
    email = _fresh()
    _make(email, status="suspended")
    provider["email"] = email
    r = _go(client)
    assert _where(r)[1] == {"oauth_error": ["suspended"]}
    assert not _signed_in(client)


# ─────────────────────────── pre-registered (squatted) addresses ───────────────────────────

def test_squatter_loses_the_password_when_the_real_owner_signs_in(client, provider):
    """Someone signs up with the victim's address and a password of their own;
    the victim later uses "Continue with Google". The squatter must not keep a
    way in."""
    email = _fresh()
    squat = _make(email, status="pending", verified=0)
    squatter_session = users.create_session(squat["id"], ip="203.0.113.9", ua="squatter")
    squatter_token = tokens.create(squat["id"], "squatter's")

    provider["email"] = email
    r = _go(client)

    assert _signed_in(client) and _where(r)[0] == "/console"
    u = users.get_by_email(email)
    assert u["id"] == squat["id"] and u["status"] == "active" and u["email_verified"] == 1

    # The squatter's password no longer works …
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 401
    # … and neither does a session they may already hold.
    assert users.session_user(squatter_session) is None
    assert tokens.verify(squatter_token) is None


def test_unverified_admin_keeps_the_password(client, provider):
    """Self-signup only mints role "user", so a squatter cannot be an admin;
    rotating an administrator's password would just lock them out."""
    email = _fresh()
    _make(email, role="admin", verified=0)
    provider["email"] = email
    _go(client)
    assert _signed_in(client)
    assert users.get_by_email(email)["email_verified"] == 1
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200


# ─────────────────────────── operator approval ───────────────────────────

@pytest.fixture()
def manual_approval(monkeypatch):
    monkeypatch.setattr(activation, "enabled", lambda: False)


def test_new_account_waits_for_approval_when_activation_is_off(client, provider, manual_approval):
    email = _fresh()
    provider["email"] = email
    r = _go(client)
    path, q, _ = _where(r)
    assert path == "/login" and q == {"oauth_notice": ["pending"]}
    assert users.get_by_email(email)["status"] == "pending"
    assert not _signed_in(client)


def test_pending_account_cannot_skip_the_approval_queue(client, provider, manual_approval):
    email = _fresh()
    _make(email, status="pending", verified=0)
    provider["email"] = email
    r = _go(client)
    assert _where(r)[1] == {"oauth_error": ["pending"]}
    assert users.get_by_email(email)["status"] == "pending"
    assert not _signed_in(client)
    # Not signed in, but also not left with the squatter's password: whoever
    # the operator eventually approves must be the person who owns the mailbox.
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 401


# ─────────────────────────── MFA ───────────────────────────

def test_mfa_account_gets_a_challenge_not_a_session(client, provider):
    email = _fresh()
    u = _make(email)
    db.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (u["id"],))
    provider["email"] = email

    r = _go(client, next_path="/billing")

    assert not _signed_in(client)           # Google is not a second factor
    path, query, fragment = _where(r)
    assert path == "/login" and query == {}
    frag = parse_qs(fragment)
    assert frag["methods"] == ["totp"] and frag["next"] == ["/billing"]
    # The challenge rides in the fragment so it never reaches a server log.
    from app import auth
    assert auth.read_challenge(frag["mfa"][0]) == u["id"]

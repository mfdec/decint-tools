"""Captcha: Google reCAPTCHA v2 alongside hCaptcha, on signup and login.

What matters: the right vendor is chosen from config, the right vendor is asked
to verify (with the right fields), every way verification can go wrong is a
refusal rather than a pass, and the real signup/login endpoints actually enforce
it — signup always, login once the IP has been failing.

`httpx.post` is replaced throughout, so none of this touches the network.
"""

import os
import tempfile

import httpx
import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "captcha.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.services import captcha  # noqa: E402

GOOGLE_URL = "https://www.google.com/recaptcha/api/siteverify"
HCAPTCHA_URL = "https://api.hcaptcha.com/siteverify"
PASSWORD = "a-perfectly-adequate-passphrase"
_seq = [0]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Every test starts with captcha off, no vendor keys, and no failure history."""
    s = captcha.settings
    for name, value in {
        "captcha_provider": "",
        "hcaptcha_site_key": "", "hcaptcha_secret": "",
        "recaptcha_site_key": "", "recaptcha_secret": "",
        "captcha_always_on_login": False,
    }.items():
        monkeypatch.setattr(s, name, value)
    captcha.reset_all()
    yield
    captcha.reset_all()


def use_recaptcha(monkeypatch):
    monkeypatch.setattr(captcha.settings, "recaptcha_site_key", "rc-site")
    monkeypatch.setattr(captcha.settings, "recaptcha_secret", "rc-secret")


def use_hcaptcha(monkeypatch):
    monkeypatch.setattr(captcha.settings, "hcaptcha_site_key", "hc-site")
    monkeypatch.setattr(captcha.settings, "hcaptcha_secret", "hc-secret")


class FakeVendor:
    """Stands in for httpx.post; records each call and answers with `reply`."""

    def __init__(self, reply=None, raises=None):
        self.reply = reply if reply is not None else {"success": True}
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, data=None, timeout=None):
        self.calls.append((url, dict(data or {})))
        if self.raises:
            raise self.raises
        return httpx.Response(200, json=self.reply, request=httpx.Request("POST", url))


@pytest.fixture()
def vendor(monkeypatch):
    fake = FakeVendor()
    monkeypatch.setattr(captcha.httpx, "post", fake)
    return fake


# ─────────────────────────── which vendor is active ───────────────────────────

def test_off_when_nothing_is_configured():
    assert captcha.provider() == ""
    assert not captcha.configured()
    assert captcha.site_key() == ""
    assert not captcha.required_for_signup()
    assert not captcha.required_for_login("1.2.3.4")


def test_recaptcha_alone_is_picked(monkeypatch):
    use_recaptcha(monkeypatch)
    assert captcha.provider() == "recaptcha"
    assert captcha.site_key() == "rc-site"
    assert captcha.required_for_signup()


def test_hcaptcha_alone_still_works(monkeypatch):
    use_hcaptcha(monkeypatch)
    assert captcha.provider() == "hcaptcha"
    assert captcha.site_key() == "hc-site"


def test_both_configured_prefers_hcaptcha_so_existing_deployments_do_not_switch(monkeypatch):
    use_hcaptcha(monkeypatch)
    use_recaptcha(monkeypatch)
    assert captcha.provider() == "hcaptcha"


@pytest.mark.parametrize("name", ["recaptcha", "google", " ReCaptcha "])
def test_explicit_choice_overrides_the_default_order(monkeypatch, name):
    use_hcaptcha(monkeypatch)
    use_recaptcha(monkeypatch)
    monkeypatch.setattr(captcha.settings, "captcha_provider", name)
    assert captcha.provider() == "recaptcha"
    assert captcha.site_key() == "rc-site"


def test_naming_a_vendor_without_keys_turns_captcha_off_not_over_to_the_other(monkeypatch):
    use_hcaptcha(monkeypatch)
    monkeypatch.setattr(captcha.settings, "captcha_provider", "recaptcha")
    assert captcha.provider() == ""
    assert not captcha.configured()


@pytest.mark.parametrize("field", ["recaptcha_site_key", "recaptcha_secret"])
def test_half_a_keypair_is_not_configured(monkeypatch, field):
    use_recaptcha(monkeypatch)
    monkeypatch.setattr(captcha.settings, field, "")
    assert captcha.provider() == ""


# ─────────────────────────── verification ───────────────────────────

def test_recaptcha_is_verified_against_google(monkeypatch, vendor):
    use_recaptcha(monkeypatch)
    ok, _ = captcha.verify("tok", "9.9.9.9")
    assert ok
    (url, data), = vendor.calls
    assert url == GOOGLE_URL
    assert data == {"secret": "rc-secret", "response": "tok", "remoteip": "9.9.9.9"}


def test_hcaptcha_is_verified_against_hcaptcha_with_its_site_key(monkeypatch, vendor):
    use_hcaptcha(monkeypatch)
    ok, _ = captcha.verify("tok", "9.9.9.9")
    assert ok
    (url, data), = vendor.calls
    assert url == HCAPTCHA_URL
    assert data["sitekey"] == "hc-site" and data["secret"] == "hc-secret"


def test_remote_ip_is_omitted_when_unknown(monkeypatch, vendor):
    use_recaptcha(monkeypatch)
    captcha.verify("tok")
    assert "remoteip" not in vendor.calls[0][1]


def test_a_failed_solve_is_refused_with_googles_reason(monkeypatch, vendor):
    use_recaptcha(monkeypatch)
    vendor.reply = {"success": False, "error-codes": ["timeout-or-duplicate"]}
    ok, reason = captcha.verify("stale")
    assert not ok and "timeout-or-duplicate" in reason


def test_no_token_is_refused_without_calling_the_vendor(monkeypatch, vendor):
    use_recaptcha(monkeypatch)
    ok, reason = captcha.verify("")
    assert not ok and reason == "Captcha required."
    assert vendor.calls == []


@pytest.mark.parametrize("boom", [
    httpx.ConnectError("down"),
    httpx.ReadTimeout("slow"),
])
def test_vendor_unreachable_fails_closed(monkeypatch, vendor, boom):
    use_recaptcha(monkeypatch)
    vendor.raises = boom
    ok, reason = captcha.verify("tok")
    assert not ok and "try again" in reason


def test_unparseable_reply_fails_closed(monkeypatch):
    use_recaptcha(monkeypatch)

    def junk(url, data=None, timeout=None):
        return httpx.Response(200, text="<html>not json</html>", request=httpx.Request("POST", url))

    monkeypatch.setattr(captcha.httpx, "post", junk)
    ok, _ = captcha.verify("tok")
    assert not ok


def test_http_error_status_fails_closed(monkeypatch):
    use_recaptcha(monkeypatch)

    def server_error(url, data=None, timeout=None):
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(captcha.httpx, "post", server_error)
    ok, _ = captcha.verify("tok")
    assert not ok


def test_verify_passes_when_captcha_is_off(vendor):
    assert captcha.verify("")[0] is True
    assert vendor.calls == []


# ─────────────────────────── the real endpoints ───────────────────────────

@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import mail

    # These tests create accounts. In a whole-suite run another module can bind
    # the DB layer to the .env database (production) before this module's temp
    # path takes effect, so check where it points — without connecting — and
    # refuse rather than leave test accounts there.
    if db._conn is not None:
        in_use = db._conn.execute("pragma database_list").fetchone()[2]
    else:
        in_use = str(db.settings.analytics_db)
    in_use = os.path.realpath(os.path.expanduser(in_use))
    if not in_use.startswith(os.path.realpath(tempfile.gettempdir()) + os.sep):
        pytest.skip(f"DB layer is bound to {in_use}, not a temp file; run this module on its own")
    db.get_conn()

    # SMTP may be configured in .env; mail from these signups must never leave
    # the test.
    monkeypatch.setattr(mail, "send_soon", lambda *a, **k: None)
    monkeypatch.setattr(mail, "notify_admins", lambda *a, **k: None)
    # The per-IP hourly signup cap is process-wide state shared with other test
    # modules; it is not what is under test here.
    monkeypatch.setattr(auth_router, "_signup_allowed", lambda ip: True)
    return TestClient(app)


def _account():
    _seq[0] += 1
    return {"email": f"cap{_seq[0]}@example.test", "username": f"capuser{_seq[0]}",
            "password": PASSWORD}


def test_signup_info_tells_the_page_which_widget_to_draw(monkeypatch, client):
    use_recaptcha(monkeypatch)
    info = client.get("/api/v1/auth/signup-info").json()
    assert info["captcha_provider"] == "recaptcha"
    assert info["captcha_site_key"] == "rc-site"
    assert info["captcha_on_signup"] is True


def test_signup_info_never_leaks_the_secret(monkeypatch, client):
    use_recaptcha(monkeypatch)
    assert "rc-secret" not in client.get("/api/v1/auth/signup-info").text


def test_signup_info_reports_no_captcha_when_off(client):
    info = client.get("/api/v1/auth/signup-info").json()
    assert info["captcha_provider"] == "" and info["captcha_site_key"] == ""
    assert info["captcha_on_signup"] is False


def test_signup_without_a_solved_captcha_is_refused(monkeypatch, client, vendor):
    use_recaptcha(monkeypatch)
    r = client.post("/api/v1/auth/signup", json=_account())
    assert r.status_code == 400 and r.json()["detail"] == "Captcha required."


def test_signup_with_a_rejected_captcha_is_refused(monkeypatch, client, vendor):
    use_recaptcha(monkeypatch)
    vendor.reply = {"success": False, "error-codes": ["invalid-input-response"]}
    r = client.post("/api/v1/auth/signup", json={**_account(), "captcha": "forged"})
    assert r.status_code == 400 and "invalid-input-response" in r.json()["detail"]


def test_a_rejected_captcha_does_not_create_the_account(monkeypatch, client, vendor):
    from app.services import users

    use_recaptcha(monkeypatch)
    vendor.reply = {"success": False}
    acct = _account()
    client.post("/api/v1/auth/signup", json={**acct, "captcha": "forged"})
    assert users.get_by_email(acct["email"]) is None


def test_signup_with_a_solved_captcha_succeeds(monkeypatch, client, vendor):
    use_recaptcha(monkeypatch)
    r = client.post("/api/v1/auth/signup", json={**_account(), "captcha": "good"})
    assert r.status_code == 201, r.text
    assert vendor.calls and vendor.calls[0][0] == GOOGLE_URL


def test_signup_needs_no_captcha_when_it_is_off(client, vendor):
    r = client.post("/api/v1/auth/signup", json=_account())
    assert r.status_code == 201, r.text
    assert vendor.calls == []


def test_login_demands_a_captcha_when_always_on(monkeypatch, client, vendor):
    use_recaptcha(monkeypatch)
    monkeypatch.setattr(captcha.settings, "captcha_always_on_login", True)
    r = client.post("/api/v1/auth/login", json={"email": "nobody@example.test", "password": "x" * 14})
    assert r.status_code == 400
    assert r.headers.get("X-Captcha-Required") == "1"
    assert r.json()["detail"] == "Captcha required."


def test_login_is_not_gated_until_the_ip_has_failed_enough(monkeypatch, client, vendor):
    use_recaptcha(monkeypatch)
    # Two failed attempts: still under the threshold, so no captcha is asked for.
    for _ in range(captcha.settings.captcha_login_threshold - 1):
        r = client.post("/api/v1/auth/login", json={"email": "nobody@example.test", "password": "x" * 14})
        assert r.headers.get("X-Captcha-Required") is None
    assert vendor.calls == []
    assert client.get("/api/v1/auth/signup-info").json()["captcha_on_login"] is False


def test_login_asks_for_a_captcha_once_the_ip_keeps_failing(monkeypatch, client, vendor):
    use_recaptcha(monkeypatch)
    for _ in range(captcha.settings.captcha_login_threshold):
        client.post("/api/v1/auth/login", json={"email": "nobody@example.test", "password": "x" * 14})
    assert client.get("/api/v1/auth/signup-info").json()["captcha_on_login"] is True
    r = client.post("/api/v1/auth/login", json={"email": "nobody@example.test", "password": "x" * 14})
    assert r.status_code == 400 and r.headers.get("X-Captcha-Required") == "1"

"""Self-serve activation: signup → emailed link → active → sign in → checkout.

The property under test is that a stranger can go from the pricing page to a
paid plan with no operator in the loop, and that the link which makes that
possible confirms nothing about which addresses exist.
"""

import os
import re
import tempfile
import time

import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "activation.db")
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "pending"
os.environ["SIGNUP_EMAIL_ACTIVATION"] = "true"
os.environ["SIGNUP_ACTIVATION_TTL_HOURS"] = "48"
# Every request here comes from one client address; the production per-IP
# throttles would stop the module after five signups.
os.environ["SIGNUP_MAX_PER_IP_PER_HOUR"] = "1000"
os.environ["PASSWORD_RESET_MAX_PER_IP_PER_HOUR"] = "1000"
# A relay "exists" so activation.enabled() is true; sending is stubbed below.
os.environ["SMTP_HOST"] = "smtp.example.test"
os.environ["SMTP_FROM"] = "noreply@example.test"
# Billing on, so the last step of the funnel can be exercised too.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_offline"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_offline"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import activation, mail, users  # noqa: E402
from app.services.billing import stripe_provider as cards  # noqa: E402
from app.services.billing import store  # noqa: E402

for mod in (activation, mail, store, cards):
    mod.settings = cfg.settings

PASSWORD = "a-perfectly-adequate-passphrase"
_seq = [0]


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mail, "send_soon", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    db.get_conn()
    return TestClient(app)


def _fresh():
    _seq[0] += 1
    return f"buyer{_seq[0]}@example.test", f"buyer{_seq[0]}"


def _signup(client, email, username, next_path=""):
    return client.post("/api/v1/auth/signup", json={
        "email": email, "username": username, "password": PASSWORD, "next": next_path,
    })


def _link_for(outbox, email):
    for to, subject, body in outbox:
        if to == email and "Activate" in subject:
            m = re.search(r"https://example\.test/activate\?token=(\S+)", body)
            assert m, body
            return m.group(1)
    return None


# ─────────────────────────── the funnel ───────────────────────────

def test_signup_is_pending_until_the_link_is_clicked(client, outbox):
    email, username = _fresh()
    r = _signup(client, email, username, next_path="/pricing")
    assert r.status_code == 201, r.text
    assert "activation link" in r.json()["note"]
    assert "approval" not in r.json()["note"]

    user = users.get_by_email(email)
    assert user["status"] == "pending" and user["email_verified"] == 0

    # Cannot sign in yet — and the refusal points at the email, not an operator.
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 403
    assert "activated" in r.json()["detail"] and "operator" not in r.json()["detail"]

    token = _link_for(outbox, email)
    assert token

    r = client.get("/api/v1/auth/activate-check", params={"token": token})
    assert r.json() == {"valid": True, "next": "/pricing"}

    r = client.post("/api/v1/auth/activate", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["activated"] is True and r.json()["already"] is False
    assert r.json()["next"] == "/pricing"

    user = users.get_by_email(email)
    assert user["status"] == "active" and user["email_verified"] == 1
    assert any(to == email and "active" in subject for to, subject, _ in outbox)


def test_activated_account_can_sign_in_and_reach_checkout(client, outbox, monkeypatch):
    email, username = _fresh()
    _signup(client, email, username)
    client.post("/api/v1/auth/activate", json={"token": _link_for(outbox, email)})

    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json().get("authenticated") is True or r.json().get("user")

    # The last step: a checkout starts with nobody having touched the console.
    monkeypatch.setattr(cards, "create_checkout", lambda u, plan, period, oid: f"https://checkout.test/{oid}")
    r = client.post(
        "/api/v1/billing/checkout",
        json={"plan": "pro", "period": "monthly", "provider": "stripe", "accept_terms": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://checkout.test/")


def test_clicking_twice_is_fine(client, outbox):
    email, username = _fresh()
    _signup(client, email, username)
    token = _link_for(outbox, email)
    client.post("/api/v1/auth/activate", json={"token": token})
    r = client.post("/api/v1/auth/activate", json={"token": token})
    assert r.status_code == 200
    assert r.json()["already"] is True
    assert users.get_by_email(email)["status"] == "active"


# ─────────────────────────── the link itself ───────────────────────────

@pytest.mark.parametrize("token", ["", "not-a-token", "eyJ1aWQiOjF9.forged.sig"])
def test_bad_links_are_refused(client, token):
    r = client.post("/api/v1/auth/activate", json={"token": token})
    assert r.status_code == 400
    assert client.get("/api/v1/auth/activate-check", params={"token": token}).json()["valid"] is False


def test_expired_link_is_refused(client, outbox, monkeypatch):
    email, username = _fresh()
    _signup(client, email, username)
    token = _link_for(outbox, email)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 49 * 3600)
    r = client.post("/api/v1/auth/activate", json={"token": token})
    assert r.status_code == 400
    assert users.get_by_email(email)["status"] == "pending"


def test_link_dies_if_the_address_changes(client, outbox):
    email, username = _fresh()
    _signup(client, email, username)
    token = _link_for(outbox, email)
    user = users.get_by_email(email)
    users.update(user["id"], email="someone-else@example.test")
    assert client.post("/api/v1/auth/activate", json={"token": token}).status_code == 400


def test_link_cannot_lift_a_suspension(client, outbox):
    email, username = _fresh()
    _signup(client, email, username)
    token = _link_for(outbox, email)
    users.update(users.get_by_email(email)["id"], status="suspended")
    assert client.post("/api/v1/auth/activate", json={"token": token}).status_code == 400
    assert users.get_by_email(email)["status"] == "suspended"


def test_next_must_be_a_local_path():
    assert activation.safe_next("/pricing") == "/pricing"
    assert activation.safe_next("https://evil.example/") == ""
    assert activation.safe_next("//evil.example/") == ""
    assert activation.safe_next("/\\evil.example") == ""
    assert activation.safe_next(None) == ""


# ─────────────────────────── no enumeration ───────────────────────────

def test_repeat_signup_of_a_pending_address_resends_the_link_and_says_nothing(client, outbox):
    email, username = _fresh()
    first = _signup(client, email, username).json()["note"]
    before = len([1 for to, s, _ in outbox if to == email and "Activate" in s])
    again = _signup(client, email, username + "x").json()
    assert again["created"] is True and again["note"] == first
    after = len([1 for to, s, _ in outbox if to == email and "Activate" in s])
    assert after == before + 1


def test_repeat_signup_of_an_active_address_sends_nothing_and_says_the_same(client, outbox):
    email, username = _fresh()
    first = _signup(client, email, username).json()["note"]
    client.post("/api/v1/auth/activate", json={"token": _link_for(outbox, email)})
    sent_before = len(outbox)
    again = _signup(client, email, username + "x").json()
    assert again["note"] == first
    assert len(outbox) == sent_before


def test_resend_answers_identically_for_pending_active_and_unknown(client, outbox):
    pending_email, u1 = _fresh()
    _signup(client, pending_email, u1)
    active_email, u2 = _fresh()
    _signup(client, active_email, u2)
    client.post("/api/v1/auth/activate", json={"token": _link_for(outbox, active_email)})

    outbox.clear()
    answers = []
    for addr in (pending_email, active_email, "nobody@example.test"):
        r = client.post("/api/v1/auth/activate/resend", json={"email": addr})
        assert r.status_code == 200
        answers.append(r.json())
    assert answers[0] == answers[1] == answers[2]
    # Exactly one link went out, to the account that could use it.
    assert [to for to, s, _ in outbox if "Activate" in s] == [pending_email]


# ─────────────────────────── the switch ───────────────────────────

def test_without_the_switch_signup_waits_for_an_operator(client, outbox, monkeypatch):
    monkeypatch.setattr(cfg.settings, "signup_email_activation", False)
    email, username = _fresh()
    r = _signup(client, email, username)
    assert "approval" in r.json()["note"]
    assert _link_for(outbox, email) is None
    assert any(to == email and "awaiting approval" in s for to, s, _ in outbox)
    r = client.post("/api/v1/auth/activate/resend", json={"email": email})
    assert r.status_code == 503


def test_without_a_relay_activation_is_off(monkeypatch):
    monkeypatch.setattr(cfg.settings, "smtp_host", "")
    assert activation.enabled() is False

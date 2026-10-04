"""Self-service account deletion (POST /api/v1/auth/account/delete).

App stores require that an account can be deleted from inside the app, and
that deleting it takes the data with it. The cases worth having: it cannot be
done without the password or by accident, it leaves no row behind in any table
keyed by the account, a card subscription is cancelled before anything is
deleted (and a Stripe failure deletes nothing), and the cancellation webhook
that follows cannot write the account's rows back.

No processor is contacted: `stripe.Subscription.cancel` is replaced per test.
Runs against a throwaway database; run it on its own, like tests/test_billing.py.
"""

import os
import tempfile

import pytest
import stripe
from fastapi.testclient import TestClient

import app.config as cfg

# Mutate the settings singleton in place (see test_email_flows.py for why).
cfg.settings.analytics_db = os.path.join(tempfile.mkdtemp(), "delete.db")
cfg.settings.operator_token = ""
cfg.settings.cookie_secure = False
cfg.settings.hcaptcha_site_key = ""
cfg.settings.hcaptcha_secret = ""
cfg.settings.stripe_secret_key = "sk_test_offline"
cfg.settings.public_base_url = "https://decint.tools"

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import mail, tickets, users  # noqa: E402
from app.services.billing import store  # noqa: E402
from app.services.billing import stripe_provider as cards  # noqa: E402

db._conn = None
db.get_conn()

PW = "correct-horse-battery-staple"
URL = "/api/v1/auth/account/delete"

# Tables keyed by user_id that must be empty for a deleted account.
USER_TABLES = (
    "sessions", "otp_codes", "login_tokens", "password_resets",
    "billing_customers", "billing_orders", "subscriptions", "entitlements",
    "usage_counters", "tickets",
)

SENT: list[tuple[str, str]] = []
mail.send_soon = lambda to, subject, body: SENT.append((to, subject))

_n = 0


def _make(role="user"):
    global _n
    _n += 1
    return users.create(f"person{_n}@example.test", PW, username=f"person{_n}", role=role)


def _login(user):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": user["email"], "password": PW})
    assert r.json().get("authenticated") is True, r.text
    return c


def _signed_in(c):
    return c.get("/api/v1/auth/session").json()["authenticated"]


def _rows(uid):
    return {t: db.one(f"SELECT COUNT(*) AS n FROM {t} WHERE user_id = ?", (uid,))["n"]
            for t in USER_TABLES}


@pytest.fixture()
def no_stripe(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe.Subscription, "cancel", lambda ref, **kw: calls.append(ref))
    return calls


@pytest.fixture(autouse=True)
def _spare_admin():
    # The last-admin rule is tested on purpose below; everywhere else there is
    # always another administrator, so it never gets in the way.
    if users.stats()["by_role"].get("admin", 0) < 1:
        _make(role="admin")


def test_needs_a_session():
    r = TestClient(app).post(URL, json={"current_password": PW, "confirm": "DELETE"})
    assert r.status_code == 401


def test_needs_the_typed_confirmation(no_stripe):
    u = _make()
    r = _login(u).post(URL, json={"current_password": PW, "confirm": "delete me"})
    assert r.status_code == 400
    assert users.get(u["id"]) is not None


def test_needs_the_current_password(no_stripe):
    u = _make()
    r = _login(u).post(URL, json={"current_password": "not-the-password", "confirm": "DELETE"})
    assert r.status_code == 401
    assert users.get(u["id"]) is not None


def test_removes_every_row_belonging_to_the_account(no_stripe):
    u = _make()
    other = _make()
    staff = users.get(db.one("SELECT id FROM users WHERE role = 'admin' LIMIT 1")["id"])
    uid = u["id"]
    now = "2026-01-01T00:00:00+00:00"

    db.execute("INSERT INTO otp_codes (user_id, method, code_hash, expires_at, created_at) "
               "VALUES (?,?,?,?,?)", (uid, "email", "h", now, now))
    db.execute("INSERT INTO login_tokens (user_id, token_hash, label, created_at) "
               "VALUES (?,?,?,?)", (uid, f"tok{uid}", "cli", now))
    db.execute("INSERT INTO password_resets (user_id, token_hash, expires_at, created_at) "
               "VALUES (?,?,?,?)", (uid, f"rst{uid}", now, now))
    db.execute("INSERT INTO usage_counters (user_id, window, count, updated_at) "
               "VALUES (?,?,?,?)", (uid, "all", 2, now))
    store.save_customer(uid, "stripe", f"cus_{uid}")
    store.create_order(uid, "stripe", "starter", "monthly")
    store.upsert_subscription(uid, "stripe", f"sub_{uid}", "starter", "active", None)
    store.grant(uid, "starter", "stripe", expires_at=None, reason="test")

    own = tickets.create(u, "billing", "my ticket", "help")
    tickets.reply(own["id"], staff, "staff answer", is_staff=True)
    theirs = tickets.create(other, "other", "their ticket", "hello")
    tickets.reply(theirs["id"], u, "a reply on someone else's thread", is_staff=False)

    c = _login(u)
    assert all(_rows(uid).values()), _rows(uid)
    SENT.clear()

    r = c.post(URL, json={"current_password": PW, "confirm": "DELETE"})

    assert r.status_code == 200 and r.json() == {"deleted": True}, r.text
    assert users.get(uid) is None
    assert not any(_rows(uid).values()), _rows(uid)
    assert db.one("SELECT COUNT(*) AS n FROM ticket_messages WHERE ticket_id = ?",
                  (own["id"],))["n"] == 0
    # The other person's thread keeps the message, minus the link to a person.
    kept = db.query("SELECT author_id FROM ticket_messages WHERE ticket_id = ? "
                    "AND body LIKE 'a reply%'", (theirs["id"],))
    assert kept == [{"author_id": None}]
    assert users.get(other["id"]) is not None
    assert no_stripe == [f"sub_{uid}"], "the card subscription was cancelled"
    assert not _signed_in(c)
    assert SENT and SENT[-1][0] == u["email"], "a confirmation went to the old address"
    assert any(e["action"] == "account.self_deleted" and e["target"] == u["email"]
               for e in users.audit_log(20))


def test_a_stripe_failure_deletes_nothing(monkeypatch):
    def boom(ref, **kw):
        raise stripe.APIConnectionError("network down")

    monkeypatch.setattr(stripe.Subscription, "cancel", boom)
    u = _make()
    store.upsert_subscription(u["id"], "stripe", f"sub_{u['id']}", "starter", "active", None)
    c = _login(u)

    r = c.post(URL, json={"current_password": PW, "confirm": "DELETE"})

    assert r.status_code == 502
    assert users.get(u["id"]) is not None
    assert _signed_in(c)


def test_the_cancellation_webhook_cannot_resurrect_rows(no_stripe):
    u = _make()
    uid = u["id"]
    store.upsert_subscription(uid, "stripe", f"sub_{uid}", "starter", "active", None)
    _login(u).post(URL, json={"current_password": PW, "confirm": "DELETE"})

    cards._apply_subscription({
        "id": f"sub_{uid}", "status": "canceled", "customer": f"cus_{uid}",
        "metadata": {"user_id": str(uid), "plan": "starter", "period": "monthly"},
    })

    assert not any(_rows(uid).values()), _rows(uid)


def test_the_last_administrator_cannot_delete_itself(no_stripe):
    admins = db.query("SELECT id FROM users WHERE role = 'admin'")
    for row in admins[1:]:
        users.update(row["id"], role="user")
    last = users.get(admins[0]["id"])

    r = _login(last).post(URL, json={"current_password": PW, "confirm": "DELETE"})

    assert r.status_code == 400
    assert "last administrator" in r.json()["detail"]
    assert users.get(last["id"]) is not None

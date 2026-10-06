"""Google Play rail (in-app subscriptions), offline.

The Play Developer API is replaced by an in-memory fake (`GOOGLE`), so these
cover what this server decides from Google's answers: a purchase is only
granted after Google confirms it AND it was made for this account; renewals,
cancellations and refunds arriving by notification move the plan; an upgrade
doesn't lose the plan when the replaced purchase expires; the website won't
sell a second plan on top of a Play one; and deleting the account stops the
Play renewal.

Run on its own, like tests/test_billing.py.
"""

import base64
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import app.config as cfg

cfg.settings.analytics_db = os.path.join(tempfile.mkdtemp(), "play.db")
cfg.settings.operator_token = ""
cfg.settings.cookie_secure = False
cfg.settings.public_base_url = "https://decint.tools"
cfg.settings.play_service_account_file = "/nonexistent/key.json"  # enables the rail; _api is faked
cfg.settings.play_rtdn_token = "rtdn-secret"
cfg.settings.play_package_name = "tools.decint.app"

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import mail, users  # noqa: E402
from app.services.billing import play_provider as play  # noqa: E402
from app.services.billing import store  # noqa: E402

db._conn = None
db.get_conn()
mail.send_soon = lambda *a, **k: None
mail.notify_admins = lambda *a, **k: None

PW = "correct-horse-battery-staple"

# ─────────────────────────── a fake Google ───────────────────────────

GOOGLE: dict[str, dict] = {}
CALLS: list[tuple[str, str]] = []


def fake_api(method, path, body=None):
    CALLS.append((method, path))
    if method == "GET" and path.startswith("purchases/subscriptionsv2/tokens/"):
        token = path.rsplit("/", 1)[1]
        if token not in GOOGLE:
            raise play.PlayError("Google Play API 404: purchase not found")
        return json.loads(json.dumps(GOOGLE[token]))
    if path.endswith(":acknowledge"):
        token = path.split("/tokens/")[1].split(":")[0]
        GOOGLE[token]["acknowledgementState"] = "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
        return {}
    if path.endswith(":cancel"):
        token = path.split("/tokens/")[1].split(":")[0]
        GOOGLE[token]["subscriptionState"] = "SUBSCRIPTION_STATE_CANCELED"
        GOOGLE[token]["lineItems"][0]["autoRenewingPlan"]["autoRenewEnabled"] = False
        return {}
    raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture(autouse=True)
def _fake_google(monkeypatch):
    monkeypatch.setattr(play, "_api", fake_api)
    CALLS.clear()


def purchase(token, user, product="starter", base_plan="monthly",
             state="SUBSCRIPTION_STATE_ACTIVE", ack="ACKNOWLEDGEMENT_STATE_PENDING",
             expiry="2030-01-01T00:00:00.000Z", linked=None, ref=None):
    GOOGLE[token] = {
        "subscriptionState": state,
        "acknowledgementState": ack,
        "latestOrderId": f"GPA.{token}",
        "externalAccountIdentifiers": {
            "obfuscatedExternalAccountId": ref or play.account_ref(user["id"]),
        },
        "lineItems": [{
            "productId": product,
            "expiryTime": expiry,
            "offerDetails": {"basePlanId": base_plan},
            "autoRenewingPlan": {
                "autoRenewEnabled": True,
                "recurringPrice": {"currencyCode": "EUR", "units": "5", "nanos": 490000000},
            },
        }],
        **({"linkedPurchaseToken": linked} if linked else {}),
    }


_n = 0


def make_user():
    global _n
    _n += 1
    return users.create(f"player{_n}@example.test", PW, username=f"player{_n}", tier="free")


def signed_in(user):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": user["email"], "password": PW})
    assert r.json().get("authenticated") is True, r.text
    return c


def tier(user):
    return users.get(user["id"])["tier"]


def rtdn(token, ntype=2, secret="rtdn-secret", package="tools.decint.app"):
    data = {"version": "1.0", "packageName": package, "eventTimeMillis": "1",
            "subscriptionNotification": {"version": "1.0", "notificationType": ntype,
                                         "purchaseToken": token, "subscriptionId": "starter"}}
    body = {"message": {"data": base64.b64encode(json.dumps(data).encode()).decode(),
                        "messageId": token + str(ntype)}, "subscription": "projects/x/subscriptions/y"}
    return TestClient(app).post(f"/api/v1/billing/webhook/play?token={secret}", json=body)


# ─────────────────────────── tests ───────────────────────────

def test_config_advertises_the_play_rail():
    assert TestClient(app).get("/api/v1/billing/config").json()["play_enabled"] is True


def test_a_reported_purchase_is_checked_granted_and_acknowledged():
    u = make_user()
    c = signed_in(u)
    acct = c.get("/api/v1/billing/play/account").json()
    assert acct["account_ref"] == play.account_ref(u["id"])
    assert acct["current"] is None and acct["billed_elsewhere"] is False

    purchase("tok-a", u, product="pro", base_plan="yearly")
    r = c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-a"})

    assert r.status_code == 200, r.text
    assert tier(u) == "pro"
    assert GOOGLE["tok-a"]["acknowledgementState"] == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
    summary = r.json()["summary"]
    assert summary["subscription"]["provider"] == "google_play"
    assert summary["subscription"]["period"] == "yearly"
    assert summary["renews"] is True
    order = store.order_by_reference("google_play", "GPA.tok-a")
    assert order and order["amount_cents"] == 549 and order["currency"] == "eur"

    # Reporting it again changes nothing and writes no second order.
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-a"})
    assert len([o for o in store.orders_for(u["id"]) if o["provider"] == "google_play"]) == 1

    current = c.get("/api/v1/billing/play/account").json()["current"]
    assert current == {"product_id": "pro", "base_plan_id": "yearly", "purchase_token": "tok-a"}


def test_someone_elses_purchase_cannot_be_redeemed():
    owner, thief = make_user(), make_user()
    signed_in(owner).get("/api/v1/billing/play/account")
    purchase("tok-owner", owner)

    r = signed_in(thief).post("/api/v1/billing/play/verify", json={"purchase_token": "tok-owner"})

    assert r.status_code == 403
    assert tier(thief) == "free"


def test_a_token_google_does_not_know_grants_nothing():
    u = make_user()
    r = signed_in(u).post("/api/v1/billing/play/verify", json={"purchase_token": "made-up"})
    assert r.status_code == 502
    assert tier(u) == "free"


def test_an_unknown_product_is_refused():
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-odd", u, product="enterprise")
    r = c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-odd"})
    assert r.status_code == 400
    assert tier(u) == "free"


def test_notifications_need_the_secret():
    assert rtdn("whatever", secret="wrong").status_code == 403
    assert rtdn("whatever", secret="").status_code == 403


def test_notifications_for_another_app_are_ignored():
    r = rtdn("tok-x", package="com.example.other")
    assert r.status_code == 200 and r.json()["ignored"] == "other package"


def test_a_purchase_the_app_never_reported_arrives_by_notification():
    u = make_user()
    signed_in(u).get("/api/v1/billing/play/account")  # the app linked the account
    purchase("tok-rtdn", u)

    r = rtdn("tok-rtdn", ntype=4)  # SUBSCRIPTION_PURCHASED

    assert r.status_code == 200, r.text
    assert tier(u) == "starter"
    assert GOOGLE["tok-rtdn"]["acknowledgementState"] == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"


def test_grace_keeps_the_plan_and_expiry_ends_it():
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-life", u)
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-life"})

    GOOGLE["tok-life"]["subscriptionState"] = "SUBSCRIPTION_STATE_IN_GRACE_PERIOD"
    rtdn("tok-life", ntype=6)
    assert tier(u) == "starter"

    GOOGLE["tok-life"]["subscriptionState"] = "SUBSCRIPTION_STATE_ON_HOLD"
    rtdn("tok-life", ntype=5)
    assert tier(u) == "free"


def test_cancelled_keeps_the_plan_until_it_expires():
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-cxl", u)
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-cxl"})

    GOOGLE["tok-cxl"]["subscriptionState"] = "SUBSCRIPTION_STATE_CANCELED"
    GOOGLE["tok-cxl"]["lineItems"][0]["autoRenewingPlan"]["autoRenewEnabled"] = False
    rtdn("tok-cxl", ntype=3)

    assert tier(u) == "starter"
    assert store.summary(users.get(u["id"]))["renews"] is False

    GOOGLE["tok-cxl"]["subscriptionState"] = "SUBSCRIPTION_STATE_EXPIRED"
    rtdn("tok-cxl", ntype=13)
    assert tier(u) == "free"


def test_an_upgrade_survives_the_old_purchase_expiring():
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-old", u, product="starter")
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-old"})

    purchase("tok-new", u, product="pro", linked="tok-old")
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-new"})
    assert tier(u) == "pro"
    assert store.subscription_by_ref("tok-old")["status"] == "canceled"

    GOOGLE["tok-old"]["subscriptionState"] = "SUBSCRIPTION_STATE_EXPIRED"
    rtdn("tok-old", ntype=13)
    assert tier(u) == "pro", "the replaced purchase expiring must not take the new plan away"


def test_the_website_wont_sell_a_second_plan_to_a_play_subscriber():
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-web", u)
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-web"})

    r = c.post(
        "/api/v1/billing/checkout",
        json={"plan": "pro", "period": "monthly", "provider": "stripe", "accept_terms": True},
    )

    assert r.status_code == 400
    assert "Google Play" in r.json()["detail"]


def test_a_plan_paid_elsewhere_is_not_sold_again_in_the_app():
    u = make_user()
    store.grant(u["id"], "pro", "nowpayments", expires_at="2030-01-01T00:00:00+00:00")
    acct = signed_in(u).get("/api/v1/billing/play/account").json()
    assert acct["billed_elsewhere"] is True and acct["current"] is None


def test_deleting_the_account_stops_the_play_renewal():
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-del", u)
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-del"})

    r = c.post("/api/v1/auth/account/delete", json={"current_password": PW, "confirm": "DELETE"})

    assert r.status_code == 200, r.text
    assert ("POST", "purchases/subscriptions/starter/tokens/tok-del:cancel") in CALLS
    assert users.get(u["id"]) is None
    # Google's follow-up notification finds no account and writes nothing back.
    assert rtdn("tok-del", ntype=3).status_code == 200
    assert store.subscription_by_ref("tok-del") is None


def test_a_play_failure_during_deletion_deletes_nothing(monkeypatch):
    u = make_user()
    c = signed_in(u)
    c.get("/api/v1/billing/play/account")
    purchase("tok-keep", u)
    c.post("/api/v1/billing/play/verify", json={"purchase_token": "tok-keep"})

    def down(method, path, body=None):
        raise play.PlayError("Google Play API unreachable: ConnectError")

    monkeypatch.setattr(play, "_api", down)
    r = c.post("/api/v1/auth/account/delete", json={"current_password": PW, "confirm": "DELETE"})

    assert r.status_code == 502
    assert users.get(u["id"]) is not None

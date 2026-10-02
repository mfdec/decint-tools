"""Billing logic, offline. No processor is contacted.

The cases worth having are the ones where getting it wrong costs real money:
a replayed webhook buying a second month, an underpayment buying a full one, a
forged callback buying anything at all, and a lapsed plan that never lapses.
"""

import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "billing.db")
os.environ["NOWPAYMENTS_IPN_SECRET"] = "ipn-test-secret"
os.environ["NOWPAYMENTS_API_KEY"] = "api-test-key"
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
# A configured catalogue, so plan resolution by Price id and in-place plan
# changes can be exercised without a processor.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_offline"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_offline"
os.environ["STRIPE_PRICE_STARTER_MONTHLY"] = "price_starter_m"
os.environ["STRIPE_PRICE_STARTER_YEARLY"] = "price_starter_y"
os.environ["STRIPE_PRICE_PRO_MONTHLY"] = "price_pro_m"
os.environ["STRIPE_PRICE_PRO_SEMIANNUAL"] = "price_pro_s"
os.environ["STRIPE_PRICE_PRO_YEARLY"] = "price_pro_y"
# Pinned, not inherited: a deployment .env with COOKIE_SECURE=true makes every
# session cookie Secure, and the test client speaks plain HTTP — so it silently
# drops the cookie and every authenticated request 401s. That reads as a broken
# authorisation check rather than a harness problem, so pin it here.
os.environ["COOKIE_SECURE"] = "false"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import users  # noqa: E402
from app.services.billing import nowpayments_provider as crypto  # noqa: E402
from app.services.billing import plans, store  # noqa: E402
from app.services.billing import stripe_provider as cards  # noqa: E402

# The modules bound `settings` at import time, so patch the name each holds.
for mod in (store, crypto, cards):
    mod.settings = cfg.settings


@pytest.fixture()
def user():
    db.get_conn()
    email = f"payer{db.one('SELECT COUNT(*) AS n FROM users')['n']}@example.test"
    return users.create(email, "a-long-enough-password", tier="free")


# ─────────────────────────── catalogue ───────────────────────────

def test_only_purchasable_plans_can_be_bought():
    assert plans.require_purchasable("starter", "monthly").key == "starter"
    for bad in [("enterprise", "monthly"), ("free", "monthly"), ("nope", "monthly")]:
        with pytest.raises(ValueError):
            plans.require_purchasable(*bad)


def test_unknown_period_is_refused():
    with pytest.raises(ValueError):
        plans.require_purchasable("starter", "weekly")


def test_yearly_is_ten_months_not_twelve():
    pro = plans.get("pro")
    assert pro.yearly_cents == pro.monthly_cents * 10
    assert plans.months_for("yearly") == 12


def test_semiannual_is_fifteen_percent_off_both_paid_tiers():
    assert plans.months_for("semiannual") == 6
    # 495 * 6 = 2970, 15% off = 2524.5, rounded up to the nearest cent.
    assert plans.get("starter").semiannual_cents == 2525
    # 1495 * 6 = 8970, 15% off = 7624.5, rounded up to the nearest cent.
    assert plans.get("pro").semiannual_cents == 7625
    for key in ("starter", "pro"):
        plan = plans.get(key)
        assert plan.cents("semiannual") == plan.semiannual_cents


def test_tier_quota_matches_catalogue():
    # The gate and the price list must not drift apart.
    assert users.TIER_QUOTA == {p.key: p.quota for p in plans.PLANS}
    assert set(users.TIERS) == {p.key for p in plans.PLANS}


# ─────────────────────────── month arithmetic ───────────────────────────

@pytest.mark.parametrize(
    "start,months,expected",
    [
        ((2026, 1, 31), 1, (2026, 2, 28)),   # clamps into a short month
        ((2028, 1, 31), 1, (2028, 2, 29)),   # leap year
        ((2026, 8, 27), 12, (2027, 8, 27)),
        ((2026, 12, 15), 1, (2027, 1, 15)),  # year rollover
    ],
)
def test_add_months(start, months, expected):
    got = store.add_months(datetime(*start, tzinfo=timezone.utc), months)
    assert (got.year, got.month, got.day) == expected


# ─────────────────────────── entitlements ───────────────────────────

def test_extend_stacks_when_renewing_early(user):
    store.extend(user["id"], "starter", "nowpayments", 1)
    first = store.entitlement(user["id"])["expires_at"]
    store.extend(user["id"], "starter", "nowpayments", 1)
    second = store.entitlement(user["id"])["expires_at"]
    # Paying again a week early must add a month to the END, not from today.
    assert second > first
    assert store.add_months(store._parse(first), 1) == store._parse(second)


def test_extend_syncs_the_tier_column(user):
    store.extend(user["id"], "pro", "nowpayments", 1)
    assert users.get(user["id"])["tier"] == "pro"


def test_sweep_drops_only_plans_past_grace(user):
    store.extend(user["id"], "starter", "nowpayments", 1)
    # Inside grace: expired yesterday.
    db.execute(
        "UPDATE entitlements SET expires_at = ? WHERE user_id = ?",
        (store._iso(datetime.now(timezone.utc) - timedelta(days=1)), user["id"]),
    )
    store.sweep()
    assert users.get(user["id"])["tier"] == "starter"

    # Past grace.
    past = datetime.now(timezone.utc) - timedelta(
        days=cfg.settings.billing_grace_days + 2
    )
    db.execute(
        "UPDATE entitlements SET expires_at = ? WHERE user_id = ?",
        (store._iso(past), user["id"]),
    )
    store.sweep()
    assert users.get(user["id"])["tier"] == "free"


def test_never_expiring_grant_survives_the_sweep(user):
    store.grant(user["id"], "enterprise", "manual", expires_at=None)
    store.sweep()
    assert users.get(user["id"])["tier"] == "enterprise"


# ─────────────────────────── IPN signature ───────────────────────────

def _sign(payload: dict, secret: str = "ipn-test-secret") -> tuple[bytes, str]:
    raw = json.dumps(payload).encode()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), canonical, hashlib.sha512).hexdigest()
    return raw, sig


def test_signature_accepts_a_genuine_callback():
    raw, sig = _sign({"payment_id": 1, "payment_status": "finished", "b": 2})
    assert crypto.verify_signature(raw, sig)["payment_id"] == 1


def test_signature_survives_key_reordering():
    # The signature is over the SORTED body, so wire order must not matter.
    body = {"z": 1, "a": 2, "payment_status": "finished"}
    _, sig = _sign(body)
    reordered = json.dumps({"payment_status": "finished", "a": 2, "z": 1}).encode()
    assert crypto.verify_signature(reordered, sig)


@pytest.mark.parametrize("sig", ["", "deadbeef", "0" * 128])
def test_signature_rejects_forgeries(sig):
    raw, _ = _sign({"payment_id": 1, "payment_status": "finished"})
    with pytest.raises(PermissionError):
        crypto.verify_signature(raw, sig)


def test_signature_rejects_a_tampered_body():
    raw, sig = _sign({"payment_id": 1, "price_amount": 49.0})
    tampered = raw.replace(b"49.0", b"0.01")
    with pytest.raises(PermissionError):
        crypto.verify_signature(tampered, sig)


# ─────────────────────────── the crypto rail end to end ───────────────────────────

def _ipn(order, status="finished", payment_id=None, **over):
    # Unique per order: the dedupe key is payment_id + status, and NOWPayments
    # payment ids are unique per account, so sharing one across orders here
    # would test the fixture rather than the code.
    body = {
        "payment_id": payment_id or f"pay-{order['id']}",
        "payment_status": status,
        "order_id": crypto._order_ref(order["id"]),
        "price_amount": round(order["amount_cents"] / 100, 2),
        "price_currency": "usd",
        "pay_currency": "btc",
        "actually_paid": 0.001,
    }
    body.update(over)
    return _sign(body)


def test_finished_payment_grants_the_plan(user):
    order = store.create_order(user["id"], "nowpayments", "pro", "monthly")
    raw, sig = _ipn(order)
    assert crypto.handle_webhook(raw, sig)["handled"] is True
    assert users.get(user["id"])["tier"] == "pro"
    assert store.get_order(order["id"])["status"] == store.PAID


def test_replayed_callback_does_not_buy_a_second_month(user):
    order = store.create_order(user["id"], "nowpayments", "starter", "monthly")
    raw, sig = _ipn(order)
    crypto.handle_webhook(raw, sig)
    first = store.entitlement(user["id"])["expires_at"]

    result = crypto.handle_webhook(raw, sig)
    assert result["handled"] is False and result["reason"] == "duplicate"
    assert store.entitlement(user["id"])["expires_at"] == first


def test_underpayment_grants_nothing(user):
    order = store.create_order(user["id"], "nowpayments", "starter", "monthly")
    raw, sig = _ipn(order, status="partially_paid")
    crypto.handle_webhook(raw, sig)
    assert users.get(user["id"])["tier"] == "free"
    assert store.get_order(order["id"])["status"] == store.PENDING


def test_price_mismatch_grants_nothing(user):
    order = store.create_order(user["id"], "nowpayments", "pro", "monthly")
    raw, sig = _ipn(order, price_amount=0.01)
    result = crypto.handle_webhook(raw, sig)
    assert result["handled"] is False and result["reason"] == "price mismatch"
    assert users.get(user["id"])["tier"] == "free"


@pytest.mark.parametrize("status", ["waiting", "confirming", "confirmed", "sending"])
def test_in_flight_statuses_grant_nothing(user, status):
    order = store.create_order(user["id"], "nowpayments", "starter", "monthly")
    raw, sig = _ipn(order, status=status)
    crypto.handle_webhook(raw, sig)
    assert users.get(user["id"])["tier"] == "free"


@pytest.mark.parametrize("status", ["failed", "expired"])
def test_dead_statuses_grant_nothing(user, status):
    order = store.create_order(user["id"], "nowpayments", "starter", "monthly")
    raw, sig = _ipn(order, status=status)
    crypto.handle_webhook(raw, sig)
    assert users.get(user["id"])["tier"] == "free"


def test_refund_revokes_access(user):
    order = store.create_order(user["id"], "nowpayments", "pro", "monthly")
    raw, sig = _ipn(order)
    crypto.handle_webhook(raw, sig)
    assert users.get(user["id"])["tier"] == "pro"

    raw, sig = _ipn(order, status="refunded")
    crypto.handle_webhook(raw, sig)
    assert users.get(user["id"])["tier"] == "free"


def test_callback_for_an_unknown_order_is_ignored():
    raw, sig = _sign(
        {"payment_id": "x", "payment_status": "finished", "order_id": "decint-999999"}
    )
    assert crypto.handle_webhook(raw, sig)["handled"] is False


# ─────────────────────────── the card rail ───────────────────────────

def _subscription(user_id, plan="pro", status="active", days=30, **over):
    end = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
    sub = {
        "id": f"sub_{user_id}_{status}",
        "status": status,
        "customer": "cus_test",
        "cancel_at_period_end": False,
        "metadata": {"user_id": str(user_id), "plan": plan},
        "items": {"data": [{"current_period_end": end, "price": {"id": "price_x"}}]},
    }
    sub.update(over)
    return sub


@pytest.mark.parametrize("status", ["active", "trialing", "past_due"])
def test_live_subscription_grants_the_plan(user, status):
    cards._apply_subscription(_subscription(user["id"], status=status))
    assert users.get(user["id"])["tier"] == "pro"


@pytest.mark.parametrize("status", ["canceled", "unpaid", "incomplete_expired"])
def test_dead_subscription_revokes(user, status):
    cards._apply_subscription(_subscription(user["id"], status="active"))
    cards._apply_subscription(_subscription(user["id"], status=status))
    assert users.get(user["id"])["tier"] == "free"


def test_subscription_expiry_tracks_the_period_end(user):
    cards._apply_subscription(_subscription(user["id"], days=30))
    ent = store.entitlement(user["id"])
    assert 28 <= (store._parse(ent["expires_at"]) - datetime.now(timezone.utc)).days <= 30


def test_subscription_with_no_resolvable_account_is_ignored():
    sub = _subscription(0, metadata={}, customer="cus_unknown")
    cards._apply_subscription(sub)  # must not raise
    assert store.subscription_by_ref(sub["id"]) is None


# ─────────────────────────── expiry notices ───────────────────────────
# The prepaid rail's safety net: a crypto customer who is not warned simply
# discovers the expiry by losing access.

@pytest.fixture()
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    from app.services import mail

    monkeypatch.setattr(mail, "available", lambda: True)
    monkeypatch.setattr(
        mail, "send_soon", lambda to, subject, body: sent.append((to, subject, body))
    )
    return sent


def _expire_in(user_id, days):
    db.execute(
        "UPDATE entitlements SET expires_at = ? WHERE user_id = ?",
        (store._iso(datetime.now(timezone.utc) + timedelta(days=days)), user_id),
    )


def test_prepaid_expiry_is_announced_once(user, outbox):
    store.extend(user["id"], "starter", "nowpayments", 1)
    _expire_in(user["id"], 3)

    assert store.notify_expiring() == 1
    assert len(outbox) == 1
    assert "ends in 3 days" in outbox[0][1] or "ends" in outbox[0][1]

    # A second pass must not nag.
    assert store.notify_expiring() == 0
    assert len(outbox) == 1


def test_buying_another_period_earns_another_warning(user, outbox):
    store.extend(user["id"], "starter", "nowpayments", 1)
    _expire_in(user["id"], 2)
    store.notify_expiring()
    assert len(outbox) == 1

    store.extend(user["id"], "starter", "nowpayments", 1)
    _expire_in(user["id"], 2)
    assert store.notify_expiring() == 1
    assert len(outbox) == 2


def test_expiry_far_out_is_not_announced(user, outbox):
    store.extend(user["id"], "starter", "nowpayments", 1)
    _expire_in(user["id"], cfg.settings.billing_expiry_notice_days + 5)
    assert store.notify_expiring() == 0
    assert outbox == []


def test_renewing_card_subscription_is_never_warned(user, outbox):
    cards._apply_subscription(_subscription(user["id"], status="active", days=3))
    assert store.notify_expiring() == 0
    assert outbox == []


def test_cancelled_card_subscription_is_warned(user, outbox):
    cards._apply_subscription(
        _subscription(user["id"], status="active", days=3, cancel_at_period_end=True)
    )
    assert store.notify_expiring() == 1
    assert len(outbox) == 1


def test_lapsing_emails_the_customer(user, outbox):
    store.extend(user["id"], "pro", "nowpayments", 1)
    past = datetime.now(timezone.utc) - timedelta(
        days=cfg.settings.billing_grace_days + 2
    )
    db.execute(
        "UPDATE entitlements SET expires_at = ? WHERE user_id = ?",
        (store._iso(past), user["id"]),
    )
    store.sweep()
    assert users.get(user["id"])["tier"] == "free"
    assert any("has ended" in subject for _, subject, _ in outbox)


# ─────────────────────────── the card rail: shapes and resolution ───────────────────────────
# The account this ships against defaults to an API version after Basil
# (2025-03-31), where the fields the handlers need moved. These pin the new
# shapes so a renewal cannot silently resolve to nothing again.

def _basil_sub(user_id, price="price_pro_m", status="active", days=30, meta_plan=None, **over):
    end = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
    interval = "year" if price.endswith("_y") else "month"
    sub = {
        "id": f"sub_basil_{user_id}",
        "status": status,
        "customer": "cus_test",
        "cancel_at_period_end": False,
        "metadata": {"user_id": str(user_id), "plan": meta_plan or "starter", "period": "monthly"},
        "items": {"data": [{
            "id": "si_1",
            "current_period_end": end,
            "price": {"id": price, "recurring": {"interval": interval}},
        }]},
    }
    sub.update(over)
    return sub


def test_plan_resolves_from_the_price_not_stale_metadata(user):
    # Metadata still says the plan bought at checkout; the Price says what is
    # being charged now. After a portal switch only the latter is true.
    sub = _basil_sub(user["id"], price="price_pro_m", meta_plan="starter")
    assert cards._resolve_plan(sub) == "pro"
    assert cards._resolve_period(sub) == "monthly"
    assert cards._resolve_period(_basil_sub(user["id"], price="price_pro_y")) == "yearly"


def test_plan_falls_back_to_metadata_for_an_unknown_price(user):
    sub = _basil_sub(user["id"], price="price_inline_xyz", meta_plan="starter")
    assert cards._resolve_plan(sub) == "starter"


def test_period_falls_back_to_the_interval_for_an_unknown_price(user):
    sub = _basil_sub(user["id"], price="price_inline_xyz")
    sub["items"]["data"][0]["price"]["recurring"]["interval"] = "year"
    assert cards._resolve_period(sub) == "yearly"


def test_period_falls_back_to_six_month_interval_for_an_unknown_price(user):
    # Stripe has no native "semiannual" interval — an inline-priced 6-month
    # subscription is "month" with interval_count=6, which must not be read
    # back as plain monthly.
    sub = _basil_sub(user["id"], price="price_inline_xyz")
    sub["items"]["data"][0]["price"]["recurring"] = {"interval": "month", "interval_count": 6}
    assert cards._resolve_period(sub) == "semiannual"


def test_invoice_subscription_is_read_on_both_sides_of_basil():
    assert cards._invoice_subscription({"subscription": "sub_old"}) == "sub_old"
    assert cards._invoice_subscription({"subscription": {"id": "sub_old"}}) == "sub_old"
    assert cards._invoice_subscription(
        {"parent": {"subscription_details": {"subscription": "sub_new"}}}
    ) == "sub_new"
    assert cards._invoice_subscription({"parent": None}) is None


def test_applying_a_subscription_records_its_period(user):
    cards._apply_subscription(_basil_sub(user["id"], price="price_starter_y"))
    row = store.active_subscription(user["id"])
    assert row["plan"] == "starter" and row["period"] == "yearly"
    assert store.summary(users.get(user["id"]))["subscription"]["period"] == "yearly"


# ─────────────────────────── the card rail: webhook dispatch ───────────────────────────

_event_seq = [0]


def _event(kind, obj):
    _event_seq[0] += 1
    return {"id": f"evt_{_event_seq[0]}", "type": kind, "data": {"object": obj}}


@pytest.fixture()
def stripe_subs(monkeypatch):
    """Stand in for Subscription.retrieve: whatever the test registers."""
    subs: dict[str, dict] = {}
    import stripe

    monkeypatch.setattr(
        stripe.Subscription, "retrieve", lambda sid, **kw: subs[sid]
    )
    return subs


def test_expired_checkout_closes_the_order(user):
    order = store.create_order(user["id"], "stripe", "pro", "monthly")
    store.attach_reference(order["id"], "cs_expired_1")
    assert cards.dispatch(_event("checkout.session.expired", {"id": "cs_expired_1"}))["handled"]
    assert store.get_order(order["id"])["status"] == store.EXPIRED
    assert users.get(user["id"])["tier"] == "free"


def test_expired_checkout_never_reopens_a_paid_order(user):
    order = store.create_order(user["id"], "stripe", "pro", "monthly")
    store.attach_reference(order["id"], "cs_paid_then_expired")
    store.set_order_status(order["id"], store.PAID)
    cards.dispatch(_event("checkout.session.expired", {"id": "cs_paid_then_expired"}))
    assert store.get_order(order["id"])["status"] == store.PAID


def test_renewal_extends_access_and_lands_in_history(user, stripe_subs):
    sub = _basil_sub(user["id"], price="price_pro_m", days=60)
    stripe_subs[sub["id"]] = sub
    invoice = {
        "id": "in_renewal_1",
        "billing_reason": "subscription_cycle",
        "amount_paid": 4500,
        "currency": "usd",
        # Basil shape: no top-level `subscription`.
        "parent": {"subscription_details": {"subscription": sub["id"]}},
    }
    assert cards.dispatch(_event("invoice.paid", invoice))["handled"] is True

    assert users.get(user["id"])["tier"] == "pro"
    ent = store.entitlement(user["id"])
    assert 58 <= (store._parse(ent["expires_at"]) - datetime.now(timezone.utc)).days <= 60

    history = store.orders_for(user["id"])
    assert len(history) == 1
    assert history[0]["status"] == store.PAID
    assert history[0]["amount_cents"] == 4500
    assert history[0]["plan"] == "pro"


def test_replayed_renewal_is_one_line_of_history(user, stripe_subs):
    sub = _basil_sub(user["id"])
    stripe_subs[sub["id"]] = sub
    invoice = {
        "id": "in_renewal_2", "billing_reason": "subscription_cycle",
        "amount_paid": 4500, "currency": "usd",
        "parent": {"subscription_details": {"subscription": sub["id"]}},
    }
    cards.dispatch(_event("invoice.paid", invoice))
    # Stripe retries under a NEW event id after a slow 2xx; the invoice is
    # the thing that must not be counted twice.
    cards.dispatch(_event("invoice.paid", invoice))
    assert len(store.orders_for(user["id"])) == 1


def test_first_invoice_does_not_duplicate_the_checkout_order(user, stripe_subs):
    order = store.create_order(user["id"], "stripe", "pro", "monthly")
    store.attach_reference(order["id"], "cs_first")
    sub = _basil_sub(user["id"])
    stripe_subs[sub["id"]] = sub
    cards.dispatch(_event("checkout.session.completed", {"id": "cs_first", "subscription": sub["id"]}))
    cards.dispatch(_event("invoice.paid", {
        "id": "in_first", "billing_reason": "subscription_create",
        "amount_paid": 4500, "currency": "usd",
        "parent": {"subscription_details": {"subscription": sub["id"]}},
    }))
    history = store.orders_for(user["id"])
    assert len(history) == 1 and history[0]["status"] == store.PAID
    assert users.get(user["id"])["tier"] == "pro"


def test_unknown_event_types_are_ignored_not_errors():
    r = cards.dispatch(_event("customer.created", {"id": "cus_x"}))
    assert r == {"handled": False, "reason": "ignored", "type": "customer.created"}


# ─────────────────────────── abandoned checkouts ───────────────────────────

def test_sweep_closes_abandoned_checkouts_but_not_fresh_ones(user):
    stale = store.create_order(user["id"], "stripe", "pro", "monthly")
    fresh = store.create_order(user["id"], "stripe", "pro", "monthly")
    old = store._iso(datetime.now(timezone.utc) - timedelta(hours=store.ORDER_TTL_HOURS + 1))
    db.execute("UPDATE billing_orders SET created_at = ? WHERE id = ?", (old, stale["id"]))
    store.sweep()
    assert store.get_order(stale["id"])["status"] == store.EXPIRED
    assert store.get_order(fresh["id"])["status"] == store.PENDING


def test_late_crypto_settlement_still_grants_after_expiry(user):
    # The sweep closed the row; the coins arrived anyway. Money wins.
    order = store.create_order(user["id"], "nowpayments", "pro", "monthly")
    store.set_order_status(order["id"], store.EXPIRED, detail="abandoned")
    raw, sig = _ipn(order)
    assert crypto.handle_webhook(raw, sig)["handled"] is True
    assert users.get(user["id"])["tier"] == "pro"
    assert store.get_order(order["id"])["status"] == store.PAID


# ─────────────────────────── in-place plan changes ───────────────────────────
# The "automatic purchase": a subscriber picks another plan and the difference
# is charged to the card Stripe already holds. No second Checkout, no second
# subscription.

@pytest.fixture()
def stripe_modify(monkeypatch):
    """Record Subscription.modify calls and return the updated object."""
    import stripe

    calls: list[tuple[str, dict]] = []
    state: dict = {}

    def retrieve(sid, **kw):
        return state["sub"]

    def modify(sid, **kw):
        calls.append((sid, kw))
        sub = dict(state["sub"])
        if "items" in kw:
            new_price = kw["items"][0]["price"]
            interval = "year" if new_price.endswith("_y") else "month"
            sub["items"] = {"data": [{
                "id": "si_1",
                "current_period_end": state["sub"]["items"]["data"][0]["current_period_end"],
                "price": {"id": new_price, "recurring": {"interval": interval}},
            }]}
            sub["latest_invoice"] = state.get("invoice") or {
                "id": f"in_change_{sid}", "amount_paid": 1600, "currency": "usd",
            }
        if "cancel_at_period_end" in kw:
            sub["cancel_at_period_end"] = kw["cancel_at_period_end"]
        if "metadata" in kw:
            sub["metadata"] = kw["metadata"]
        state["sub"] = sub
        return sub

    monkeypatch.setattr(stripe.Subscription, "retrieve", retrieve)
    monkeypatch.setattr(stripe.Subscription, "modify", modify)
    state["calls"] = calls
    return state


def _subscribe(user, price="price_starter_m", **over):
    sub = _basil_sub(user["id"], price=price, **over)
    cards._apply_subscription(sub)
    return sub


def test_upgrade_is_applied_in_place_and_charged_to_the_card(user, stripe_modify):
    stripe_modify["sub"] = _subscribe(user, "price_starter_m")
    assert users.get(user["id"])["tier"] == "starter"

    result = cards.change_plan(users.get(user["id"]), plans.get("pro"), "monthly")

    assert result["action"] == "changed"
    sid, kw = stripe_modify["calls"][-1]
    assert kw["items"] == [{"id": "si_1", "price": "price_pro_m"}]
    assert kw["proration_behavior"] == "always_invoice"
    assert kw["payment_behavior"] == "error_if_incomplete"
    assert "billing_cycle_anchor" not in kw          # same interval: keep the renewal date
    assert kw["metadata"]["plan"] == "pro"           # so a later webhook agrees

    assert users.get(user["id"])["tier"] == "pro"
    assert store.active_subscription(user["id"])["plan"] == "pro"
    order = store.get_order(result["order_id"])
    assert order["status"] == store.PAID
    assert order["amount_cents"] == 1600             # what the card was charged, pro rata
    assert order["reference"] == f"in_change_{sid}"


def test_interval_change_restarts_the_billing_cycle(user, stripe_modify):
    stripe_modify["sub"] = _subscribe(user, "price_pro_m")
    cards.change_plan(users.get(user["id"]), plans.get("pro"), "yearly")
    _, kw = stripe_modify["calls"][-1]
    assert kw["billing_cycle_anchor"] == "now"
    assert store.active_subscription(user["id"])["period"] == "yearly"


def test_switching_to_semiannual_also_restarts_the_billing_cycle(user, stripe_modify):
    # Monthly -> semiannual is still "month" on the Stripe side (interval_count
    # changes, not interval) — the anchor reset must key off both, not just the
    # interval string, or this switch would wrongly keep the old renewal date.
    stripe_modify["sub"] = _subscribe(user, "price_pro_m")
    result = cards.change_plan(users.get(user["id"]), plans.get("pro"), "semiannual")
    _, kw = stripe_modify["calls"][-1]
    assert kw["items"] == [{"id": "si_1", "price": "price_pro_s"}]
    assert kw["billing_cycle_anchor"] == "now"
    assert result["action"] == "changed"
    assert store.active_subscription(user["id"])["period"] == "semiannual"


def test_same_plan_is_refused_without_writing_an_order(user, stripe_modify):
    stripe_modify["sub"] = _subscribe(user, "price_pro_m")
    with pytest.raises(ValueError):
        cards.change_plan(users.get(user["id"]), plans.get("pro"), "monthly")
    assert stripe_modify["calls"] == []
    assert store.orders_for(user["id"]) == []


def test_rebuying_a_cancelling_plan_resumes_it_for_nothing(user, stripe_modify):
    stripe_modify["sub"] = _subscribe(user, "price_pro_m", cancel_at_period_end=True)
    result = cards.change_plan(users.get(user["id"]), plans.get("pro"), "monthly")
    assert result["action"] == "resumed" and result["amount_cents"] == 0
    _, kw = stripe_modify["calls"][-1]
    assert kw["cancel_at_period_end"] is False and "items" not in kw
    assert store.active_subscription(user["id"])["cancel_at_period_end"] == 0
    order = store.get_order(result["order_id"])
    assert order["status"] == store.PAID and order["amount_cents"] == 0


def test_webhook_racing_the_change_settles_the_same_order(user, stripe_modify, monkeypatch):
    """invoice.paid for the proration invoice can land before modify() returns.
    Whichever side gets there first, one charge is one line of history."""
    import stripe

    stripe_modify["sub"] = _subscribe(user, "price_starter_m")
    real_modify = stripe.Subscription.modify

    def modify_then_webhook(sid, **kw):
        sub = real_modify(sid, **kw)
        # The webhook arrives with the subscription already carrying the new
        # metadata, and no order row has a reference yet.
        cards.dispatch(_event("invoice.paid", {
            "id": sub["latest_invoice"]["id"], "billing_reason": "subscription_update",
            "amount_paid": 1600, "currency": "usd",
            "parent": {"subscription_details": {"subscription": sid}},
        }))
        return sub

    monkeypatch.setattr(stripe.Subscription, "modify", modify_then_webhook)
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid, **kw: stripe_modify["sub"])
    result = cards.change_plan(users.get(user["id"]), plans.get("pro"), "monthly")

    history = store.orders_for(user["id"])
    assert len(history) == 1
    assert history[0]["id"] == result["order_id"]
    assert history[0]["status"] == store.PAID and history[0]["amount_cents"] == 1600
    assert users.get(user["id"])["tier"] == "pro"


def test_a_declined_card_changes_nothing(user, stripe_modify, monkeypatch):
    import stripe

    stripe_modify["sub"] = _subscribe(user, "price_starter_m")

    def decline(sid, **kw):
        raise stripe.CardError("Your card was declined.", "card", "card_declined")

    monkeypatch.setattr(stripe.Subscription, "modify", decline)
    with pytest.raises(stripe.CardError):
        cards.change_plan(users.get(user["id"]), plans.get("pro"), "monthly")
    assert users.get(user["id"])["tier"] == "starter"
    orders = store.orders_for(user["id"])
    assert len(orders) == 1 and orders[0]["status"] == store.FAILED


def test_change_plan_needs_a_live_subscription(user):
    with pytest.raises(ValueError):
        cards.change_plan(users.get(user["id"]), plans.get("pro"), "monthly")


# ─────────────────────────── the router picks the right path ───────────────────────────

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def test_subscriber_checkout_becomes_an_in_place_change(user, client, monkeypatch):
    _subscribe(user, "price_starter_m")
    seen = {}

    def fake_change(u, plan, period):
        seen.update(user=u["id"], plan=plan.key, period=period)
        return {"action": "changed", "order_id": 42, "amount_cents": 1600, "currency": "usd"}

    monkeypatch.setattr(cards, "change_plan", fake_change)
    monkeypatch.setattr(cards, "create_checkout", lambda *a, **k: pytest.fail("Checkout must not open"))

    r = _signed_in(client, user).post(
        "/api/v1/billing/checkout", json={"plan": "pro", "period": "monthly", "provider": "stripe"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["url"] is None and body["action"] == "changed"
    assert seen == {"user": user["id"], "plan": "pro", "period": "monthly"}


def test_subscriber_cannot_stack_crypto_under_a_card_subscription(user, client):
    _subscribe(user, "price_starter_m")
    r = _signed_in(client, user).post(
        "/api/v1/billing/checkout", json={"plan": "pro", "period": "monthly", "provider": "nowpayments"}
    )
    assert r.status_code == 400
    assert "card subscription" in r.json()["detail"]
    assert store.orders_for(user["id"]) == []


def test_declined_card_on_change_is_a_402(user, client, monkeypatch):
    import stripe

    _subscribe(user, "price_starter_m")

    def decline(u, plan, period):
        raise stripe.CardError("Your card was declined.", "card", "card_declined")

    monkeypatch.setattr(cards, "change_plan", decline)
    r = _signed_in(client, user).post(
        "/api/v1/billing/checkout", json={"plan": "pro", "period": "monthly", "provider": "stripe"}
    )
    assert r.status_code == 402
    assert "declined" in r.json()["detail"]


def test_new_customer_still_goes_through_checkout(user, client, monkeypatch):
    monkeypatch.setattr(cards, "create_checkout", lambda u, plan, period, oid: f"https://checkout.test/{oid}")
    r = _signed_in(client, user).post(
        "/api/v1/billing/checkout", json={"plan": "pro", "period": "monthly", "provider": "stripe"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is False and body["url"].startswith("https://checkout.test/")
    assert store.get_order(body["order_id"])["status"] == store.PENDING

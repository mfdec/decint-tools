"""The card rail: Stripe Checkout + Billing Portal.

Checkout is hosted by Stripe, so no card number, CVC or PAN ever reaches this
server — the deployment stays in PCI SAQ-A, which is the whole reason to use a
redirect flow rather than collecting card fields ourselves.

Cards are the rail that can genuinely recur: Stripe holds the mandate and pulls
the renewal, so a subscription here is a real subscription and the entitlement
expiry simply tracks whatever period end Stripe reports.

That mandate is also what makes a plan change cheap. A subscriber who picks a
different plan is not sent through Checkout again — that would open a *second*
subscription and bill the same card twice — the subscription they already have
is moved to the new price and the difference is charged to the card on file.

Object shapes: this account defaults to an API version after the 2025-03-31
(Basil) release, where `current_period_end` lives on the subscription items and
an invoice's subscription lives under `parent.subscription_details`. Every
reader below accepts both the old and the new shape, so the handlers keep
working whether the webhook endpoint is pinned before or after that change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe

from ...config import settings
from .. import users as users_svc
from . import plans, store

log = logging.getLogger("decint.billing.stripe")

PROVIDER = "stripe"

# Subscription states that should keep the customer's access on. `past_due` is
# included on purpose: Stripe is still retrying the card, and cutting someone
# off mid-retry over a bank blip is a support ticket, not a policy.
LIVE_STATUSES = {"trialing", "active", "past_due"}
DEAD_STATUSES = {"canceled", "unpaid", "incomplete_expired"}

# Everything the webhook endpoint must be subscribed to. `admin/status` checks
# the registered endpoint against this list, so a missing event shows up in
# the console rather than as a renewal that quietly never lands.
WEBHOOK_EVENTS: tuple[str, ...] = (
    "checkout.session.completed",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
)

# Marks the Billing Portal configuration this deployment owns, so it can be
# found again on the next process start instead of created a second time.
# Bump PORTAL_REVISION whenever `_portal_params` changes shape: the stored
# configuration is only rewritten when its fingerprint no longer matches.
PORTAL_MARK = "decint-portal"
PORTAL_REVISION = "2"
_portal_config_id: str | None = None

# Stripe has no native "every 6 months" interval — it's expressed as the
# monthly interval with a count. (interval, interval_count) per period, and
# the reverse lookup used to read an inline-priced subscription back.
_STRIPE_INTERVAL: dict[str, tuple[str, int]] = {
    "monthly": ("month", 1),
    "semiannual": ("month", 6),
    "yearly": ("year", 1),
}


def _interval_for(period: str) -> tuple[str, int]:
    return _STRIPE_INTERVAL.get(period, ("month", 1))


def _period_for_interval(interval: str | None, count: int) -> str | None:
    for period, iv in _STRIPE_INTERVAL.items():
        if iv == (interval, count):
            return period
    return None


def _client() -> None:
    stripe.api_key = settings.stripe_secret_key


def available() -> bool:
    return settings.stripe_enabled


def _return_urls() -> tuple[str, str]:
    base = settings.public_base_url.rstrip("/")
    return (
        f"{base}{settings.billing_success_path}?session_id={{CHECKOUT_SESSION_ID}}",
        f"{base}{settings.billing_cancel_path}",
    )


def _customer_for(user: dict) -> str:
    """Reuse the account's Stripe customer, or create one.

    One customer per account matters for the portal: it is what lets someone
    see every past invoice in one place instead of a new customer per purchase.
    """
    existing = store.customer_ref(user["id"], PROVIDER)
    if existing:
        try:
            c = stripe.Customer.retrieve(existing)
            if not getattr(c, "deleted", False):
                return existing
        except stripe.StripeError as e:
            log.warning("stale stripe customer %s: %s", existing, e)
    c = stripe.Customer.create(
        email=user["email"],
        metadata={"decint_user_id": str(user["id"])},
    )
    store.save_customer(user["id"], PROVIDER, c.id)
    return c.id


def _line_item(plan: plans.Plan, period: str) -> dict:
    """Prefer a configured Price; fall back to pricing the plan inline.

    Inline prices charge correctly, but Stripe's customer portal can only offer
    a plan *switch* between Prices that exist in your catalogue — so a
    deployment with no STRIPE_PRICE_* set gets working checkout and a portal
    that can cancel but not upgrade.
    """
    configured = settings.stripe_price_for(plan.key, period)
    if configured:
        return {"price": configured, "quantity": 1}

    log.warning(
        "no STRIPE_PRICE_%s_%s configured — pricing %s inline; the customer "
        "portal will not be able to offer plan switching",
        plan.key.upper(), period.upper(), plan.key,
    )
    interval, interval_count = _interval_for(period)
    return {
        "quantity": 1,
        "price_data": {
            "currency": settings.billing_currency,
            "unit_amount": plan.cents(period),
            "recurring": {"interval": interval, "interval_count": interval_count},
            "product_data": {
                "name": f"DECINT {plan.name}",
                "description": plan.blurb,
            },
        },
    }


def _subscription_metadata(user_id: int, plan_key: str, period: str, order_id: int) -> dict:
    return {
        "order_id": str(order_id),
        "user_id": str(user_id),
        "plan": plan_key,
        "period": period,
    }


def create_checkout(user: dict, plan: plans.Plan, period: str, order_id: int) -> str:
    """Open a hosted Checkout session and return its URL."""
    _client()
    success_url, cancel_url = _return_urls()
    meta = _subscription_metadata(user["id"], plan.key, period, order_id)
    kwargs: dict = dict(
        mode="subscription",
        customer=_customer_for(user),
        line_items=[_line_item(plan, period)],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(order_id),
        # Both sides carry the order id: the session metadata settles this
        # order, and the subscription metadata is what every later renewal
        # webhook is resolved against, long after the session is gone.
        metadata=meta,
        subscription_data={"metadata": meta},
        allow_promotion_codes=True,
    )
    if settings.stripe_tax_enabled:
        kwargs["automatic_tax"] = {"enabled": True}
        # Stripe Tax needs an address on the customer to pick a jurisdiction,
        # and refuses the session unless it is allowed to save what Checkout
        # collects.
        kwargs["customer_update"] = {"address": "auto", "name": "auto"}

    session = stripe.checkout.Session.create(**kwargs)
    store.attach_reference(order_id, session.id)
    return session.url


# ─────────────────────────── plan changes ───────────────────────────

def live_subscription(user: dict) -> dict | None:
    """The account's current card subscription row, if it has one."""
    sub = store.active_subscription(user["id"])
    return sub if sub and sub["provider"] == PROVIDER else None


def cancel_now(user_id: int) -> str | None:
    """End the account's card subscription today, for an account being deleted.

    Immediate rather than at period end: there will be no account left for
    the rest of the period to belong to, and a mandate outliving its account
    would keep charging someone with no way to sign in and stop it. Returns
    the cancelled subscription id, or None when there was nothing to cancel.
    A Stripe error propagates, so the caller can refuse to delete an account
    whose card is still being pulled.
    """
    row = store.active_subscription(user_id)
    if not row or row["provider"] != PROVIDER:
        return None
    _client()
    stripe.Subscription.cancel(row["subscription_ref"])
    log.info("cancelled subscription %s for deleted account %s", row["subscription_ref"], user_id)
    return row["subscription_ref"]


def change_plan(user: dict, plan: plans.Plan, period: str) -> dict:
    """Move the account's live subscription to another plan or period.

    Stripe already holds this customer's mandate, so the change is applied to
    the subscription they have and settled against the card on file straight
    away: an upgrade is charged pro rata the moment the card clears, a
    downgrade is credited against the next renewal. `error_if_incomplete`
    keeps that transactional — if the card declines, Stripe refuses the update
    and nothing about the plan changes, which is far easier to explain than a
    plan that changed and a payment that did not.

    Switching interval (monthly / semiannual / yearly) restarts the billing
    cycle today so the new period is a clean one rather than, say, a year
    anchored to some earlier date; a same-interval switch keeps the renewal
    date the customer already knows.

    Returns what the router reports back. Raises ValueError for anything the
    customer can act on (already on this plan, no price configured) and lets
    stripe.CardError through so the router can say "declined" specifically.
    """
    _client()
    row = live_subscription(user)
    if not row:
        raise ValueError("This account has no card subscription to change.")

    price_id = settings.stripe_price_for(plan.key, period)
    if not price_id:
        raise ValueError(
            f"Switching to {plan.name} ({period}) is not available — ask the operator."
        )

    sub = stripe.Subscription.retrieve(row["subscription_ref"])
    items = list(_get(_get(sub, "items") or {}, "data") or [])
    if len(items) != 1:
        raise ValueError("This subscription cannot be changed here — ask the operator.")
    item = items[0]
    current_price = _get(_get(item, "price") or {}, "id") or ""
    current_recurring = _get(_get(item, "price") or {}, "recurring") or {}
    current_interval = _get(current_recurring, "interval")
    current_interval_count = _get(current_recurring, "interval_count") or 1

    same_price = current_price == price_id
    if same_price and not _get(sub, "cancel_at_period_end"):
        raise ValueError(f"You are already on {plan.name} ({period}).")

    # Every path below writes an order row first, the same as checkout does:
    # the intent is on our side before the processor is asked to do anything.
    order = store.create_order(user["id"], PROVIDER, plan.key, period)
    meta = _subscription_metadata(user["id"], plan.key, period, order["id"])
    sub_id = _get(sub, "id")

    if same_price:
        # Picking the plan you are already leaving means "keep it". Nothing is
        # charged now — the renewal that was going to be skipped simply isn't.
        sub = stripe.Subscription.modify(sub_id, cancel_at_period_end=False, metadata=meta)
        _apply_subscription(sub)
        store.set_order_amount(order["id"], 0)
        store.set_order_status(order["id"], store.PAID, detail="subscription resumed")
        return {"action": "resumed", "order_id": order["id"], "amount_cents": 0}

    kwargs: dict = dict(
        items=[{"id": _get(item, "id"), "price": price_id}],
        proration_behavior="always_invoice",
        payment_behavior="error_if_incomplete",
        cancel_at_period_end=False,
        metadata=meta,
        expand=["latest_invoice"],
    )
    new_interval, new_interval_count = _interval_for(period)
    if current_interval and (current_interval, current_interval_count) != (
        new_interval, new_interval_count,
    ):
        kwargs["billing_cycle_anchor"] = "now"

    try:
        sub = stripe.Subscription.modify(sub_id, **kwargs)
    except stripe.StripeError as e:
        store.set_order_status(order["id"], store.FAILED, detail=f"{type(e).__name__}: {e}")
        raise

    # Land the change now rather than waiting for the webhook — the customer
    # is looking at the page. The webhook that follows is idempotent.
    _apply_subscription(sub)

    invoice = _get(sub, "latest_invoice")
    inv_id = _id_of(invoice)
    charged = int(_get(invoice, "amount_paid") or 0) if not isinstance(invoice, str) else 0
    currency = (_get(invoice, "currency") if not isinstance(invoice, str) else None) or ""

    # Stripe fires invoice.paid for the proration invoice while modify() is
    # still returning, so the webhook may already have settled this order by
    # its metadata order_id — or, if it could not, written a row of its own.
    # Either way there must be exactly one row for one charge.
    settled = store.order_by_reference(PROVIDER, inv_id) if inv_id else None
    if settled and settled["id"] != order["id"]:
        store.discard_order(order["id"])
        order = settled
    else:
        if inv_id:
            store.attach_reference(order["id"], inv_id)
        store.set_order_amount(order["id"], charged, currency)
        store.set_order_status(
            order["id"], store.PAID,
            detail=f"plan change via card on file; invoice {inv_id or 'n/a'}",
        )
    return {
        "action": "changed",
        "order_id": order["id"],
        "amount_cents": charged,
        "currency": currency or settings.billing_currency,
    }


# ─────────────────────────── billing portal ───────────────────────────

def _price_catalogue() -> list[str]:
    return sorted(
        pid
        for p in plans.PLANS if p.purchasable()
        for period in plans.PERIODS
        if (pid := settings.stripe_price_for(p.key, period))
    )


def _portal_products() -> list[dict]:
    """The plan-switch menu: every configured Price, grouped under its Product.

    Stripe wants the list shaped product-first. A retrieval failure on any
    Price degrades to "no switching" rather than failing the portal outright —
    a portal that can only cancel is still a portal.
    """
    by_product: dict[str, list[str]] = {}
    for pid in _price_catalogue():
        try:
            price = stripe.Price.retrieve(pid)
        except stripe.StripeError as e:
            log.warning("portal: cannot read price %s: %s", pid, e)
            return []
        product = price.product if isinstance(price.product, str) else price.product.id
        by_product.setdefault(product, []).append(pid)
    return [
        {
            "product": prod,
            "prices": prices,
            # A tier is per account. Stripe's default lets the customer set a
            # quantity of two and be charged twice for an entitlement that
            # does not know what a quantity is.
            "adjustable_quantity": {"enabled": False},
        }
        for prod, prices in by_product.items()
    ]


def _portal_params() -> dict:
    base = settings.public_base_url.rstrip("/")
    features: dict = {
        "invoice_history": {"enabled": True},
        "payment_method_update": {"enabled": True},
        "customer_update": {
            "enabled": True,
            "allowed_updates": ["email", "name", "address"],
        },
        "subscription_cancel": {
            "enabled": True,
            # Access runs to the end of what was paid for; nothing is clawed
            # back and nothing is refunded pro rata.
            "mode": "at_period_end",
            "proration_behavior": "none",
            "cancellation_reason": {
                "enabled": True,
                "options": [
                    "too_expensive", "missing_features", "switched_service",
                    "unused", "customer_service", "too_complex", "low_quality",
                    "other",
                ],
            },
        },
    }
    products = _portal_products()
    if products:
        features["subscription_update"] = {
            "enabled": True,
            "default_allowed_updates": ["price"],
            "proration_behavior": "always_invoice",
            "products": products,
        }
    return {
        "business_profile": {"headline": "DECINT — manage your subscription"},
        "default_return_url": f"{base}/billing",
        "features": features,
        "metadata": {
            "decint": PORTAL_MARK,
            "revision": PORTAL_REVISION,
            "prices": ",".join(_price_catalogue()),
        },
    }


def ensure_portal_configuration() -> str:
    """Find or create the Billing Portal configuration this deployment uses.

    Stripe will not open a portal session without a configuration — either
    the account's default, which only exists once someone has pressed Save on
    the portal settings page in the Dashboard, or one passed by id. Relying on
    that Dashboard click is the kind of step that gets missed, so the
    configuration is created here and passed explicitly. Re-run when the
    Price ids change so the plan-switch menu tracks the catalogue.
    """
    global _portal_config_id
    _client()
    if _portal_config_id:
        return _portal_config_id

    params = _portal_params()
    want = {k: params["metadata"][k] for k in ("revision", "prices")}
    found = None
    for cfg in stripe.billing_portal.Configuration.list(active=True, limit=100).auto_paging_iter():
        if (cfg.metadata or {}).get("decint") == PORTAL_MARK:
            found = cfg
            break

    if found is None:
        cfg = stripe.billing_portal.Configuration.create(**params)
        log.info("created billing portal configuration %s", cfg.id)
    elif {k: (found.metadata or {}).get(k) for k in want} != want:
        cfg = stripe.billing_portal.Configuration.modify(found.id, **params)
        log.info("updated billing portal configuration %s (revision %s)", cfg.id, PORTAL_REVISION)
    else:
        cfg = found
    _portal_config_id = cfg.id
    return cfg.id


def portal_url(user: dict) -> str:
    """Stripe's own self-serve screen: change card, switch plan, cancel,
    download invoices. Building that ourselves would mean re-implementing a
    compliance surface Stripe already maintains."""
    _client()
    ref = store.customer_ref(user["id"], PROVIDER)
    if not ref:
        raise ValueError("This account has never paid by card.")
    base = settings.public_base_url.rstrip("/")
    kwargs: dict = dict(customer=ref, return_url=f"{base}/billing")
    try:
        kwargs["configuration"] = ensure_portal_configuration()
    except stripe.StripeError as e:
        # Fall back to the account default rather than refusing outright; if
        # that does not exist either, Stripe's error says exactly what to do.
        log.warning("portal configuration unavailable, using account default: %s", e)
    session = stripe.billing_portal.Session.create(**kwargs)
    return session.url


# ─────────────────────────── admin status ───────────────────────────

def webhook_status() -> dict:
    """Is the endpoint registered, and does it get every event we handle?"""
    _client()
    url = f"{settings.public_base_url.rstrip('/')}/api/v1/billing/webhook/stripe"
    try:
        endpoints = [w for w in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter() if w.url == url]
    except stripe.StripeError as e:
        return {"registered": None, "detail": str(e)[:200]}
    if not endpoints:
        return {"registered": False, "missing_events": list(WEBHOOK_EVENTS)}
    ep = endpoints[0]
    enabled = set(ep.enabled_events or [])
    missing = [] if "*" in enabled else [e for e in WEBHOOK_EVENTS if e not in enabled]
    return {
        "registered": True,
        "status": ep.status,
        "api_version": ep.api_version,
        "missing_events": missing,
    }


def portal_status() -> dict:
    _client()
    try:
        cfg_id = ensure_portal_configuration()
        cfg = stripe.billing_portal.Configuration.retrieve(cfg_id)
        return {
            "configured": True,
            "id": cfg_id,
            "plan_switching": bool(cfg.features.subscription_update.enabled),
        }
    except stripe.StripeError as e:
        return {"configured": False, "detail": str(e)[:200]}


# ─────────────────────────── webhook ───────────────────────────

def _get(obj, key, default=None):
    """Stripe objects are dict-like, but plain dicts arrive in tests."""
    if obj is None:
        return default
    try:
        return obj.get(key, default)
    except AttributeError:  # pragma: no cover
        return getattr(obj, key, default)


def _id_of(ref) -> str | None:
    """An expandable field is either an id or the expanded object."""
    if isinstance(ref, str):
        return ref
    return _get(ref, "id") if ref else None


def _period_end(sub) -> int | None:
    """Read the renewal date, whichever shape the pinned API version uses.

    Stripe moved `current_period_end` off the subscription and onto its items
    in the 2025-03-31 (Basil) release, and it is item-only on the version this
    client defaults to. Reading both keeps this correct whether the account is
    pinned before or after that change.
    """
    direct = _get(sub, "current_period_end")
    if direct:
        return int(direct)
    items = _get(_get(sub, "items") or {}, "data") or []
    ends = [_get(i, "current_period_end") for i in items]
    ends = [int(e) for e in ends if e]
    return max(ends) if ends else None


def _invoice_subscription(invoice) -> str | None:
    """The subscription an invoice belongs to, on either side of Basil.

    Before 2025-03-31 it was `invoice.subscription`; since then it is
    `invoice.parent.subscription_details.subscription`. This account's
    default version is after that change, so without the second branch every
    renewal webhook would resolve to no subscription and silently do nothing.
    """
    direct = _id_of(_get(invoice, "subscription"))
    if direct:
        return direct
    details = _get(_get(invoice, "parent") or {}, "subscription_details") or {}
    return _id_of(_get(details, "subscription"))


def _iso_from_unix(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec="seconds")


def _resolve_user(sub) -> int | None:
    """Whose subscription is this? Metadata first, then the customer mapping."""
    meta = _get(sub, "metadata") or {}
    if meta.get("user_id"):
        try:
            return int(meta["user_id"])
        except (TypeError, ValueError):
            pass
    customer = _id_of(_get(sub, "customer"))
    return store.user_for_customer(PROVIDER, customer) if customer else None


def _plan_for_price(price_id: str) -> tuple[str, str] | None:
    """(plan key, period) for a configured Price id."""
    if not price_id:
        return None
    for plan in plans.PLANS:
        for period in plans.PERIODS:
            if settings.stripe_price_for(plan.key, period) == price_id:
                return plan.key, period
    return None


def _items(sub) -> list:
    return list(_get(_get(sub, "items") or {}, "data") or [])


def _resolve_plan(sub) -> str | None:
    """The plan key: from the Price being charged first, metadata second.

    The Price is what Stripe is actually billing; the metadata is what we
    wrote at checkout. A plan switch in the customer portal changes the
    former without touching the latter, so trusting metadata first would keep
    granting the old plan after an upgrade — or, worse, after a downgrade.
    Never from the amount: a promotion code changes the amount.
    """
    for item in _items(sub):
        hit = _plan_for_price(_get(_get(item, "price") or {}, "id") or "")
        if hit:
            return hit[0]
    meta = _get(sub, "metadata") or {}
    if meta.get("plan") and plans.get(meta["plan"]):
        return meta["plan"]
    return None


def _resolve_period(sub) -> str:
    """monthly | semiannual | yearly, from the Price id, else its interval
    (interval + interval_count, since Stripe has no native "6 months"), else
    metadata."""
    for item in _items(sub):
        price = _get(item, "price") or {}
        hit = _plan_for_price(_get(price, "id") or "")
        if hit:
            return hit[1]
        recurring = _get(price, "recurring") or {}
        interval = _get(recurring, "interval")
        count = _get(recurring, "interval_count") or 1
        by_interval = _period_for_interval(interval, count)
        if by_interval:
            return by_interval
    meta = _get(sub, "metadata") or {}
    return meta.get("period") if meta.get("period") in plans.PERIOD_MONTHS else "monthly"


def _apply_subscription(sub) -> None:
    """Bring the entitlement in line with one subscription object."""
    sub_id = _get(sub, "id")
    user_id = _resolve_user(sub)
    if not user_id:
        log.warning("stripe subscription %s maps to no account", sub_id)
        return
    if not users_svc.get(user_id):
        # The account was deleted (cancel_now ran first); the webhook that
        # follows the cancellation must not write rows back for it.
        log.info("stripe subscription %s belongs to deleted account %s", sub_id, user_id)
        return
    plan_key = _resolve_plan(sub)
    if not plan_key:
        log.warning("stripe subscription %s maps to no plan", sub_id)
        return

    status = _get(sub, "status") or ""
    period_end = _iso_from_unix(_period_end(sub))
    store.upsert_subscription(
        user_id, PROVIDER, sub_id, plan_key, status, period_end,
        bool(_get(sub, "cancel_at_period_end")),
        period=_resolve_period(sub),
    )

    if status in LIVE_STATUSES:
        store.grant(
            user_id, plan_key, PROVIDER,
            expires_at=period_end,
            reason=f"subscription {status}",
        )
    elif status in DEAD_STATUSES:
        store.revoke(user_id, PROVIDER, reason=f"subscription {status}")


def _pending_change_order(sub, user_id: int) -> dict | None:
    meta = _get(sub, "metadata") or {}
    try:
        order = store.get_order(int(meta.get("order_id") or 0))
    except (TypeError, ValueError):
        return None
    if (
        order
        and order["user_id"] == user_id
        and order["provider"] == PROVIDER
        and order["status"] == store.PENDING
        and not order["reference"]
    ):
        return order
    return None


def _record_invoice(invoice, sub) -> None:
    """Put a settled invoice in the customer's payment history.

    The first invoice is already covered by the checkout order, and an
    in-place plan change attaches its invoice to the order it wrote. What is
    left is the renewal Stripe pulled on its own — the one charge the customer
    never clicked a button for, and therefore the one they most need to be
    able to see.
    """
    inv_id = _get(invoice, "id")
    if not inv_id:
        return
    order = store.order_by_reference(PROVIDER, inv_id)
    if order:
        if order["status"] != store.PAID:
            store.set_order_status(order["id"], store.PAID, detail="invoice paid")
        return
    reason = _get(invoice, "billing_reason") or ""
    if reason == "subscription_create":
        return
    user_id = _resolve_user(sub)
    plan_key = _resolve_plan(sub)
    if not user_id or not plan_key:
        return
    # A plan change we started: its order row is waiting on this very invoice,
    # named in the metadata written alongside the change. Settle that row
    # rather than adding a second one for the same charge.
    pending = _pending_change_order(sub, user_id)
    if pending:
        store.attach_reference(pending["id"], inv_id)
        store.set_order_amount(
            pending["id"], int(_get(invoice, "amount_paid") or 0),
            _get(invoice, "currency") or "",
        )
        store.set_order_status(pending["id"], store.PAID, detail=f"invoice {reason or 'paid'}")
        return
    store.record_paid_order(
        user_id, PROVIDER, plan_key, _resolve_period(sub),
        amount_cents=int(_get(invoice, "amount_paid") or 0),
        currency=_get(invoice, "currency") or settings.billing_currency,
        reference=inv_id,
        detail=f"invoice {reason or 'paid'}",
    )


def handle_webhook(payload: bytes, signature: str) -> dict:
    """Verify and apply one Stripe event.

    The signature check has no fallback path on purpose: an unverified billing
    webhook is an anonymous endpoint for granting yourself a paid plan.
    """
    _client()
    if not settings.stripe_webhook_secret:
        raise PermissionError("STRIPE_WEBHOOK_SECRET is not configured.")
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise PermissionError(f"Invalid Stripe signature: {e}") from e
    return dispatch(event)


def dispatch(event) -> dict:
    """Apply one already-verified event. Split from `handle_webhook` so the
    handlers can be exercised with plain dicts and no signing key."""
    kind = event["type"]
    obj = event["data"]["object"]

    if store.seen_event(PROVIDER, event["id"], kind):
        return {"handled": False, "reason": "duplicate", "type": kind}

    if kind == "checkout.session.completed":
        order = store.order_by_reference(PROVIDER, _get(obj, "id") or "")
        if order:
            store.set_order_status(order["id"], store.PAID, detail=kind)
        else:
            log.warning("checkout %s settled with no matching order row", _get(obj, "id"))
        # The session says money moved; the subscription says until when.
        # Always read the subscription rather than assuming a month.
        sub_id = _id_of(_get(obj, "subscription"))
        if sub_id:
            _apply_subscription(stripe.Subscription.retrieve(sub_id))

    elif kind == "checkout.session.expired":
        # The customer never finished. Closing the row keeps the open-order
        # cap honest; nothing about access is involved.
        order = store.order_by_reference(PROVIDER, _get(obj, "id") or "")
        if order and order["status"] == store.PENDING:
            store.set_order_status(order["id"], store.EXPIRED, detail=kind)

    elif kind in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _apply_subscription(obj)

    elif kind == "invoice.paid":
        # A renewal, or the settlement of a plan change. Re-read the
        # subscription so the new period end lands, then put the charge in
        # the customer's history.
        sub_id = _invoice_subscription(obj)
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            _apply_subscription(sub)
            _record_invoice(obj, sub)
        else:
            log.info("invoice %s paid with no subscription attached", _get(obj, "id"))

    elif kind == "invoice.payment_failed":
        # Deliberately no downgrade here. Stripe keeps retrying and will move
        # the subscription to past_due, then unpaid/canceled — and those events
        # are what actually change access, once the grace window has run out.
        log.info("invoice payment failed for customer %s", _id_of(_get(obj, "customer")))

    else:
        return {"handled": False, "reason": "ignored", "type": kind}

    return {"handled": True, "type": kind}

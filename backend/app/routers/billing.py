"""Billing: the plan catalogue, checkout on either rail, and the webhooks.

Three payment rails share one entitlement model:

  * **Stripe** — cards, wallets and the local methods Checkout turns on. A real
    subscription: Stripe holds the mandate and pulls the renewal.
  * **NOWPayments** — BTC and ~300 other assets. A prepaid period, because no
    chain lets a merchant pull a renewal. The UI says so rather than implying a
    subscription it cannot deliver.
  * **Google Play** — subscriptions bought inside the Android app, where Play's
    policy allows no other way to pay. The app reports each purchase and this
    server checks it with Google before granting anything.

The two webhook routes are the only unauthenticated writes in this file, and
both refuse anything they cannot cryptographically attribute to the processor.
They are also deliberately forgiving about *what* they return: a processor that
gets a 5xx retries, so we answer 200 to anything we have durably recorded, and
non-2xx only when we want the retry.
"""

from __future__ import annotations

import asyncio
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_admin, require_session
from ..config import settings
from ..services import users
from ..services.analytics import client_ip
from ..services.billing import nowpayments_provider as crypto
from ..services.billing import play_provider as play
from ..services.billing import plans, store
from ..services.billing import stripe_provider as cards

log = logging.getLogger("decint.billing")

router = APIRouter(prefix="/billing", tags=["billing"])

# A customer with this many unpaid orders open is either confused or probing.
# Cheap guard: every checkout call costs us an API round trip to a processor.
MAX_OPEN_ORDERS = 10

# Revision of the Data Access Purchase Agreement the checkout box points at
# (frontend/app/(marketing)/terms). Bump it whenever the wording changes; it is
# written to the audit log with every acceptance so a dispute can say which
# text the customer agreed to.
TERMS_VERSION = "2026-10-05"


class CheckoutRequest(BaseModel):
    plan: str
    period: str = "monthly"
    provider: str = "stripe"
    # Crypto only. Empty lets the customer pick on the gateway's own page.
    pay_currency: str = ""
    # The tick-box on the pricing page. Enforced here rather than only in the
    # UI: a checkbox the API does not check is one a script walks straight past.
    accept_terms: bool = False


class PlayVerifyRequest(BaseModel):
    purchase_token: str


class GrantRequest(BaseModel):
    """Admin: comp an account, or record a payment taken outside the system."""
    user_id: int
    tier: str
    months: int | None = None  # None = no expiry
    reason: str = ""


# ─────────────────────────── public catalogue ───────────────────────────

@router.get("/config")
async def billing_config() -> dict:
    """What the pricing page needs to render itself. Public by design — the
    catalogue is published, and the frontend keeping its own copy is how a
    pricing page ends up disagreeing with the charge."""
    return {
        "enabled": settings.billing_enabled,
        "providers": settings.billing_providers,
        "card_enabled": cards.available(),
        "crypto_enabled": crypto.available(),
        "currency": settings.billing_currency,
        "periods": list(plans.PERIODS),
        "plans": plans.catalogue(),
        "free_tier": plans.FREE_PLAN.key,
        # In-app subscriptions (Android). Not in `providers`: the website
        # cannot sell through Play, only the app can.
        "play_enabled": play.available(),
    }


@router.get("/crypto/currencies")
async def crypto_currencies() -> dict:
    if not crypto.available():
        return {"currencies": []}
    coins = await asyncio.to_thread(crypto.currencies)
    return {"currencies": coins}


# ─────────────────────────── the account's own billing ───────────────────────────

@router.get("/me")
async def my_billing(user: dict = Depends(require_session)) -> dict:
    if user.get("break_glass"):
        raise HTTPException(400, "The bootstrap operator has no billing account.")
    return store.summary(user)


@router.post("/checkout")
async def checkout(
    body: CheckoutRequest, request: Request, user: dict = Depends(require_session)
) -> dict:
    if not settings.billing_enabled:
        raise HTTPException(503, "Billing is not enabled on this deployment.")
    if user.get("break_glass"):
        raise HTTPException(400, "Create a real account before buying a plan.")
    if not body.accept_terms:
        raise HTTPException(
            400,
            "Tick the box to accept the Data Access Purchase Agreement before buying.",
        )

    try:
        plan = plans.require_purchasable(body.plan, body.period)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    provider = body.provider.lower()
    # A Play subscription is billed and renewed by Google. A website purchase
    # on top of it would charge the same person twice for one account.
    if play.live_subscription(user):
        raise HTTPException(
            400,
            "Your plan is billed through Google Play. Change or cancel it in the "
            "DECINT app or the Play Store.",
        )
    if provider not in ("stripe", "nowpayments"):
        raise HTTPException(400, "Unknown payment provider.")
    if provider == "stripe" and not cards.available():
        raise HTTPException(503, "Card payments are not configured on this deployment.")
    if provider == "nowpayments" and not crypto.available():
        raise HTTPException(503, "Crypto payments are not configured on this deployment.")

    open_orders = [
        o for o in store.orders_for(user["id"], limit=50) if o["status"] == store.PENDING
    ]
    if len(open_orders) >= MAX_OPEN_ORDERS:
        raise HTTPException(
            429, "You have too many unfinished checkouts. Complete or abandon one first."
        )

    ip = client_ip(request)

    # A card subscriber picking another plan is changed in place, against the
    # card Stripe already holds. Sending them through Checkout again would
    # open a second subscription and bill them twice; sending them to the
    # crypto rail would stack a prepaid period under a subscription that keeps
    # renewing over it. Neither is a purchase anyone meant to make.
    current = cards.live_subscription(user)
    if current and provider == "nowpayments":
        raise HTTPException(
            400,
            "You have a card subscription that renews itself. Cancel it under "
            "Manage card & invoices before buying a prepaid crypto period.",
        )
    if current and provider == "stripe":
        try:
            result = await asyncio.to_thread(cards.change_plan, user, plan, body.period)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except stripe.CardError as e:
            raise HTTPException(
                402, f"Your card was declined ({e.user_message or 'no reason given'}). "
                     "Update it under Manage card & invoices and try again.",
            ) from e
        except stripe.StripeError as e:
            log.exception("plan change failed for user %s", user["id"])
            raise HTTPException(
                502, "The payment provider could not apply the change. Try again shortly."
            ) from e
        users.audit(
            "billing.plan_change", actor=user, target=plan.key,
            detail=f"stripe {body.period} {result['action']} order={result['order_id']} "
                   f"terms={TERMS_VERSION}",
            ip=ip,
        )
        return {
            "url": None,
            "changed": True,
            "action": result["action"],
            "order_id": result["order_id"],
            "provider": provider,
            "plan": plan.key,
            "period": body.period,
            "amount_cents": result["amount_cents"],
            "currency": result.get("currency", settings.billing_currency),
            "recurring": True,
        }

    order = store.create_order(user["id"], provider, plan.key, body.period)

    try:
        if provider == "stripe":
            url = await asyncio.to_thread(
                cards.create_checkout, user, plan, body.period, order["id"]
            )
        else:
            url = await asyncio.to_thread(
                crypto.create_checkout, user, plan, body.period, order["id"],
                body.pay_currency,
            )
    except crypto.BelowMinimum as e:
        store.set_order_status(
            order["id"], store.FAILED, detail=f"{type(e).__name__}: {e}"
        )
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        store.set_order_status(
            order["id"], store.FAILED, detail=f"{type(e).__name__}: {e}"
        )
        log.exception("checkout failed for user %s on %s", user["id"], provider)
        raise HTTPException(
            502, "The payment provider could not start a checkout. Try again shortly."
        ) from e

    users.audit(
        "billing.checkout", actor=user, target=plan.key,
        detail=f"{provider} {body.period} order={order['id']} terms={TERMS_VERSION}",
        ip=ip,
    )
    return {
        "url": url,
        "changed": False,
        "order_id": order["id"],
        "provider": provider,
        "plan": plan.key,
        "period": body.period,
        "amount_cents": plan.cents(body.period),
        "currency": settings.billing_currency,
        # The one thing the crypto flow must not leave ambiguous.
        "recurring": provider == "stripe",
    }


@router.post("/portal")
async def portal(user: dict = Depends(require_session)) -> dict:
    """Hand the customer to Stripe's own billing screen."""
    if not cards.available():
        raise HTTPException(503, "Card payments are not configured on this deployment.")
    try:
        url = await asyncio.to_thread(cards.portal_url, user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        log.exception("portal failed for user %s", user["id"])
        raise HTTPException(502, "Could not open the billing portal.") from e
    return {"url": url}


@router.get("/orders")
async def my_orders(user: dict = Depends(require_session)) -> dict:
    return {"orders": store.orders_for(user["id"])}


# ─────────────────────────── Google Play (the Android app) ───────────────────────────

@router.get("/play/account")
async def play_account(user: dict = Depends(require_session)) -> dict:
    """What the app needs before opening Google's purchase sheet: the account
    reference to stamp on the purchase, and whatever the account already has."""
    if not play.available():
        raise HTTPException(503, "In-app subscriptions are not configured on this deployment.")
    if user.get("break_glass"):
        raise HTTPException(400, "Create a real account before buying a plan.")
    ref = play.link_account(user)
    current = play.live_subscription(user)
    paid = (user.get("tier") or plans.FREE_PLAN.key) != plans.FREE_PLAN.key
    ent = store.entitlement(user["id"]) or {}
    return {
        "account_ref": ref,
        "package_name": settings.play_package_name,
        # Switching plans in the app replaces this purchase rather than adding one.
        "current": (
            {
                "product_id": current["plan"],
                "base_plan_id": current.get("period") or "monthly",
                "purchase_token": current["subscription_ref"],
            }
            if current
            else None
        ),
        # A plan paid some other way (card, crypto, or granted by an operator):
        # the app must not sell a second one on top of it.
        "billed_elsewhere": paid and not current and ent.get("source") != play.PROVIDER,
    }


@router.post("/play/verify")
async def play_verify(
    body: PlayVerifyRequest, request: Request, user: dict = Depends(require_session)
) -> dict:
    """The app reports a purchase it just completed. Nothing is granted on its
    word: the token is looked up with Google first."""
    if not play.available():
        raise HTTPException(503, "In-app subscriptions are not configured on this deployment.")
    token = body.purchase_token.strip()
    if not token or len(token) > 4096:
        raise HTTPException(400, "Missing purchase token.")
    play.link_account(user)
    try:
        result = await asyncio.to_thread(play.apply, token, expected_user_id=user["id"])
    except PermissionError as e:
        users.audit("billing.play_mismatch", actor=user, ip=client_ip(request))
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except play.PlayError as e:
        log.warning("play verify failed for user %s: %s", user["id"], e)
        raise HTTPException(
            502, "Google Play could not confirm the purchase yet. It will be applied "
                 "automatically within a few minutes.",
        ) from e
    users.audit(
        "billing.play_purchase", actor=user, target=result.get("plan", ""),
        detail=f"{result.get('period', '')} {result.get('state', '')}", ip=client_ip(request),
    )
    return {"ok": True, "summary": store.summary(users.get(user["id"]))}


# ─────────────────────────── webhooks ───────────────────────────
# No auth dependency, by necessity: the caller is a processor, not a session.
# Authenticity comes from the signature over the raw body, which is why both
# handlers take `await request.body()` and never a parsed model — re-encoding
# the body would invalidate the very thing being checked.

@router.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> dict:
    raw = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        return await asyncio.to_thread(cards.handle_webhook, raw, signature)
    except PermissionError as e:
        log.warning("rejected Stripe webhook from %s: %s", client_ip(request), e)
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        # A 500 here asks Stripe to retry, which is what we want for a
        # transient fault — the handler is idempotent, so a retry is safe.
        log.exception("Stripe webhook processing failed")
        raise HTTPException(500, "Webhook processing failed.") from e


@router.post("/webhook/play")
async def play_webhook(request: Request, token: str = "") -> dict:
    """Real-time developer notifications, pushed by Cloud Pub/Sub. A 2xx
    acknowledges the message; anything else makes Pub/Sub redeliver it."""
    raw = await request.body()
    try:
        return await asyncio.to_thread(play.handle_rtdn, raw, token)
    except PermissionError as e:
        log.warning("rejected Play notification from %s: %s", client_ip(request), e)
        raise HTTPException(403, "Forbidden.") from e
    except ValueError as e:
        # A product this deployment doesn't sell. Redelivering won't change that.
        log.warning("ignored Play notification: %s", e)
        return {"ok": True, "ignored": str(e)}
    except Exception as e:
        log.exception("Play notification processing failed")
        raise HTTPException(500, "Webhook processing failed.") from e


@router.post("/webhook/nowpayments")
async def nowpayments_webhook(request: Request) -> dict:
    raw = await request.body()
    signature = request.headers.get("x-nowpayments-sig", "")
    try:
        return await asyncio.to_thread(crypto.handle_webhook, raw, signature)
    except PermissionError as e:
        log.warning("rejected NOWPayments IPN from %s: %s", client_ip(request), e)
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        log.exception("NOWPayments IPN processing failed")
        raise HTTPException(500, "Webhook processing failed.") from e


# ─────────────────────────── admin ───────────────────────────

@router.post("/admin/grant")
async def admin_grant(
    body: GrantRequest, request: Request, admin: dict = Depends(require_admin)
) -> dict:
    """Set an account's tier by hand — a comp, a wire transfer, an invoice paid
    off-platform. Audited like every other admin action on an account."""
    target = users.get(body.user_id)
    if not target:
        raise HTTPException(404, "No such account.")
    if body.tier not in users.TIERS:
        raise HTTPException(400, f"tier must be one of {users.TIERS}")

    try:
        if body.months:
            ent = store.extend(
                body.user_id, body.tier, "manual", int(body.months),
                reason=body.reason or f"granted by {admin.get('email')}",
            )
        else:
            ent = store.grant(
                body.user_id, body.tier, "manual", expires_at=None,
                reason=body.reason or f"granted by {admin.get('email')}",
            )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    users.audit(
        "billing.admin_grant", actor=admin, target=target["email"],
        detail=f"tier={body.tier} months={body.months or 'never expires'}",
        ip=client_ip(request),
    )
    return {"entitlement": ent}


@router.post("/admin/usage/reset")
async def admin_usage_reset(
    body: GrantRequest, request: Request, admin: dict = Depends(require_admin)
) -> dict:
    """Wipe an account's search counters — a support case, or a trial someone
    burned through testing. Only `user_id` is read from the body."""
    from ..services import usage

    target = users.get(body.user_id)
    if not target:
        raise HTTPException(404, "No such account.")
    usage.reset(body.user_id)
    users.audit(
        "usage.reset", actor=admin, target=target["email"],
        detail=body.reason or "", ip=client_ip(request),
    )
    return {"usage": usage.status(target)}


@router.post("/admin/sweep")
async def admin_sweep(admin: dict = Depends(require_admin)) -> dict:
    """Force the lapse sweep now instead of waiting for the next request."""
    return {"lapsed": await asyncio.to_thread(store.sweep)}


@router.get("/admin/status")
async def admin_status(admin: dict = Depends(require_admin)) -> dict:
    """Is each rail actually wired up? Answers the "why is there no crypto
    button" question without reading the .env over SSH."""
    crypto_ok, crypto_detail = (
        await asyncio.to_thread(crypto.status) if crypto.available()
        else (False, "not configured")
    )
    webhook, portal = (
        (await asyncio.to_thread(cards.webhook_status),
         await asyncio.to_thread(cards.portal_status))
        if cards.available()
        else ({"registered": None}, {"configured": False})
    )
    return {
        "billing_enabled": settings.billing_enabled,
        "public_base_url": settings.public_base_url,
        "stripe": {
            "configured": cards.available(),
            "has_secret_key": bool(settings.stripe_secret_key),
            "has_webhook_secret": bool(settings.stripe_webhook_secret),
            "tax_enabled": settings.stripe_tax_enabled,
            "prices": {
                f"{p.key}_{period}": bool(settings.stripe_price_for(p.key, period))
                for p in plans.PLANS if p.purchasable()
                for period in plans.PERIODS
            },
            "webhook_url": f"{settings.public_base_url.rstrip('/')}"
                           f"/api/v1/billing/webhook/stripe",
            "webhook_events": list(cards.WEBHOOK_EVENTS),
            # Live checks against the account: is the endpoint registered
            # with every event we handle, and can the portal open at all?
            "webhook": webhook,
            "portal": portal,
        },
        "nowpayments": {
            "configured": crypto.available(),
            "has_api_key": bool(settings.nowpayments_api_key),
            "has_ipn_secret": bool(settings.nowpayments_ipn_secret),
            "reachable": crypto_ok,
            "detail": crypto_detail,
            "currencies": settings.nowpayments_currency_list,
            "ipn_url": f"{settings.public_base_url.rstrip('/')}"
                       f"/api/v1/billing/webhook/nowpayments",
        },
        "google_play": {
            "configured": play.available(),
            "package_name": settings.play_package_name,
            "has_service_account": bool(settings.play_service_account_file),
            "has_rtdn_token": bool(settings.play_rtdn_token),
            # Register this (with ?token=PLAY_RTDN_TOKEN) as the Pub/Sub push endpoint.
            "rtdn_url": f"{settings.public_base_url.rstrip('/')}/api/v1/billing/webhook/play",
            "products": {
                p.key: list(plans.PERIODS) for p in plans.PLANS if p.purchasable()
            },
        },
    }

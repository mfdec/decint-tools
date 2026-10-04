"""The Google Play rail: subscriptions bought inside the Android app.

Play's payments policy requires its own billing for anything sold in an app,
so the app sells the same plans through Google Play Billing. Google takes the
payment and holds the mandate; this module only ever *reads* what Google says
a purchase is, and turns that into the same entitlement the other rails write.

Mapping, fixed by convention so there is nothing to keep in sync:
  Play product id  = plan key    ("starter", "pro")
  Play base plan id = period key ("monthly", "semiannual", "yearly")

Trust model:
  * The app reports a purchase token; the token itself proves nothing. Every
    token is looked up with the Play Developer API (`fetch`) before anything
    is granted, and the purchase must carry this account's obfuscated account
    id — set by the app from `account_ref()` when it opened the purchase — so
    one person's token cannot be redeemed by another account.
  * Real-time developer notifications (renewals, cancellations, refunds) arrive
    through a Cloud Pub/Sub push to /billing/webhook/play?token=…. They are
    authenticated by that shared secret and, like the app's report, only ever
    trigger a fresh lookup — the notification body is not trusted for state.
    That also makes them idempotent, so a redelivery is harmless.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from ...config import settings
from .. import users as users_svc
from . import plans, store

log = logging.getLogger("decint.billing.play")

PROVIDER = "google_play"
API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# subscriptionsv2 states → the status vocabulary the subscriptions table uses
# (store.active_subscription treats trialing/active/past_due as live).
STATUS = {
    "SUBSCRIPTION_STATE_ACTIVE": "active",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD": "past_due",
    # Auto-renew switched off, but paid up until expiryTime: still live.
    "SUBSCRIPTION_STATE_CANCELED": "active",
    "SUBSCRIPTION_STATE_ON_HOLD": "unpaid",
    "SUBSCRIPTION_STATE_PAUSED": "paused",
    "SUBSCRIPTION_STATE_EXPIRED": "canceled",
    "SUBSCRIPTION_STATE_PENDING": "incomplete",
    "SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED": "incomplete_expired",
}
LIVE_STATES = {
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
    "SUBSCRIPTION_STATE_CANCELED",
}
DEAD_STATES = {
    "SUBSCRIPTION_STATE_ON_HOLD",
    "SUBSCRIPTION_STATE_PAUSED",
    "SUBSCRIPTION_STATE_EXPIRED",
    "SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED",
}


class PlayError(RuntimeError):
    """Google's API refused or could not be reached."""


def available() -> bool:
    return settings.play_enabled


# ─────────────────────────── account linking ───────────────────────────

def account_ref(user_id: int) -> str:
    """The obfuscated account id the app attaches to a purchase. A keyed hash,
    as Google asks: it identifies the account to us and to nobody else."""
    mac = hmac.new(settings.session_secret.encode(), f"google-play:{user_id}".encode(), hashlib.sha256)
    return mac.hexdigest()[:40]


def link_account(user: dict) -> str:
    """Remember the ref → account mapping, so a notification about a purchase
    can be traced back to the account that made it."""
    ref = account_ref(user["id"])
    if store.customer_ref(user["id"], PROVIDER) != ref:
        store.save_customer(user["id"], PROVIDER, ref)
    return ref


def live_subscription(user: dict) -> dict | None:
    """The account's current Play subscription row, if it has one."""
    sub = store.active_subscription(user["id"])
    return sub if sub and sub["provider"] == PROVIDER else None


# ─────────────────────────── the Play Developer API ───────────────────────────

_cred_lock = threading.Lock()
_creds = None


def _access_token() -> str:
    global _creds
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import service_account

    with _cred_lock:
        if _creds is None:
            _creds = service_account.Credentials.from_service_account_file(
                settings.play_service_account_file, scopes=[SCOPE]
            )
        if not _creds.valid:
            _creds.refresh(GoogleRequest())
        return _creds.token


def _api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API}/{quote(settings.play_package_name)}/{path}"
    try:
        r = httpx.request(
            method, url, json=body,
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=20.0,
        )
    except Exception as e:
        raise PlayError(f"Google Play API unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = r.text[:200]
        raise PlayError(f"Google Play API {r.status_code}: {detail}")
    return r.json() if r.content else {}


def fetch(token: str) -> dict:
    return _api("GET", f"purchases/subscriptionsv2/tokens/{quote(token, safe='')}")


def acknowledge(product_id: str, token: str) -> None:
    # Unacknowledged purchases are refunded by Google after three days.
    _api("POST", f"purchases/subscriptions/{quote(product_id)}/tokens/{quote(token, safe='')}:acknowledge", {})


def cancel(product_id: str, token: str) -> None:
    _api("POST", f"purchases/subscriptions/{quote(product_id)}/tokens/{quote(token, safe='')}:cancel")


# ─────────────────────────── applying a purchase ───────────────────────────

def plan_for(product_id: str, base_plan_id: str) -> tuple[str, str] | None:
    plan = plans.get(product_id)
    if plan is None or not plan.purchasable() or base_plan_id not in plans.PERIOD_MONTHS:
        return None
    return plan.key, base_plan_id


def _iso(rfc3339: str | None) -> str | None:
    if not rfc3339:
        return None
    try:
        dt = datetime.fromisoformat(rfc3339.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _price_cents(item: dict) -> tuple[int, str]:
    money = (item.get("autoRenewingPlan") or {}).get("recurringPrice") or {}
    units = int(money.get("units") or 0)
    nanos = int(money.get("nanos") or 0)
    return units * 100 + nanos // 10_000_000, (money.get("currencyCode") or "").lower()


def apply(token: str, *, expected_user_id: int | None = None) -> dict:
    """Look the purchase up with Google and bring the account in line with it.

    `expected_user_id` is the signed-in account when the app reports a
    purchase; a token whose purchase was made for any other account is
    refused. Notifications pass None and are attributed through the
    obfuscated account id instead.
    """
    sub = fetch(token)
    ref = (sub.get("externalAccountIdentifiers") or {}).get("obfuscatedExternalAccountId", "")
    user_id = store.user_for_customer(PROVIDER, ref) if ref else None
    if expected_user_id is not None and user_id != expected_user_id:
        raise PermissionError("This purchase was made for a different account.")
    if not user_id or not users_svc.get(user_id):
        # Deleted since, or a purchase made outside the app's flow.
        log.warning("play purchase %s… maps to no account", token[:12])
        return {"applied": False}

    items = sub.get("lineItems") or []
    if not items:
        raise ValueError("The purchase has no subscription in it.")
    item = items[0]
    product_id = item.get("productId", "")
    base_plan = (item.get("offerDetails") or {}).get("basePlanId", "")
    mapped = plan_for(product_id, base_plan)
    if mapped is None:
        raise ValueError(f"Unknown Play product {product_id}/{base_plan}.")
    plan_key, period = mapped

    state = sub.get("subscriptionState", "")
    expiry = _iso(item.get("expiryTime"))
    auto_renew = bool((item.get("autoRenewingPlan") or {}).get("autoRenewEnabled"))

    # An upgrade, downgrade or resubscribe replaces an older purchase. Close
    # the old row so it stops looking live.
    linked = sub.get("linkedPurchaseToken")
    if linked:
        old = store.subscription_by_ref(linked)
        if old and old["user_id"] == user_id:
            store.upsert_subscription(
                user_id, PROVIDER, linked, old["plan"], "canceled",
                old["current_period_end"], True, period=old.get("period") or "monthly",
            )

    store.upsert_subscription(
        user_id, PROVIDER, token, plan_key, STATUS.get(state, "incomplete"), expiry,
        not auto_renew, period=period,
    )

    if state in LIVE_STATES:
        store.grant(user_id, plan_key, PROVIDER, expires_at=expiry, reason=f"google play {state}")
        if sub.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_PENDING":
            acknowledge(product_id, token)
        order_id = sub.get("latestOrderId")
        if order_id:
            cents, currency = _price_cents(item)
            store.record_paid_order(
                user_id, PROVIDER, plan_key, period,
                amount_cents=cents, currency=currency, reference=order_id,
                detail="Google Play" + (" test purchase" if sub.get("testPurchase") else ""),
            )
    elif state in DEAD_STATES:
        # Only take the plan away if this purchase is what grants it: after an
        # upgrade the old token expires while the new one keeps the account on.
        current = store.active_subscription(user_id)
        ent = store.entitlement(user_id) or {}
        if ent.get("source") == PROVIDER and not current:
            store.revoke(user_id, PROVIDER, reason=f"google play {state}")

    return {"applied": True, "user_id": user_id, "plan": plan_key, "period": period, "state": state}


def cancel_now(user_id: int) -> str | None:
    """Stop the account's Play subscription renewing, for an account being
    deleted. Google keeps the remaining paid time; there is just no account
    left to use it. A PlayError propagates so the deletion can be refused."""
    row = store.active_subscription(user_id)
    if not row or row["provider"] != PROVIDER:
        return None
    cancel(row["plan"], row["subscription_ref"])
    log.info("cancelled play subscription for deleted account %s", user_id)
    return row["subscription_ref"][:12] + "…"


# ─────────────────────────── notifications ───────────────────────────

def handle_rtdn(raw: bytes, token: str) -> dict:
    """A Pub/Sub push of one real-time developer notification."""
    secret = settings.play_rtdn_token
    if not secret or not hmac.compare_digest(token.encode(), secret.encode()):
        raise PermissionError("bad or missing token")
    try:
        envelope = json.loads(raw)
        data = json.loads(base64.b64decode(envelope["message"]["data"]))
    except (ValueError, KeyError, TypeError) as e:
        raise PermissionError("not a Pub/Sub push") from e

    if data.get("packageName") != settings.play_package_name:
        return {"ok": True, "ignored": "other package"}
    if "testNotification" in data:
        log.info("play: test notification received")
        return {"ok": True, "test": True}
    note = data.get("subscriptionNotification")
    if not note or not note.get("purchaseToken"):
        return {"ok": True, "ignored": "not a subscription notification"}

    result = apply(note["purchaseToken"])
    log.info("play: notification type %s → %s", note.get("notificationType"), result.get("state", "unmapped"))
    return {"ok": True, **{k: v for k, v in result.items() if k != "user_id"}}

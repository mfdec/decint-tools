"""The crypto rail: NOWPayments hosted invoices (BTC + ~300 other assets).

**Crypto is sold as a prepaid period, never as a subscription.** No chain lets
a merchant debit a wallet on a schedule — there is no mandate, no pull, no card
on file. Anything calling itself a "crypto subscription" is really a reminder
email. So this rail sells one month or one year up front and pushes the
entitlement's expiry forward, and the UI says exactly that.

Why NOWPayments rather than the name most people reach for first: Coinbase
Commerce dropped native Bitcoin (and every other UTXO coin) in February 2024
and closed its merchant portal on 31 March 2026, so it is not an option for a
BTC-accepting merchant any more. NOWPayments is custodial and hosted, which is
the trade for not running a node — see BTCPay Server in the docs for the
self-hosted, zero-fee alternative this module's shape leaves room for.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from ...config import settings
from . import plans, store

log = logging.getLogger("decint.billing.nowpayments")

PROVIDER = "nowpayments"
API_BASE = "https://api.nowpayments.io/v1"
TIMEOUT = 20.0

# NOWPayments payment lifecycle. Only `finished` means the money is ours:
#   waiting        invoice open, nothing received
#   confirming     seen on-chain, not yet enough confirmations
#   confirmed      confirmed on-chain, not yet settled to the merchant balance
#   sending        being settled
#   partially_paid customer underpaid — NOT access, a support case
#   finished       settled
#   failed / expired / refunded
GRANTING = {"finished"}
DEAD = {"failed", "expired"}


def available() -> bool:
    return settings.crypto_enabled


def _headers() -> dict:
    return {
        "x-api-key": settings.nowpayments_api_key,
        "Content-Type": "application/json",
    }


# Our own join key, echoed back on every callback. We resolve the callback
# against this rather than against anything NOWPayments generates, so the
# lookup never depends on which of invoice_id / payment_id / purchase_id a
# given callback happens to carry.
def _order_ref(order_id: int) -> str:
    return f"decint-{order_id}"


def _parse_order_ref(raw: str | None) -> int | None:
    if not raw or not str(raw).startswith("decint-"):
        return None
    try:
        return int(str(raw).split("-", 1)[1])
    except (IndexError, ValueError):
        return None


# ─────────────────────────── API ───────────────────────────

def status() -> tuple[bool, str]:
    """Is the gateway reachable and is the key accepted?"""
    if not settings.nowpayments_api_key:
        return False, "no API key configured"
    try:
        r = httpx.get(f"{API_BASE}/status", headers=_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        return True, r.json().get("message", "ok")
    except httpx.HTTPError as e:
        return False, f"{type(e).__name__}: {e}"


def currencies() -> list[str]:
    """Coins this merchant account can actually accept.

    Intersected with NOWPAYMENTS_CURRENCIES so the checkout never offers a coin
    the account cannot settle — an invoice in an unsupported asset fails at the
    gateway, after the customer has already chosen it.
    """
    wanted = settings.nowpayments_currency_list
    try:
        r = httpx.get(f"{API_BASE}/currencies", headers=_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        supported = {c.lower() for c in r.json().get("currencies", [])}
    except httpx.HTTPError as e:
        log.warning("could not list NOWPayments currencies: %s", e)
        return wanted
    if not wanted:
        return sorted(supported)
    return [c for c in wanted if c in supported]


class BelowMinimum(ValueError):
    """The price is under what the gateway will accept in the chosen coin(s).

    A ValueError so the router can show its message to the customer: unlike a
    gateway outage, this is something they can act on.
    """


def min_fiat(coin: str) -> float | None:
    """Smallest payment NOWPayments accepts in `coin`, in the billing currency.

    The minimum moves with the exchange rate and network fees (BTC was ~$22 when
    a $4.95 invoice was refused on the hosted page). None when it cannot be
    determined, so a lookup failure never blocks a sale the gateway would take.
    """
    try:
        r = httpx.get(
            f"{API_BASE}/min-amount",
            headers=_headers(),
            params={
                "currency_from": coin.lower(),
                "fiat_equivalent": settings.billing_currency.lower(),
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        value = r.json().get("fiat_equivalent")
        return float(value) if value is not None else None
    except (httpx.HTTPError, ValueError, TypeError) as e:
        log.warning("could not read NOWPayments minimum for %s: %s", coin, e)
        return None


def _check_minimum(price: float, pay_currency: str) -> None:
    """Refuse up front what the hosted invoice page would refuse later."""
    currency = settings.billing_currency.upper()
    if pay_currency:
        floor = min_fiat(pay_currency)
        if floor is not None and price < floor:
            raise BelowMinimum(
                f"{pay_currency.upper()} payments need at least about "
                f"{floor:.2f} {currency} each, and this plan is {price:.2f} "
                f"{currency}. Choose a longer billing period, another coin, "
                "or pay by card."
            )
        return
    # No coin chosen: the customer picks on the invoice page, so only refuse
    # when nothing we offer could take this amount.
    floors = [f for f in map(min_fiat, settings.nowpayments_currency_list) if f is not None]
    if floors and price < min(floors):
        raise BelowMinimum(
            f"This plan ({price:.2f} {currency}) is below the minimum crypto "
            f"payment (about {min(floors):.2f} {currency}). Choose a longer "
            "billing period or pay by card."
        )


def create_checkout(
    user: dict, plan: plans.Plan, period: str, order_id: int, pay_currency: str = ""
) -> str:
    """Create a hosted invoice and return the URL to send the customer to."""
    base = settings.public_base_url.rstrip("/")
    months = plans.months_for(period)
    price = round(plan.cents(period) / 100, 2)
    _check_minimum(price, pay_currency.lower())
    body = {
        "price_amount": price,
        "price_currency": settings.billing_currency,
        "order_id": _order_ref(order_id),
        # ASCII only: the IPN signature is verified by re-serialising this
        # payload, and non-ASCII would have to survive that round trip byte for
        # byte to still match.
        "order_description": f"DECINT {plan.name} - {months} month access",
        "ipn_callback_url": f"{base}/api/v1/billing/webhook/nowpayments",
        "success_url": f"{base}{settings.billing_success_path}?provider=nowpayments",
        "cancel_url": f"{base}{settings.billing_cancel_path}",
    }
    if pay_currency:
        body["pay_currency"] = pay_currency.lower()

    r = httpx.post(
        f"{API_BASE}/invoice", headers=_headers(), json=body, timeout=TIMEOUT
    )
    if r.status_code >= 400:
        log.error("NOWPayments invoice failed (%s): %s", r.status_code, r.text[:400])
        raise RuntimeError(
            f"The crypto gateway refused the invoice ({r.status_code})."
        )
    data = r.json()
    invoice_url = data.get("invoice_url")
    if not invoice_url:
        raise RuntimeError("The crypto gateway returned no invoice URL.")

    store.attach_reference(order_id, str(data.get("id", "")))
    return invoice_url


def payment_status(payment_id: str) -> dict:
    r = httpx.get(
        f"{API_BASE}/payment/{payment_id}", headers=_headers(), timeout=TIMEOUT
    )
    r.raise_for_status()
    return r.json()


# ─────────────────────────── webhook ───────────────────────────

def verify_signature(raw: bytes, signature: str) -> dict:
    """Check `x-nowpayments-sig` and return the parsed body.

    NOWPayments signs the HMAC-SHA512 of the callback JSON re-serialised with
    its keys sorted alphabetically and no whitespace — the Python equivalent of
    the `JSON.stringify(body, Object.keys(body).sort())` in their own SDK. The
    payloads are flat and ASCII, which is what makes that round trip reliable.
    """
    if not settings.nowpayments_ipn_secret:
        raise PermissionError("NOWPAYMENTS_IPN_SECRET is not configured.")
    if not signature:
        raise PermissionError("Missing x-nowpayments-sig header.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PermissionError(f"Unparseable IPN body: {e}") from e

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(
        settings.nowpayments_ipn_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise PermissionError("Invalid IPN signature.")
    return payload


def handle_webhook(raw: bytes, signature: str) -> dict:
    payload = verify_signature(raw, signature)

    payment_status_ = str(payload.get("payment_status", "")).lower()
    payment_id = str(payload.get("payment_id", ""))
    order_id = _parse_order_ref(payload.get("order_id"))

    if order_id is None:
        log.warning("IPN with unrecognised order_id %r", payload.get("order_id"))
        return {"handled": False, "reason": "unknown order"}

    order = store.get_order(order_id)
    if not order or order["provider"] != PROVIDER:
        log.warning("IPN for missing order %s", order_id)
        return {"handled": False, "reason": "unknown order"}

    # One event per payment per status. NOWPayments walks an invoice through
    # several statuses and retries each, so dedupe on the pair rather than on
    # the payment id alone — otherwise only the first status would ever land.
    if store.seen_event(PROVIDER, f"{payment_id}:{payment_status_}", payment_status_):
        return {"handled": False, "reason": "duplicate", "status": payment_status_}

    pay_currency = str(payload.get("pay_currency") or "")

    if payment_status_ in GRANTING:
        # The signature already proves the body is NOWPayments'. This second
        # check catches the different failure: our own order row and the
        # gateway's invoice having drifted apart, which would mean granting a
        # plan against the wrong price.
        expected_amount = round(order["amount_cents"] / 100, 2)
        quoted = payload.get("price_amount")
        try:
            quoted_ok = quoted is not None and abs(float(quoted) - expected_amount) < 0.01
        except (TypeError, ValueError):
            quoted_ok = False
        if not quoted_ok:
            log.error(
                "IPN price mismatch on order %s: expected %s, callback said %r",
                order_id, expected_amount, quoted,
            )
            store.set_order_status(
                order["id"], store.FAILED,
                detail=f"price mismatch: expected {expected_amount}, got {quoted}",
                pay_currency=pay_currency,
            )
            return {"handled": False, "reason": "price mismatch"}

        if order["status"] == store.PAID:
            return {"handled": False, "reason": "already settled"}

        store.set_order_status(
            order["id"], store.PAID, detail=payment_status_, pay_currency=pay_currency
        )
        store.extend(
            order["user_id"], order["plan"], PROVIDER, int(order["months"]),
            reason=f"crypto payment {payment_id} ({pay_currency or 'unknown coin'})",
        )
        return {"handled": True, "status": payment_status_}

    if payment_status_ == "partially_paid":
        # Underpaid. Granting a full period for a partial payment is the most
        # expensive possible rounding error, so this is left for a human.
        store.set_order_status(
            order["id"], store.PENDING,
            detail=f"partially paid: {payload.get('actually_paid')} {pay_currency}",
            pay_currency=pay_currency,
        )
        log.warning(
            "order %s underpaid: %s of %s %s",
            order_id, payload.get("actually_paid"), payload.get("pay_amount"),
            pay_currency,
        )
        return {"handled": True, "status": payment_status_, "note": "underpaid"}

    if payment_status_ == "refunded":
        store.set_order_status(order["id"], store.REFUNDED, detail=payment_status_)
        store.revoke(order["user_id"], PROVIDER, reason=f"refund on payment {payment_id}")
        return {"handled": True, "status": payment_status_}

    if payment_status_ in DEAD:
        store.set_order_status(
            order["id"],
            store.EXPIRED if payment_status_ == "expired" else store.FAILED,
            detail=payment_status_,
        )
        return {"handled": True, "status": payment_status_}

    # waiting / confirming / confirmed / sending — progress, not settlement.
    store.set_order_status(
        order["id"], store.PENDING, detail=payment_status_, pay_currency=pay_currency
    )
    return {"handled": True, "status": payment_status_, "note": "in progress"}

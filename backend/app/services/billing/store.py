"""Billing persistence, and the one place an entitlement is granted.

The rule this module exists to enforce: **a webhook never says what someone
bought.** It says only "reference X settled". What X is worth was decided when
the order row was written, before the customer ever reached the processor. So
every grant path here starts from a `billing_orders` row or a subscription we
created, never from a number in a callback body.

`entitlements` is the read model; `users.tier` is kept in step with it so every
existing feature gate keeps reading the column it already reads.
"""

from __future__ import annotations

import calendar
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from ... import db
from ...config import settings
from .. import mail
from .. import users as users_svc
from . import plans

log = logging.getLogger("decint.billing")

PENDING, PAID, FAILED, EXPIRED, REFUNDED = (
    "pending", "paid", "failed", "expired", "refunded",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def add_months(start: datetime, months: int) -> datetime:
    """Calendar-month arithmetic, clamped to the end of a short month.

    31 Jan + 1 month is 28/29 Feb, not 3 March. Getting this wrong hands people
    an extra day or two of access every month they renew — small, but it
    compounds and it is visible on an invoice.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


# ─────────────────────────── webhook idempotency ───────────────────────────

def seen_event(provider: str, event_id: str, event_type: str = "") -> bool:
    """Record an event id; return True if it had already been recorded.

    Every processor retries on a non-2xx, and some retry on a slow 2xx. Without
    this, one retried "payment finished" buys the customer a second month.
    """
    if not event_id:
        # Nothing to deduplicate on. Better to process than to drop a payment;
        # the callers below are written so a replay is inert anyway.
        return False
    key = f"{provider}:{event_id}"
    if db.one("SELECT 1 AS x FROM webhook_events WHERE id = ?", (key,)):
        return True
    db.execute(
        "INSERT INTO webhook_events (id, provider, event_type, received_at) "
        "VALUES (?,?,?,?)",
        (key, provider, event_type, _now()),
    )
    return False


# ─────────────────────────── customers ───────────────────────────

def customer_ref(user_id: int, provider: str) -> str | None:
    row = db.one(
        "SELECT customer_ref FROM billing_customers WHERE user_id = ? AND provider = ?",
        (user_id, provider),
    )
    return row["customer_ref"] if row else None


def save_customer(user_id: int, provider: str, ref: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO billing_customers (user_id, provider, customer_ref, created_at) "
        "VALUES (?,?,?,?)",
        (user_id, provider, ref, _now()),
    )


def user_for_customer(provider: str, ref: str) -> int | None:
    row = db.one(
        "SELECT user_id FROM billing_customers WHERE provider = ? AND customer_ref = ?",
        (provider, ref),
    )
    return row["user_id"] if row else None


# ─────────────────────────── orders ───────────────────────────

def create_order(
    user_id: int, provider: str, plan_key: str, period: str
) -> dict:
    """Write the intent before the customer leaves for the processor."""
    plan = plans.require_purchasable(plan_key, period)
    months = plans.months_for(period)
    oid = db.execute(
        "INSERT INTO billing_orders "
        "(user_id, provider, plan, period, months, amount_cents, currency, "
        " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (user_id, provider, plan.key, period, months, plan.cents(period),
         settings.billing_currency, PENDING, _now(), _now()),
    )
    return get_order(oid)  # type: ignore[return-value]


def get_order(order_id: int) -> dict | None:
    return db.one("SELECT * FROM billing_orders WHERE id = ?", (order_id,))


def order_by_reference(provider: str, reference: str) -> dict | None:
    if not reference:
        return None
    return db.one(
        "SELECT * FROM billing_orders WHERE provider = ? AND reference = ?",
        (provider, str(reference)),
    )


def attach_reference(order_id: int, reference: str) -> None:
    db.execute(
        "UPDATE billing_orders SET reference = ?, updated_at = ? WHERE id = ?",
        (str(reference), _now(), order_id),
    )


def set_order_status(
    order_id: int,
    status: str,
    *,
    detail: str = "",
    pay_currency: str = "",
) -> None:
    db.execute(
        "UPDATE billing_orders SET status = ?, detail = ?, pay_currency = ?, "
        "updated_at = ?, paid_at = CASE WHEN ? = 'paid' THEN ? ELSE paid_at END "
        "WHERE id = ?",
        (status, detail or None, pay_currency or None, _now(), status, _now(), order_id),
    )


def discard_order(order_id: int) -> None:
    """Remove an order that never became a charge. Only a pending row can go:
    anything that has settled, failed or expired is history and stays."""
    db.execute(
        "DELETE FROM billing_orders WHERE id = ? AND status = ?", (order_id, PENDING)
    )


def set_order_amount(order_id: int, amount_cents: int, currency: str = "") -> None:
    """Correct the amount on an order after the processor has priced it.

    An in-place plan change is invoiced pro rata, so what the card was actually
    charged is less than the catalogue price the row was written with. The
    row is history for the customer, and history should say what they paid.
    """
    db.execute(
        "UPDATE billing_orders SET amount_cents = ?, "
        "currency = COALESCE(NULLIF(?, ''), currency), updated_at = ? WHERE id = ?",
        (int(amount_cents), currency.lower(), _now(), order_id),
    )


def record_paid_order(
    user_id: int,
    provider: str,
    plan_key: str,
    period: str,
    *,
    amount_cents: int,
    currency: str,
    reference: str,
    detail: str = "",
) -> dict | None:
    """Write a settled order the customer never initiated — a renewal the
    processor pulled on its own.

    Unlike `create_order` this is not the source of truth for anything: the
    entitlement was already set from the subscription. It exists so the
    billing page's history shows every charge the card took, not just the
    first one. Keyed on the processor's reference, so a replayed webhook
    finds the existing row instead of writing a duplicate."""
    existing = order_by_reference(provider, reference)
    if existing:
        return existing
    plan = plans.get(plan_key)
    if plan is None or period not in plans.PERIOD_MONTHS:
        return None
    oid = db.execute(
        "INSERT INTO billing_orders "
        "(user_id, provider, plan, period, months, amount_cents, currency, "
        " reference, status, detail, created_at, updated_at, paid_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, provider, plan.key, period, plans.months_for(period),
         int(amount_cents), (currency or settings.billing_currency).lower(),
         str(reference), PAID, detail or None, _now(), _now(), _now()),
    )
    return get_order(oid)


def orders_for(user_id: int, limit: int = 25) -> list[dict]:
    return db.query(
        "SELECT id, provider, plan, period, months, amount_cents, currency, "
        "status, pay_currency, created_at, paid_at FROM billing_orders "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )


# ─────────────────────────── subscriptions ───────────────────────────

def upsert_subscription(
    user_id: int,
    provider: str,
    ref: str,
    plan_key: str,
    status: str,
    current_period_end: str | None,
    cancel_at_period_end: bool = False,
    period: str = "monthly",
) -> None:
    existing = db.one(
        "SELECT id FROM subscriptions WHERE subscription_ref = ?", (ref,)
    )
    if existing:
        db.execute(
            "UPDATE subscriptions SET plan = ?, period = ?, status = ?, "
            "current_period_end = ?, cancel_at_period_end = ?, updated_at = ? "
            "WHERE subscription_ref = ?",
            (plan_key, period, status, current_period_end,
             int(cancel_at_period_end), _now(), ref),
        )
        return
    db.execute(
        "INSERT INTO subscriptions (user_id, provider, subscription_ref, plan, "
        "period, status, current_period_end, cancel_at_period_end, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (user_id, provider, ref, plan_key, period, status, current_period_end,
         int(cancel_at_period_end), _now(), _now()),
    )


def subscription_by_ref(ref: str) -> dict | None:
    return db.one("SELECT * FROM subscriptions WHERE subscription_ref = ?", (ref,))


def active_subscription(user_id: int) -> dict | None:
    return db.one(
        "SELECT * FROM subscriptions WHERE user_id = ? "
        "AND status IN ('trialing','active','past_due') "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


# ─────────────────────────── entitlements ───────────────────────────

def entitlement(user_id: int) -> dict | None:
    return db.one("SELECT * FROM entitlements WHERE user_id = ?", (user_id,))


# Grants that are not a customer paying, and so earn no "new paid plan" alert:
# an admin comp/wire ("manual") and the nightly lapse back to free ("lapsed").
# Every other source landing on a paid tier is a sale worth hearing about.
_NON_SALE_SOURCES = {"manual", "lapsed"}


def paying_customers() -> dict:
    """How many end-users are on a paid plan they actually paid for.

    Counts `role = 'user'` accounts whose live entitlement sits on a paid tier
    with a payment source — so staff (comped enterprise, no entitlement row),
    admin comps (`manual`), lapsed rows and expired periods are all left out.
    This is revenue, not an entitlement headcount."""
    placeholders = ",".join("?" for _ in _NON_SALE_SOURCES)
    rows = db.query(
        "SELECT e.tier AS k, COUNT(*) AS n "
        "FROM entitlements e JOIN users u ON u.id = e.user_id "
        f"WHERE u.role = 'user' AND e.tier != ? AND e.source NOT IN ({placeholders}) "
        "AND (e.expires_at IS NULL OR e.expires_at > ?) "
        "GROUP BY e.tier",
        (plans.FREE_PLAN.key, *_NON_SALE_SOURCES, _now()),
    )
    by_tier = {r["k"]: r["n"] for r in rows}
    return {"paying": sum(by_tier.values()), "by_tier": by_tier}


def _write_entitlement(
    user_id: int, tier: str, source: str, expires_at: str | None
) -> None:
    before = entitlement(user_id)
    # A kept allowance belongs to the plan it was sold with: changing plan, or
    # lapsing to free, ends it. Renewing the same plan leaves it alone.
    if before and before["tier"] != tier:
        db.execute("DELETE FROM quota_grandfathers WHERE user_id = ?", (user_id,))
    # notice_sent_at resets here: buying another period earns another warning.
    db.execute(
        "INSERT INTO entitlements "
        "(user_id, tier, source, expires_at, updated_at, notice_sent_at) "
        "VALUES (?,?,?,?,?,NULL) ON CONFLICT(user_id) DO UPDATE SET "
        "tier = excluded.tier, source = excluded.source, "
        "expires_at = excluded.expires_at, updated_at = excluded.updated_at, "
        "notice_sent_at = NULL",
        (user_id, tier, source, expires_at, _now()),
    )
    # Keep the column every existing gate reads in step with the read model.
    db.execute(
        "UPDATE users SET tier = ?, updated_at = ? WHERE id = ?",
        (tier, _now(), user_id),
    )
    _alert_if_new_paid(user_id, before, tier, source, expires_at)


def _alert_if_new_paid(
    user_id: int,
    before: dict | None,
    tier: str,
    source: str,
    expires_at: str | None,
) -> None:
    """Tell the operators when an account first reaches a paid plan, or moves
    between paid plans. A renewal of the same plan, a drop to free (revoke or
    lapse), and admin comps all stay quiet — so the alert means a new paying
    customer and nothing else."""
    if tier == plans.FREE_PLAN.key or source in _NON_SALE_SOURCES:
        return
    previous = before["tier"] if before else ""
    if previous == tier:
        return  # a renewal of the same plan — not news
    user = users_svc.get(user_id)
    if not user:
        return
    plan = plans.get(tier)
    mail.notify_admins(
        *mail.subscription_alert(
            user.get("email") or "",
            user.get("username") or "",
            plan.name if plan else tier,
            previous,
            expires_at or "",
        )
    )


def grant(
    user_id: int,
    tier: str,
    source: str,
    *,
    expires_at: str | None,
    reason: str = "",
) -> dict:
    """Set an entitlement outright. Used by the card rail, whose renewal date
    the processor owns, and by admin grants (expires_at=None)."""
    if tier not in users_svc.TIERS:
        raise ValueError(f"tier must be one of {users_svc.TIERS}")
    _write_entitlement(user_id, tier, source, expires_at)
    users_svc.audit(
        "billing.grant",
        target=str(user_id),
        detail=json.dumps(
            {"tier": tier, "source": source, "expires_at": expires_at,
             "reason": reason}
        ),
    )
    log.info("entitlement: user=%s tier=%s source=%s until=%s",
             user_id, tier, source, expires_at or "never")
    return entitlement(user_id)  # type: ignore[return-value]


def extend(user_id: int, tier: str, source: str, months: int, *, reason: str = "") -> dict:
    """Add prepaid time — the crypto rail's only grant path.

    Extends from whichever is later: now, or the current expiry. Renewing early
    therefore stacks rather than burning the remaining days, which is the
    behaviour someone paying on-chain a week ahead of expiry expects.

    A tier *upgrade* keeps the remaining time rather than restarting it; that is
    slightly generous, and much easier to explain than a pro-rata conversion no
    on-chain payment can refund.
    """
    if tier not in users_svc.TIERS:
        raise ValueError(f"tier must be one of {users_svc.TIERS}")
    current = entitlement(user_id)
    base = datetime.now(timezone.utc)
    if current and current["tier"] == tier:
        existing = _parse(current["expires_at"])
        if existing and existing > base:
            base = existing
    new_expiry = _iso(add_months(base, months))
    _write_entitlement(user_id, tier, source, new_expiry)
    users_svc.audit(
        "billing.extend",
        target=str(user_id),
        detail=json.dumps(
            {"tier": tier, "source": source, "months": months,
             "expires_at": new_expiry, "reason": reason}
        ),
    )
    log.info("entitlement extended: user=%s tier=%s +%sm until=%s",
             user_id, tier, months, new_expiry)
    return entitlement(user_id)  # type: ignore[return-value]


def revoke(user_id: int, source: str, *, reason: str = "") -> None:
    """Drop to free immediately — a cancelled or refunded subscription."""
    _write_entitlement(user_id, plans.FREE_PLAN.key, source, None)
    users_svc.audit(
        "billing.revoke", target=str(user_id),
        detail=json.dumps({"source": source, "reason": reason}),
    )


# ─────────────────────────── the lapse sweep ───────────────────────────

_sweep_lock = threading.Lock()
_last_sweep = 0.0
SWEEP_INTERVAL_SECONDS = 60.0

# How long a checkout may sit unpaid before the row is closed as expired. A
# Stripe Checkout session lives 24 hours; a NOWPayments invoice varies with the
# account's settings but is not open for days. Anything older is abandoned.
ORDER_TTL_HOURS = 48


def expire_stale_orders(max_age_hours: int = ORDER_TTL_HOURS) -> int:
    """Close pending orders nobody came back to.

    Nothing about access depends on this: an unpaid order grants nothing
    whether it says `pending` or `expired`. What does depend on it is the
    open-order cap on checkout — without this, ten abandoned checkouts would
    lock a customer out of buying anything, permanently, with no way to clear
    them. A payment that settles late is still honoured: both webhook handlers
    grant on a settled reference regardless of what the row said before.
    """
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(hours=max_age_hours))
    stale = db.query(
        "SELECT id FROM billing_orders WHERE status = ? AND created_at < ?",
        (PENDING, cutoff),
    )
    for row in stale:
        set_order_status(row["id"], EXPIRED, detail="abandoned")
    if stale:
        log.info("closed %s abandoned checkout(s)", len(stale))
    return len(stale)


def _renews_itself(user_id: int) -> bool:
    """Is a processor going to charge this account again without being asked?

    Only true for a live card subscription that has not been cancelled. It is
    the test for whether an expiry is news: a renewing subscription's period
    end is routine, a prepaid period's is the end of access.
    """
    sub = active_subscription(user_id)
    return bool(sub and not sub["cancel_at_period_end"])


def notify_expiring() -> int:
    """Warn accounts whose prepaid access is about to run out.

    This exists because of the asymmetry between the two rails. Cards renew
    themselves; crypto cannot, so for those customers the only thing standing
    between them and a silent loss of access is this email.
    """
    days = settings.billing_expiry_notice_days
    if days <= 0:
        return 0
    from .. import mail

    if not mail.available():
        return 0

    horizon = _iso(datetime.now(timezone.utc) + timedelta(days=days))
    due = db.query(
        "SELECT e.user_id, e.tier, e.expires_at, u.email FROM entitlements e "
        "JOIN users u ON u.id = e.user_id "
        "WHERE e.expires_at IS NOT NULL AND e.expires_at <= ? "
        "AND e.notice_sent_at IS NULL AND e.tier != ? AND u.status = 'active'",
        (horizon, plans.FREE_PLAN.key),
    )

    sent = 0
    for row in due:
        if _renews_itself(row["user_id"]):
            continue
        plan = plans.get(row["tier"]) or plans.FREE_PLAN
        left = _days_left(row["expires_at"]) or 0
        subject, body = mail.access_expiring(plan.name, left, row["tier"])
        mail.send_soon(row["email"], subject, body)
        db.execute(
            "UPDATE entitlements SET notice_sent_at = ? WHERE user_id = ?",
            (_now(), row["user_id"]),
        )
        sent += 1
        log.info("expiry notice queued for user=%s (%s days left)",
                 row["user_id"], left)
    return sent


def sweep() -> int:
    """Drop every entitlement whose grace period has run out, and warn the ones
    approaching it.

    Cheap enough to run often: two indexed range scans over a table with at
    most one row per paying account.
    """
    cutoff = _iso(
        datetime.now(timezone.utc) - timedelta(days=settings.billing_grace_days)
    )
    lapsed = db.query(
        "SELECT user_id, tier, source, expires_at FROM entitlements "
        "WHERE expires_at IS NOT NULL AND expires_at < ? AND tier != ?",
        (cutoff, plans.FREE_PLAN.key),
    )
    for row in lapsed:
        _write_entitlement(row["user_id"], plans.FREE_PLAN.key, "lapsed", None)
        users_svc.audit(
            "billing.lapsed", target=str(row["user_id"]),
            detail=json.dumps(
                {"was": row["tier"], "expired_at": row["expires_at"],
                 "source": row["source"]}
            ),
        )
        log.info("entitlement lapsed: user=%s was=%s expired=%s",
                 row["user_id"], row["tier"], row["expires_at"])
        _mail_lapsed(row["user_id"], row["tier"])

    try:
        notify_expiring()
    except Exception as e:  # pragma: no cover - a mail fault must not block the sweep
        log.warning("expiry notices failed: %s: %s", type(e).__name__, e)

    try:
        expire_stale_orders()
    except Exception as e:  # pragma: no cover - bookkeeping must not block the sweep
        log.warning("stale order sweep failed: %s: %s", type(e).__name__, e)

    return len(lapsed)


def _mail_lapsed(user_id: int, tier: str) -> None:
    from .. import mail

    if not mail.available():
        return
    row = db.one("SELECT email, status FROM users WHERE id = ?", (user_id,))
    if not row or row["status"] != "active":
        return
    plan = plans.get(tier) or plans.FREE_PLAN
    subject, body = mail.access_lapsed(plan.name)
    mail.send_soon(row["email"], subject, body)


def sweep_throttled() -> None:
    """Called from the auth path, so a lapse takes effect without a cron.

    Throttled to one pass a minute: the check is on the hot path for every
    authenticated request, and running the query per request would be pure
    waste for a table that changes a few times a day.
    """
    global _last_sweep
    import time

    now = time.monotonic()
    if now - _last_sweep < SWEEP_INTERVAL_SECONDS:
        return
    with _sweep_lock:
        if now - _last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        _last_sweep = now
    try:
        sweep()
    except Exception as e:  # pragma: no cover - never break a login over this
        log.warning("entitlement sweep failed: %s: %s", type(e).__name__, e)


# ─────────────────────────── read model for the UI ───────────────────────────

def summary(user: dict) -> dict:
    """Everything the billing screen needs about one account."""
    uid = user["id"]
    ent = entitlement(uid)
    sub = active_subscription(uid)
    tier = user.get("tier") or plans.FREE_PLAN.key
    plan = plans.get(tier) or plans.FREE_PLAN
    expires = (ent or {}).get("expires_at")
    from .. import usage  # local: usage reads plans, and this module is imported early

    return {
        "tier": tier,
        "plan_name": plan.name,
        "quota": usage.allowance(uid, plan),
        "quota_window": plan.quota_window,
        "usage": usage.status(user),
        "source": (ent or {}).get("source") or "manual",
        "expires_at": expires,
        "days_left": _days_left(expires),
        "renews": bool(sub and not sub["cancel_at_period_end"]),
        # A card subscription is changed in place against the card on file;
        # the pricing page uses this to say "switch" rather than "buy".
        "can_change_plan": bool(sub and sub["provider"] == "stripe"),
        "subscription": (
            {
                "provider": sub["provider"],
                "status": sub["status"],
                "plan": sub["plan"],
                "period": sub.get("period") or "monthly",
                "current_period_end": sub["current_period_end"],
                "cancel_at_period_end": bool(sub["cancel_at_period_end"]),
            }
            if sub
            else None
        ),
        "orders": orders_for(uid),
    }


def _days_left(expires_at: str | None) -> int | None:
    dt = _parse(expires_at)
    if not dt:
        return None
    return max(0, (dt - datetime.now(timezone.utc)).days)

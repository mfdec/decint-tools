"""Search metering — the counter behind the allowances on the pricing page.

Counts, never queries. The marketing copy promises that what someone searches
for is never persisted, and this module keeps that true: the only thing it
writes is how many searches an account has made in the current window.

Two kinds of window, because the free tier is not a small plan but a trial:

* a **paid** tier gets a monthly allowance that resets on the 1st (UTC);
* the **free** tier gets a fixed number of searches, ever. When they are gone
  the answer is a plan, not next month — the console says so and points at
  pricing, which is the whole purpose of letting people in for free.

Admins and operators are never metered; neither is a tier with no quota.

The check and the increment are one statement (`UPDATE … WHERE count < ?`),
so two searches racing on the last free slot cannot both get through — the
kind of thing that does not matter at 500 a month and matters completely at
three, ever.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

from fastapi import HTTPException

from .. import db
from . import users as users_svc
from .billing import plans

LIFETIME = "lifetime"
UNMETERED_ROLES = {"admin", "operator"}


class QuotaExceeded(Exception):
    def __init__(self, state: dict):
        super().__init__("search allowance used up")
        self.state = state


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plan_for(user: dict) -> plans.Plan:
    return plans.get(user.get("tier") or plans.FREE_PLAN.key) or plans.FREE_PLAN


def _window(plan: plans.Plan, now: datetime | None = None) -> str:
    if plan.quota_window == LIFETIME:
        return "all"
    now = now or _now()
    return f"{now.year:04d}-{now.month:02d}"


def _resets_at(plan: plans.Plan, now: datetime | None = None) -> str | None:
    if plan.quota_window == LIFETIME:
        return None
    now = now or _now()
    last_day = calendar.monthrange(now.year, now.month)[1]
    nxt = now.replace(day=last_day, hour=0, minute=0, second=0, microsecond=0)
    nxt = nxt.replace(day=1)
    month = now.month % 12 + 1
    year = now.year + (1 if now.month == 12 else 0)
    return nxt.replace(year=year, month=month).isoformat(timespec="seconds")


def allowance(user_id: int, plan: plans.Plan) -> int | None:
    """The monthly allowance this account gets on `plan`: the plan's own, or
    the larger one it was sold under if it bought before the plan was cut and
    has stayed on the plan since (see the quota_grandfathers table).

    Roles are not considered here — `limit_for` handles staff. Never lowers
    anyone below the plan's number, so raising a plan later needs no cleanup."""
    quota = plan.quota
    if quota is None or plan.is_free:
        return quota
    row = db.one(
        "SELECT quota FROM quota_grandfathers WHERE user_id = ? AND tier = ?",
        (user_id, plan.key),
    )
    return max(quota, int(row["quota"])) if row else quota


def limit_for(user: dict) -> int | None:
    """Searches allowed in the current window; None means unmetered."""
    if user.get("role") in UNMETERED_ROLES or user.get("break_glass"):
        return None
    return allowance(user["id"], _plan_for(user))


def reveals_secrets(user: dict) -> bool:
    """May this account see leak-search passwords unmasked? Staff and the
    break-glass operator always; everyone else per their plan, which today
    means every paid tier and not the free trial."""
    if user.get("role") in UNMETERED_ROLES or user.get("break_glass"):
        return True
    return _plan_for(user).reveals_secrets


def is_paid(user: dict) -> bool:
    """Is this a paying account (or staff)? The gate for tools the free trial
    doesn't include, like the spider. Staff and break-glass always pass; the
    free tier never does; every self-serve tier does."""
    if user.get("role") in UNMETERED_ROLES or user.get("break_glass"):
        return True
    return not _plan_for(user).is_free


def _count(user_id: int, window: str) -> int:
    row = db.one(
        "SELECT count FROM usage_counters WHERE user_id = ? AND window = ?",
        (user_id, window),
    )
    return int(row["count"]) if row else 0


def status(user: dict) -> dict:
    """What the account has used and has left — for the status bar, the
    billing page and the 402 that points at pricing."""
    plan = _plan_for(user)
    limit = limit_for(user)
    used = _count(user["id"], _window(plan)) if limit is not None else 0
    return {
        "used": used,
        "limit": limit,
        "remaining": None if limit is None else max(0, limit - used),
        "window": plan.quota_window if limit is not None else None,
        "resets_at": _resets_at(plan) if limit is not None else None,
        "tier": plan.key,
        "is_free": plan.is_free,
    }


def consume(user: dict) -> dict:
    """Take one search from the allowance, or raise QuotaExceeded.

    The increment is conditional on the count still being under the limit,
    so it doubles as the check — no window between the two for a second
    request to slip through.
    """
    limit = limit_for(user)
    if limit is None:
        return status(user)
    plan = _plan_for(user)
    window = _window(plan)
    now = _now().isoformat(timespec="seconds")
    db.execute(
        "INSERT OR IGNORE INTO usage_counters (user_id, window, count, updated_at) "
        "VALUES (?,?,0,?)",
        (user["id"], window, now),
    )
    changed = db.execute(
        "UPDATE usage_counters SET count = count + 1, updated_at = ? "
        "WHERE user_id = ? AND window = ? AND count < ?",
        (now, user["id"], window, limit),
    )
    if not changed:
        raise QuotaExceeded(status(user))
    return status(user)


def refund(user: dict) -> None:
    """Give a search back — the provider failed, not the customer. A trial of
    three must not spend one on our outage."""
    if limit_for(user) is None:
        return
    db.execute(
        "UPDATE usage_counters SET count = MAX(count - 1, 0), updated_at = ? "
        "WHERE user_id = ? AND window = ?",
        (_now().isoformat(timespec="seconds"), user["id"], _window(_plan_for(user))),
    )


def reset(user_id: int) -> None:
    """Operator override: wipe an account's counters (a comp, a support case)."""
    db.execute("DELETE FROM usage_counters WHERE user_id = ?", (user_id,))


def _exceeded_message(state: dict) -> str:
    if state.get("is_free"):
        n = state.get("limit") or 0
        return (
            f"You've used your {n} free search{'es' if n != 1 else ''}. "
            "Pick a plan to keep going."
        )
    n = state.get("limit") or 0
    return (
        f"You've used this month's allowance of {n:,} searches. "
        "It resets on the 1st, or upgrade for more."
    )


def take(user: dict) -> dict:
    """One search against the caller's allowance, or 402 Payment Required.

    Called from the handler body *after* input validation rather than as a
    dependency — a dependency runs before the body, so a malformed query
    would be charged and then refused. The 402 body is a plain string the
    console shows as-is; the X-Quota-* headers carry the numbers.
    """
    try:
        return consume(user)
    except QuotaExceeded as e:
        state = e.state
        users_svc.audit(
            "usage.blocked", actor=user, target=state.get("tier"),
            detail=f"{state.get('used')}/{state.get('limit')} ({state.get('window')})",
        )
        raise HTTPException(
            status_code=402,
            detail=_exceeded_message(state),
            headers={
                "X-Quota-Limit": str(state.get("limit")),
                "X-Quota-Used": str(state.get("used")),
                "X-Quota-Window": str(state.get("window")),
                "X-Upgrade-Path": "/pricing",
            },
        ) from e

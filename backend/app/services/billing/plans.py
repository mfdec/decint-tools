"""The plan catalogue — the one place prices live.

Both rails and both ends of the app read this. The frontend fetches it from
`GET /api/v1/billing/plans` rather than keeping its own copy, because a pricing
page that disagrees with what the processor charges is the single most
embarrassing bug this feature can have.

`key` doubles as the `tier` written to users.tier, so the catalogue and the
authorisation model cannot drift apart. Renaming a key therefore means
migrating every users.tier, entitlements.tier, billing_orders.plan and
subscriptions.plan row that holds the old value — see
`backend/migrations/002_rename_tiers.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Months bought per period. Yearly is priced at ten months — two free — which
# is also the reason the crypto rail is worth offering yearly: one on-chain fee
# a year instead of twelve.
PERIOD_MONTHS: dict[str, int] = {"monthly": 1, "yearly": 12}
PERIODS = tuple(PERIOD_MONTHS)


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    blurb: str
    monthly_cents: int
    yearly_cents: int
    quota: int | None            # searches per window; None = unmetered
    # "monthly" resets on the 1st; "lifetime" never does. The free tier is the
    # latter: a fixed number of searches to judge the console by, after which
    # the only way forward is a plan. That is the point of it.
    quota_window: str = "monthly"
    features: list[str] = field(default_factory=list)
    featured: bool = False
    # Sold by conversation, not by checkout — the button emails the operator.
    contact: bool = False
    # The tier an account sits on having bought nothing. Never checkout-able.
    is_free: bool = False

    def cents(self, period: str) -> int:
        return self.monthly_cents if period == "monthly" else self.yearly_cents

    def purchasable(self) -> bool:
        return not (self.contact or self.is_free) and self.monthly_cents > 0


PLANS: tuple[Plan, ...] = (
    Plan(
        key="free",
        name="Free",
        blurb="A look around the console, and enough lookups to judge it.",
        monthly_cents=0,
        yearly_cents=0,
        quota=3,
        quota_window="lifetime",
        features=[
            "Leak database search",
            "Dark-web search — fast mode",
            "Discord OSINT",
            "3 free searches, then pick a plan",
        ],
        is_free=True,
    ),
    Plan(
        key="essentials",
        name="Essentials",
        blurb="The three core tools, for occasional lookups and one-off investigations.",
        monthly_cents=2_900,
        yearly_cents=29_000,
        quota=500,
        features=[
            "Leak database search",
            "Dark-web search — fast mode",
            "Discord OSINT",
            "500 queries per month",
        ],
    ),
    Plan(
        key="pro",
        name="Pro",
        blurb="The same tools run deeper — live Tor circuits, and evidence you can put in a report.",
        monthly_cents=4_500,
        yearly_cents=45_000,
        quota=5_000,
        features=[
            "Everything in Essentials",
            "Dark-web search — full Tor mode",
            "Evidence hashes for reporting",
            "Entity extraction and corroboration scoring",
            "5,000 queries per month",
            "Priority support",
        ],
        featured=True,
    ),
    Plan(
        key="enterprise",
        name="Enterprise",
        blurb="Every tool unlocked against an activated licence, plus tooling built to your scope.",
        monthly_cents=0,
        yearly_cents=0,
        quota=None,
        features=[
            "Everything in Pro",
            "Every tool unlocked while the licence is active",
            "Custom tools built to your workflow",
            "Private data sources wired in",
            "Self-hosted or dedicated deployment",
            "Unmetered queries",
            "Priced per scope, quoted up front",
        ],
        contact=True,
    ),
)

BY_KEY: dict[str, Plan] = {p.key: p for p in PLANS}
FREE_PLAN = BY_KEY["free"]


def get(key: str) -> Plan | None:
    return BY_KEY.get(key)


def require_purchasable(key: str, period: str) -> Plan:
    """Resolve a checkout request, or raise ValueError.

    Guarding here rather than in the router means neither rail can be talked
    into selling `custom` (which has no price) or `free` (which has no point).
    """
    if period not in PERIOD_MONTHS:
        raise ValueError(f"period must be one of {PERIODS}")
    plan = BY_KEY.get(key)
    if plan is None:
        raise ValueError(f"No such plan: {key}")
    if not plan.purchasable():
        raise ValueError(f"The {plan.name} plan is not sold self-serve.")
    return plan


def months_for(period: str) -> int:
    return PERIOD_MONTHS.get(period, 1)


def as_dict(plan: Plan) -> dict:
    """Catalogue entry shaped for the pricing page."""
    return {
        "key": plan.key,
        "name": plan.name,
        "blurb": plan.blurb,
        "monthly_cents": plan.monthly_cents,
        "yearly_cents": plan.yearly_cents,
        "quota": plan.quota,
        "quota_window": plan.quota_window,
        "features": list(plan.features),
        "featured": plan.featured,
        "contact": plan.contact,
        "is_free": plan.is_free,
        "purchasable": plan.purchasable(),
    }


def catalogue() -> list[dict]:
    return [as_dict(p) for p in PLANS]

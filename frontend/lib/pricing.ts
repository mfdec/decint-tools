import type { BillingPlan } from "./types";

/**
 * Last-resort catalogue for the pricing table.
 *
 * The real catalogue is served by `GET /api/v1/billing/config`, which reads
 * `backend/app/services/billing/plans.py` — the one place prices live. This
 * array is only rendered when that request fails, so the page shows something
 * rather than an empty table.
 *
 * It therefore MUST mirror `plans.py`. If you change a price there, change it
 * here too; a fallback that disagrees with the charge is the bug the whole
 * fetch-it-from-the-backend arrangement exists to prevent.
 */
export const FALLBACK_PLANS: BillingPlan[] = [
  {
    key: "free",
    name: "Free",
    blurb: "A look around the console, and enough lookups to judge it.",
    monthly_cents: 0,
    yearly_cents: 0,
    semiannual_cents: 0,
    quota: 3,
    quota_window: "lifetime",
    features: [
      "Leak database search",
      "Dark-web search — fast mode",
      "Discord OSINT",
      "3 free searches, then pick a plan",
    ],
    featured: false,
    contact: false,
    is_free: true,
    purchasable: false,
  },
  {
    key: "starter",
    name: "Starter",
    blurb: "The three core tools, for occasional lookups and one-off investigations.",
    monthly_cents: 495,
    yearly_cents: 4950,
    semiannual_cents: 2525,
    quota: 500,
    quota_window: "monthly",
    features: [
      "Leak database search",
      "Dark-web search — fast mode",
      "Discord OSINT",
      "500 queries per month",
    ],
    featured: false,
    contact: false,
    is_free: false,
    purchasable: true,
  },
  {
    key: "pro",
    name: "Pro",
    blurb:
      "The same tools run deeper — live Tor circuits, and evidence you can put in a report.",
    monthly_cents: 1495,
    yearly_cents: 14950,
    semiannual_cents: 7625,
    quota: 5000,
    quota_window: "monthly",
    features: [
      "Everything in Starter",
      "Dark-web search — full Tor mode",
      "Evidence hashes for reporting",
      "Entity extraction and corroboration scoring",
      "5,000 queries per month",
    ],
    featured: true,
    contact: false,
    is_free: false,
    purchasable: true,
  },
  {
    key: "enterprise",
    name: "Enterprise",
    blurb:
      "Every tool unlocked against an activated licence, plus tooling built to your scope.",
    monthly_cents: 0,
    yearly_cents: 0,
    semiannual_cents: 0,
    quota: null,
    quota_window: "monthly",
    features: [
      "Everything in Pro",
      "Every tool unlocked while the licence is active",
      "Custom tools built to your workflow",
      "Private data sources wired in",
      "Self-hosted or dedicated deployment",
      "Unmetered queries",
      "Priced per scope, quoted up front",
    ],
    featured: false,
    contact: true,
    is_free: false,
    purchasable: false,
  },
];

"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { CONTACT_EMAIL } from "@/lib/site";
import { Check, CreditCard, Coin, Lock } from "@/components/icons";
import { FALLBACK_PLANS } from "@/lib/pricing";
import type {
  BillingConfig, BillingPeriod, BillingPlan, BillingProvider, BillingSummary,
} from "@/lib/types";

/**
 * The pricing table, and both checkout rails behind it.
 *
 * The catalogue comes from `GET /billing/config` rather than from a constant
 * in this file. That is the whole point: prices live in one place on the
 * backend, so the page and the charge cannot drift apart. FALLBACK_PLANS only
 * covers the case where the API is unreachable, and it renders the plans
 * without buy buttons — showing a price we could not confirm, next to a button
 * that would charge it, is worse than showing nothing.
 *
 * A signed-in card subscriber sees a different table: their plan is marked,
 * and every other plan is a *switch* applied to the subscription they have,
 * charged to the card Stripe already holds — no second checkout, no second
 * subscription. The API enforces that; the copy here just says so up front.
 */

function money(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
    maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);
}

/** Common coins get a readable label; anything else shows its raw ticker. */
const COIN_LABELS: Record<string, string> = {
  btc: "Bitcoin (BTC)",
  eth: "Ethereum (ETH)",
  ltc: "Litecoin (LTC)",
  xmr: "Monero (XMR)",
  usdc: "USD Coin (USDC)",
  usdt: "Tether (USDT)",
  usdttrc20: "Tether — TRON (USDT)",
  usdterc20: "Tether — Ethereum (USDT)",
  sol: "Solana (SOL)",
  doge: "Dogecoin (DOGE)",
  bnb: "BNB",
  trx: "TRON (TRX)",
};

const coinLabel = (c: string) => COIN_LABELS[c] ?? c.toUpperCase();

// Mirrors backend/app/services/billing/plans.py PERIOD_MONTHS.
const PERIOD_MONTHS: Record<BillingPeriod, number> = {
  monthly: 1,
  semiannual: 6,
  yearly: 12,
};

const PERIOD_LABEL: Record<BillingPeriod, string> = {
  monthly: "Monthly",
  semiannual: "6 months",
  yearly: "Yearly",
};

const PERIOD_SUFFIX: Record<BillingPeriod, string> = {
  monthly: "/month",
  semiannual: "/6 months",
  yearly: "/year",
};

export function PricingTable() {
  const router = useRouter();
  const [cfg, setCfg] = React.useState<BillingConfig | null>(null);
  const [offline, setOffline] = React.useState(false);
  // Suggested first: 6 months at 15% off, on both paid tiers — a middle
  // ground between a monthly trial and a full year paid up front.
  const [period, setPeriod] = React.useState<BillingPeriod>("semiannual");
  const [signedIn, setSignedIn] = React.useState<boolean | undefined>();
  // What the account already has — only meaningful once signed in.
  const [me, setMe] = React.useState<BillingSummary | null>(null);

  // Which plan card has its payment-method panel open.
  const [openPlan, setOpenPlan] = React.useState<string | null>(null);
  const [coin, setCoin] = React.useState("");
  const [coins, setCoins] = React.useState<string[]>([]);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    api.billingConfig()
      .then((c) => alive && setCfg(c))
      .catch(() => alive && setOffline(true));
    api.session()
      .then((s) => {
        if (!alive) return;
        setSignedIn(s.authenticated);
        if (s.authenticated) {
          // The break-glass operator has no billing account; a 400 here is
          // not an error worth showing, just "nothing to mark".
          api.billingMe().then((m) => alive && setMe(m)).catch(() => {});
        }
      })
      .catch(() => alive && setSignedIn(false));
    return () => {
      alive = false;
    };
  }, []);

  // Only worth a round trip once we know the crypto rail is actually on.
  React.useEffect(() => {
    if (!cfg?.crypto_enabled) return;
    let alive = true;
    api.cryptoCurrencies()
      .then((r) => alive && setCoins(r.currencies))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [cfg?.crypto_enabled]);

  const plans: BillingPlan[] = cfg?.plans ?? FALLBACK_PLANS;
  const currency = cfg?.currency ?? "usd";
  const canBuy = Boolean(cfg?.enabled && cfg.providers.length > 0);

  // A live card subscription changes in place. `sub` is what it is on today.
  const subscriber = Boolean(me?.can_change_plan && me?.subscription);
  // Bought in the Android app: Google bills and renews it, so the website
  // sells nothing on top of it (the API refuses that too).
  const playSubscriber = me?.subscription?.provider === "google_play";
  const sub = subscriber ? me!.subscription! : null;
  const isCurrent = (plan: BillingPlan) =>
    sub ? sub.plan === plan.key && sub.period === period : me?.tier === plan.key;

  async function begin(plan: BillingPlan, provider: BillingProvider) {
    if (!signedIn) {
      // Checkout needs an account to attach the entitlement to. Come back
      // here afterwards rather than dumping them on the console.
      router.push(`/signup?next=${encodeURIComponent("/pricing")}`);
      return;
    }
    setBusy(`${plan.key}:${provider}`);
    setError(null);
    try {
      const res = await api.checkout(
        plan.key, period, provider, provider === "nowpayments" ? coin : ""
      );
      if (res.changed || !res.url) {
        // Applied already — the card on file was charged (or, for a resumed
        // cancellation, nothing was). Nothing to redirect to.
        router.push(`/billing/success?changed=${res.action ?? "changed"}`);
        return;
      }
      window.location.href = res.url;
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not start checkout. Try again."
      );
      setBusy(null);
    }
  }

  return (
    <>
      {/* ── billing period ── */}
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 28 }}>
        <div
          role="group"
          aria-label="Billing period"
          style={{
            display: "inline-flex",
            gap: 2,
            padding: 3,
            borderRadius: 10,
            border: "1px solid var(--color-divider)",
            background: "var(--color-surface)",
          }}
        >
          {(["monthly", "semiannual", "yearly"] as BillingPeriod[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPeriod(p)}
              aria-pressed={period === p}
              className={`btn ${period === p ? "btn-solid" : "btn-ghost"}`}
              style={{ fontSize: 13, padding: "7px 16px", borderRadius: 8 }}
            >
              {PERIOD_LABEL[p]}
              {p === "semiannual" && (
                <span
                  className="tag tag-accent"
                  style={{ fontSize: 9, marginLeft: 8 }}
                >
                  Save 15%
                </span>
              )}
              {p === "yearly" && (
                <span
                  className="tag tag-accent"
                  style={{ fontSize: 9, marginLeft: 8 }}
                >
                  2 months free
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {offline && (
        <p
          style={{
            textAlign: "center",
            fontSize: 13,
            color: "var(--color-warn)",
            margin: "0 0 20px",
          }}
        >
          Live pricing is unavailable right now — the figures below may be out of
          date. <a href={`mailto:${CONTACT_EMAIL}`}>Email the operator</a> to buy access.
        </p>
      )}

      {playSubscriber && (
        <p style={{ textAlign: "center", fontSize: 13.5, color: "var(--color-neutral-400)", margin: "0 0 20px" }}>
          Your plan is billed through Google Play. Change or cancel it in the DECINT
          app, or in the Play Store under Subscriptions.
        </p>
      )}

      {error && (
        <p
          style={{
            textAlign: "center",
            fontSize: 13,
            color: "var(--color-bad)",
            margin: "0 0 20px",
          }}
        >
          {error}
        </p>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 20,
          alignItems: "stretch",
        }}
      >
        {plans.map((p) => {
          const cents =
            period === "monthly" ? p.monthly_cents
            : period === "semiannual" ? p.semiannual_cents
            : p.yearly_cents;
          const open = openPlan === p.key;
          return (
            <div
              key={p.key}
              className="card"
              style={{
                padding: "28px 26px",
                gap: 0,
                borderColor: p.featured ? "var(--color-accent)" : undefined,
                background: p.featured
                  ? "linear-gradient(180deg, color-mix(in srgb, var(--color-accent) 8%, var(--color-surface)), var(--color-surface))"
                  : undefined,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <h2 style={{ fontSize: 18, margin: 0 }}>{p.name}</h2>
                {isCurrent(p) ? (
                  <span className="tag tag-ok" style={{ fontSize: 10 }}>
                    {sub?.cancel_at_period_end ? "Current — cancelling" : "Current plan"}
                  </span>
                ) : p.featured ? (
                  <span className="tag tag-accent" style={{ fontSize: 10 }}>
                    Most used
                  </span>
                ) : null}
              </div>

              <div style={{ display: "flex", alignItems: "baseline", gap: 5, margin: "16px 0 6px" }}>
                <span
                  style={{
                    fontSize: 36,
                    fontWeight: 600,
                    letterSpacing: "-0.03em",
                    color: "var(--color-text)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {p.contact ? "Let's talk" : p.is_free ? "Free" : money(cents, currency)}
                </span>
                {!p.contact && !p.is_free && (
                  <span style={{ fontSize: 14, color: "var(--color-neutral-500)" }}>
                    {PERIOD_SUFFIX[period]}
                  </span>
                )}
              </div>
              <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: "0 0 20px", lineHeight: 1.55 }}>
                {p.blurb}
              </p>

              <ul
                style={{
                  margin: 0,
                  padding: "20px 0 0",
                  borderTop: "1px solid var(--color-divider)",
                  listStyle: "none",
                  display: "flex",
                  flexDirection: "column",
                  gap: 9,
                  flex: 1,
                }}
              >
                {p.features.map((f) => (
                  <li key={f} style={{ display: "flex", gap: 10, fontSize: 13.5, color: "var(--color-neutral-300)" }}>
                    <Check size={14} style={{ color: "var(--color-accent)", flex: "none", marginTop: 3 }} />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              <div style={{ marginTop: 24 }}>
                {p.contact ? (
                  <a
                    href={`mailto:${CONTACT_EMAIL}?subject=DECINT%20custom%20access`}
                    className="btn btn-primary btn-block"
                  >
                    Email the operator
                  </a>
                ) : p.is_free ? (
                  <a href="/signup" className={`btn btn-secondary btn-block`}>
                    Create an account
                  </a>
                ) : playSubscriber ? (
                  <button type="button" className="btn btn-secondary btn-block" disabled>
                    {me?.tier === p.key ? "Your current plan" : "Change it in the app"}
                  </button>
                ) : !canBuy ? (
                  <a
                    href={`mailto:${CONTACT_EMAIL}?subject=DECINT%20${encodeURIComponent(p.name)}%20access`}
                    className="btn btn-primary btn-block"
                  >
                    Email the operator
                  </a>
                ) : subscriber && isCurrent(p) && !sub!.cancel_at_period_end ? (
                  <button type="button" className="btn btn-secondary btn-block" disabled>
                    Your current plan
                  </button>
                ) : subscriber && isCurrent(p) ? (
                  // The plan is cancelling at period end; picking it again
                  // keeps it. Nothing is charged for that.
                  <button
                    type="button"
                    className="btn btn-solid btn-block"
                    disabled={busy !== null}
                    onClick={() => begin(p, "stripe")}
                  >
                    {busy === `${p.key}:stripe` ? "Resuming…" : "Keep this plan"}
                  </button>
                ) : subscriber ? (
                  <SwitchPanel
                    plan={p}
                    period={period}
                    currentPlan={sub!.plan}
                    plans={plans}
                    busy={busy}
                    onPick={begin}
                  />
                ) : !open ? (
                  <button
                    type="button"
                    onClick={() => { setOpenPlan(p.key); setError(null); }}
                    className={`btn btn-block ${p.featured ? "btn-solid" : "btn-primary"}`}
                  >
                    {me && me.tier !== (cfg?.free_tier ?? "free") && !isCurrent(p)
                      ? "Change to this plan"
                      : isCurrent(p) ? "Extend" : "Get access"}
                  </button>
                ) : (
                  <PayPanel
                    plan={p}
                    cfg={cfg!}
                    period={period}
                    coins={coins}
                    coin={coin}
                    setCoin={setCoin}
                    busy={busy}
                    onPick={begin}
                    onCancel={() => setOpenPlan(null)}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>

      <PaymentMarks cfg={cfg} />
    </>
  );
}

/**
 * What this deployment actually accepts, drawn from the live config.
 *
 * Each mark is gated on its own rail: with no STRIPE_SECRET_KEY the card mark
 * is absent rather than greyed out, because a payment badge on a page that
 * cannot take that payment is a promise the checkout will break. Network and
 * processor logos are set in text — their real artwork is trademarked, with
 * usage rules an approximation would violate.
 */
function PaymentMarks({ cfg }: { cfg: BillingConfig | null }) {
  const card = Boolean(cfg?.card_enabled);
  const crypto = Boolean(cfg?.crypto_enabled);
  if (!card && !crypto) return null;

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        marginTop: 32,
        paddingTop: 24,
        borderTop: "1px solid var(--color-divider)",
      }}
    >
      {card && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 9,
            padding: "8px 14px",
            borderRadius: 9,
            border: "1px solid var(--color-divider)",
            background: "var(--color-surface)",
            fontSize: 12.5,
            color: "var(--color-neutral-400)",
          }}
        >
          <CreditCard size={19} style={{ color: "var(--color-accent)", flex: "none" }} />
          Visa · Mastercard · Amex · Apple&nbsp;Pay · Google&nbsp;Pay
        </span>
      )}

      {crypto && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 9,
            padding: "8px 14px",
            borderRadius: 9,
            border: "1px solid var(--color-divider)",
            background: "var(--color-surface)",
            fontSize: 12.5,
            color: "var(--color-neutral-400)",
          }}
        >
          <Coin size={19} style={{ color: "var(--color-accent)", flex: "none" }} />
          Bitcoin · Ethereum · Monero · USDT&nbsp;+&nbsp;300 more
        </span>
      )}

      {card && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 7,
            fontSize: 12,
            color: "var(--color-neutral-500)",
            width: "100%",
            justifyContent: "center",
            marginTop: 2,
          }}
        >
          <Lock size={13} style={{ flex: "none" }} />
          Card details go straight to Stripe and never reach this server.
        </span>
      )}
    </div>
  );
}

/**
 * What a card subscriber sees instead of the rails: one button that moves
 * their existing subscription to this plan. Upgrades are charged pro rata to
 * the card on file the moment it clears; downgrades credit the next renewal.
 * Crypto is deliberately absent — a prepaid block under a subscription that
 * keeps renewing over it is not a purchase anyone means to make, and the API
 * refuses it anyway.
 */
function SwitchPanel({
  plan, period, currentPlan, plans, busy, onPick,
}: {
  plan: BillingPlan;
  period: BillingPeriod;
  currentPlan: string;
  plans: BillingPlan[];
  busy: string | null;
  onPick: (plan: BillingPlan, provider: BillingProvider) => void;
}) {
  const rank = (key: string) => plans.findIndex((p) => p.key === key);
  const upgrade = rank(plan.key) > rank(currentPlan);
  const samePlan = plan.key === currentPlan;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <button
        type="button"
        className={`btn btn-block ${plan.featured ? "btn-solid" : "btn-primary"}`}
        disabled={busy !== null}
        onClick={() => onPick(plan, "stripe")}
      >
        <CreditCard size={16} style={{ flex: "none", marginRight: 8, verticalAlign: "-3px" }} />
        {busy === `${plan.key}:stripe`
          ? "Applying…"
          : samePlan
            ? `Switch to ${period}`
            : upgrade ? `Upgrade to ${plan.name}` : `Move to ${plan.name}`}
      </button>
      <p style={{ fontSize: 11.5, color: "var(--color-neutral-500)", margin: 0, lineHeight: 1.5 }}>
        {samePlan
          ? "Applied to your existing subscription today. Unused time on the current period is credited, the new period is charged to your card on file, and it renews on the new schedule."
          : upgrade
            ? "Applied today: only the difference for the rest of the current period is charged to your card on file. Renewals continue at the new price."
            : "Applied today: the unused part of what you paid is credited against your next renewal. Nothing is charged now."}
      </p>
    </div>
  );
}

/** The two rails, offered side by side once a plan is chosen. */
function PayPanel({
  plan, cfg, period, coins, coin, setCoin, busy, onPick, onCancel,
}: {
  plan: BillingPlan;
  cfg: BillingConfig;
  period: BillingPeriod;
  coins: string[];
  coin: string;
  setCoin: (c: string) => void;
  busy: string | null;
  onPick: (plan: BillingPlan, provider: BillingProvider) => void;
  onCancel: () => void;
}) {
  const months = PERIOD_MONTHS[period];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {cfg.card_enabled && (
        <button
          type="button"
          className="btn btn-solid btn-block"
          disabled={busy !== null}
          onClick={() => onPick(plan, "stripe")}
        >
          <CreditCard size={16} style={{ flex: "none", marginRight: 8, verticalAlign: "-3px" }} />
          {busy === `${plan.key}:stripe` ? "Opening checkout…" : "Pay by card"}
        </button>
      )}

      {cfg.crypto_enabled && (
        <>
          {coins.length > 0 && (
            <select
              className="input"
              value={coin}
              onChange={(e) => setCoin(e.target.value)}
              aria-label="Cryptocurrency"
              style={{ fontSize: 13 }}
            >
              <option value="">Choose the coin at checkout</option>
              {coins.map((c) => (
                <option key={c} value={c}>{coinLabel(c)}</option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="btn btn-primary btn-block"
            disabled={busy !== null}
            onClick={() => onPick(plan, "nowpayments")}
          >
            <Coin size={16} style={{ flex: "none", marginRight: 8, verticalAlign: "-3px" }} />
            {busy === `${plan.key}:nowpayments` ? "Opening invoice…" : "Pay with crypto"}
          </button>
          {/* The one thing this flow must not leave ambiguous. Nothing on-chain
              can pull a renewal, so crypto buys a fixed block of time and the
              customer comes back to buy more. Saying so here is cheaper than
              the support ticket that follows from not saying it. */}
          <p
            style={{
              fontSize: 11.5,
              color: "var(--color-neutral-500)",
              margin: 0,
              lineHeight: 1.5,
            }}
          >
            Crypto buys {months} month{months === 1 ? "" : "s"} up front and does
            not auto-renew — we&apos;ll email you before it runs out. Card
            payments renew automatically and can be cancelled any time.
          </p>
        </>
      )}

      {!cfg.card_enabled && !cfg.crypto_enabled && (
        <p style={{ fontSize: 12.5, color: "var(--color-warn)", margin: 0 }}>
          No payment method is configured on this deployment.
        </p>
      )}

      <button
        type="button"
        className="btn btn-ghost"
        onClick={onCancel}
        style={{ fontSize: 12, alignSelf: "center" }}
      >
        Back
      </button>
    </div>
  );
}

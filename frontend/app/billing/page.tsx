"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import type { BillingConfig, BillingSummary, OrderStatus } from "@/lib/types";

/**
 * What this account has paid for, and how to change it.
 *
 * The distinction the page is built around: a card subscription renews itself
 * and is managed in Stripe's portal, while crypto is a block of prepaid time
 * that simply runs out. Showing both as "your plan" without saying which one
 * you have is how someone discovers the difference by losing access.
 */

const money = (cents: number, currency: string) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
    maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);

const date = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  }) : "—";

const STATUS_TAG: Record<OrderStatus, string> = {
  paid: "tag-ok",
  pending: "tag-warn",
  failed: "tag-bad",
  expired: "tag-neutral",
  refunded: "tag-neutral",
};

export default function BillingPage() {
  const [me, setMe] = React.useState<BillingSummary | null>(null);
  const [cfg, setCfg] = React.useState<BillingConfig | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    api.billingMe().then(setMe).catch((e) =>
      setError(e instanceof ApiError ? e.message : "Could not load your billing.")
    );
  }, []);

  React.useEffect(() => {
    load();
    api.billingConfig().then(setCfg).catch(() => {});
  }, [load]);

  async function openPortal() {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.billingPortal();
      window.location.href = url;
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not open the billing portal."
      );
      setBusy(false);
    }
  }

  const onFree = me && cfg && me.tier === cfg.free_tier;
  const expiring =
    me && !me.renews && me.days_left !== null && me.days_left <= 7;
  // Stripe is retrying a failed renewal. Access stays on through the retry
  // window, but the customer has to fix the card or it ends — which is not
  // something to find out from the lapse email.
  const pastDue = me?.subscription?.status === "past_due";

  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />

      <section style={{ maxWidth: 860, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">Billing</p>
        <h1 style={{ fontSize: "clamp(28px, 5vw, 40px)", margin: "0 0 14px" }}>
          Your plan.
        </h1>

        {error && (
          <p style={{ fontSize: 13.5, color: "var(--color-bad)" }}>{error}</p>
        )}

        {!me ? (
          <p style={{ fontSize: 14, color: "var(--color-neutral-500)" }}>Loading…</p>
        ) : (
          <>
            <div className="card" style={{ padding: "26px 24px", gap: 0, marginTop: 24 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <h2 style={{ fontSize: 22, margin: 0 }}>{me.plan_name}</h2>
                {me.renews ? (
                  <span className="tag tag-ok" style={{ fontSize: 10 }}>Renews automatically</span>
                ) : onFree ? (
                  <span className="tag tag-neutral" style={{ fontSize: 10 }}>No paid plan</span>
                ) : (
                  <span className="tag tag-outline" style={{ fontSize: 10 }}>Prepaid — does not renew</span>
                )}
                {me.subscription?.cancel_at_period_end && (
                  <span className="tag tag-warn" style={{ fontSize: 10 }}>Cancels at period end</span>
                )}
                {me.usage.is_free && me.usage.remaining === 0 && (
              <p className="app-only" style={{ fontSize: 13.5, color: "var(--color-warn)", margin: "16px 0 0", lineHeight: 1.6 }}>
                Your {me.usage.limit} free searches are used up.
              </p>
            )}
            {me.usage.is_free && me.usage.remaining === 0 && (
              <p className="web-only" style={{ fontSize: 13.5, color: "var(--color-warn)", margin: "16px 0 0", lineHeight: 1.6 }}>
                Your {me.usage.limit} free searches are used up. Pick a plan on{" "}
                <Link href="/pricing">pricing</Link> to keep searching — card
                payments activate instantly.
              </p>
            )}

            {pastDue && (
                  <span className="tag tag-bad" style={{ fontSize: 10 }}>Payment failed</span>
                )}
              </div>

              <dl
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                  gap: 18,
                  margin: "22px 0 0",
                  paddingTop: 20,
                  borderTop: "1px solid var(--color-divider)",
                }}
              >
                <Stat
                  label={me.usage.window === "lifetime" ? "Free searches" : "Searches this month"}
                  value={
                    me.usage.limit === null
                      ? "Unmetered"
                      : `${me.usage.used.toLocaleString()} of ${me.usage.limit.toLocaleString()}`
                  }
                  warn={me.usage.remaining === 0}
                />
                <Stat
                  label={me.renews ? "Renews on" : "Access until"}
                  value={onFree ? "—" : date(me.expires_at)}
                />
                <Stat
                  label="Days remaining"
                  value={me.days_left === null ? (onFree ? "—" : "No expiry") : String(me.days_left)}
                  warn={Boolean(expiring)}
                />
                <Stat label="Paid via" value={sourceLabel(me.source)} />
              </dl>
            </div>

            {expiring && (
              <p className="app-only" style={{ fontSize: 13.5, color: "var(--color-warn)", margin: "16px 0 0", lineHeight: 1.6 }}>
                Your prepaid access ends in {me.days_left} day{me.days_left === 1 ? "" : "s"}.
              </p>
            )}
            {expiring && (
              <p
                className="web-only"
                style={{
                  fontSize: 13.5,
                  color: "var(--color-warn)",
                  margin: "16px 0 0",
                  lineHeight: 1.6,
                }}
              >
                Your prepaid access ends in {me.days_left} day
                {me.days_left === 1 ? "" : "s"}. Buy another period from{" "}
                <Link href="/pricing">pricing</Link> to keep it running — crypto
                payments cannot renew themselves.
              </p>
            )}

            {pastDue && (
              <p className="app-only" style={{ fontSize: 13.5, color: "var(--color-bad)", margin: "16px 0 0", lineHeight: 1.6 }}>
                Your last renewal could not be charged. Stripe will retry for a
                few days and your access stays on meanwhile.
              </p>
            )}
            {pastDue && (
              <p
                className="web-only"
                style={{
                  fontSize: 13.5,
                  color: "var(--color-bad)",
                  margin: "16px 0 0",
                  lineHeight: 1.6,
                }}
              >
                Your last renewal could not be charged. Stripe will retry for a
                few days and your access stays on meanwhile — update the card
                under <strong>Manage card &amp; invoices</strong> so the retry
                succeeds, or the subscription ends and the account drops to the
                free tier.
              </p>
            )}

            <div style={{ display: "flex", gap: 12, marginTop: 22, flexWrap: "wrap" }}>
              <Link href="/pricing" className="btn btn-primary web-only">
                {onFree ? "Choose a plan" : me.can_change_plan ? "Switch plan" : "Change or extend plan"}
              </Link>
              {me.subscription?.provider === "stripe" && (
                <button
                  type="button"
                  className="btn btn-secondary web-only"
                  onClick={openPortal}
                  disabled={busy}
                >
                  {busy ? "Opening…" : "Manage card & invoices"}
                </button>
              )}
              <Link href="/console" className="btn btn-ghost">
                Back to the console
              </Link>
            </div>

            <h3 style={{ fontSize: 16, margin: "44px 0 12px" }}>Payment history</h3>
            {me.orders.length === 0 ? (
              <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)" }}>
                Nothing yet.
              </p>
            ) : (
              <div className="card" style={{ padding: 0, gap: 0, overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--color-neutral-500)" }}>
                      {["Date", "Plan", "Period", "Amount", "Method", "Status"].map((h) => (
                        <th key={h} style={{ padding: "12px 16px", fontWeight: 500, whiteSpace: "nowrap" }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {me.orders.map((o) => (
                      <tr key={o.id} style={{ borderTop: "1px solid var(--color-divider)" }}>
                        <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{date(o.paid_at ?? o.created_at)}</td>
                        <td style={{ padding: "12px 16px", textTransform: "capitalize" }}>{o.plan}</td>
                        <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{o.months} mo</td>
                        <td style={{ padding: "12px 16px", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                          {money(o.amount_cents, o.currency)}
                        </td>
                        <td style={{ padding: "12px 16px" }}>
                          {o.provider === "stripe"
                            ? "Card"
                            : o.pay_currency
                              ? o.pay_currency.toUpperCase()
                              : "Crypto"}
                        </td>
                        <td style={{ padding: "12px 16px" }}>
                          <span className={`tag ${STATUS_TAG[o.status] ?? "tag-neutral"}`} style={{ fontSize: 10 }}>
                            {o.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      <div style={{ height: 76 }} />
      <SiteFooter />
    </main>
  );
}

function sourceLabel(source: string): string {
  if (source === "stripe") return "Card";
  if (source === "nowpayments") return "Crypto";
  if (source === "manual") return "Operator";
  if (source === "lapsed") return "Expired";
  return source;
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <dt style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-600)" }}>
        {label}
      </dt>
      <dd
        style={{
          margin: "6px 0 0",
          fontSize: 17,
          color: warn ? "var(--color-warn)" : "var(--color-text)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </dd>
    </div>
  );
}

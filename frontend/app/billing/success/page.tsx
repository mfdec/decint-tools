"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import type { BillingConfig, BillingSummary } from "@/lib/types";

/**
 * Where a processor returns the customer.
 *
 * The redirect proves the customer finished the *form*, not that money moved —
 * only the webhook proves that, and it may arrive after this page loads. So
 * the page polls its own account rather than asserting success, and never
 * upgrades anything itself.
 *
 * The two rails differ in how long that wait is: a card authorises in seconds,
 * while a BTC payment waits on block confirmations and can legitimately take
 * half an hour. The copy has to set that expectation or a normal confirmation
 * time reads as a failed payment.
 *
 * `?changed=` is the third arrival: a card subscriber who switched plans. That
 * was applied before the redirect — the card on file was charged (or, for a
 * resumed cancellation, nothing was) — so there is nothing to wait for and the
 * page says what happened instead of "confirming".
 */

const POLL_MS = 2500;
const POLL_LIMIT = 24; // ~1 minute

function SuccessBody() {
  const params = useSearchParams();
  const isCrypto = params.get("provider") === "nowpayments";
  const changed = params.get("changed"); // "changed" | "resumed" | null

  const [me, setMe] = React.useState<BillingSummary | null>(null);
  const [freeTier, setFreeTier] = React.useState<string>("free");
  const [ticks, setTicks] = React.useState(0);

  React.useEffect(() => {
    api.billingConfig()
      .then((c: BillingConfig) => setFreeTier(c.free_tier))
      .catch(() => {});
  }, []);

  const confirmed = Boolean(me && me.tier !== freeTier);

  React.useEffect(() => {
    if (confirmed || ticks >= POLL_LIMIT) return;
    let alive = true;
    const t = setTimeout(() => {
      api.billingMe()
        .then((s) => alive && setMe(s))
        .catch(() => {})
        .finally(() => alive && setTicks((n) => n + 1));
    }, ticks === 0 ? 0 : POLL_MS);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [ticks, confirmed]);

  const stillWaiting = !confirmed && ticks >= POLL_LIMIT;

  return (
    <section style={{ maxWidth: 640, margin: "0 auto", padding: "96px 24px 8px" }}>
      <p className="eyebrow">
        {changed ? "Plan updated" : confirmed ? "Payment confirmed" : "Payment received"}
      </p>
      <h1 style={{ fontSize: "clamp(28px, 5vw, 40px)", margin: "0 0 16px" }}>
        {confirmed ? `You're on ${me!.plan_name}.` : changed ? "Updating your plan…" : "Confirming your payment…"}
      </h1>

      {confirmed && changed === "resumed" ? (
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-400)", lineHeight: 1.6 }}>
          Your subscription is no longer set to cancel. Nothing was charged today —
          it simply renews as before, on {me!.expires_at ? new Date(me!.expires_at).toLocaleDateString() : "its usual date"}.
        </p>
      ) : confirmed && changed ? (
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-400)", lineHeight: 1.6 }}>
          The change was applied to your existing subscription and settled against
          your card on file — an upgrade is charged pro rata, a downgrade credits
          your next renewal. The exact amount is in your payment history.
        </p>
      ) : confirmed ? (
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-400)", lineHeight: 1.6 }}>
          {me!.renews
            ? "Your subscription is active and renews automatically. You can change or cancel it any time."
            : `Access runs until ${me!.expires_at ? new Date(me!.expires_at).toLocaleDateString() : "further notice"}. Crypto payments don't renew themselves — come back before then to extend.`}
        </p>
      ) : stillWaiting ? (
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-400)", lineHeight: 1.6 }}>
          {isCrypto
            ? "Your transaction is still waiting on network confirmations. This is normal for Bitcoin and can take up to an hour — your plan activates automatically the moment it settles, and nothing else is needed from you."
            : "Your payment went through, but confirmation hasn't reached us yet. It usually lands within a minute. Refresh your billing page shortly."}
        </p>
      ) : (
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-400)", lineHeight: 1.6 }}>
          {isCrypto
            ? "Waiting for the transaction to confirm on-chain. Bitcoin usually takes a few blocks; you can safely close this page."
            : "Just a moment while the processor confirms it."}
        </p>
      )}

      <div style={{ display: "flex", gap: 12, marginTop: 28, flexWrap: "wrap" }}>
        <Link href="/console" className="btn btn-solid">Open the console</Link>
        <Link href="/billing" className="btn btn-secondary">Billing details</Link>
      </div>
    </section>
  );
}

export default function BillingSuccessPage() {
  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />
      {/* useSearchParams needs a Suspense boundary to keep this route from
          opting the whole segment out of static rendering. */}
      <React.Suspense fallback={<div style={{ height: 300 }} />}>
        <SuccessBody />
      </React.Suspense>
      <div style={{ height: 96 }} />
      <SiteFooter />
    </main>
  );
}

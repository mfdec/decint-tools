"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { Check } from "@/components/icons";
import {
  playAvailable, playManageUrl, playProducts, playSubscribe, type PlayOffer,
} from "@/lib/playBilling";
import type {
  BillingConfig, BillingPeriod, BillingPlan, BillingSummary, PlayAccount,
} from "@/lib/types";

/**
 * The pricing table inside the Android app, where Google Play's payments policy
 * applies: plans are sold through Google Play Billing, at the prices Google
 * shows for the person's country. The website's card and crypto rails and
 * USD prices never appear here.
 *
 * The free tier is presented as what it is, a trial: a few searches to judge
 * the console by, after which the person subscribes.
 *
 * A purchase is only reflected once the server has checked its token with
 * Google (POST /billing/play/verify). This component never grants anything.
 */

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

type Load = "loading" | "ready" | "unavailable";

export function PlayPricing() {
  const [load, setLoad] = React.useState<Load>("loading");
  const [cfg, setCfg] = React.useState<BillingConfig | null>(null);
  const [signedIn, setSignedIn] = React.useState(false);
  const [me, setMe] = React.useState<BillingSummary | null>(null);
  const [acct, setAcct] = React.useState<PlayAccount | null>(null);
  const [offers, setOffers] = React.useState<PlayOffer[]>([]);
  const [period, setPeriod] = React.useState<BillingPeriod>("monthly");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  // The outcome is shown above the plans, but the person is scrolled down at
  // the button they pressed; bring it to them.
  const outcome = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (notice || error) outcome.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [notice, error]);

  React.useEffect(() => {
    let alive = true;
    (async () => {
      if (!playAvailable()) return alive && setLoad("unavailable");
      const config = await api.billingConfig().catch(() => null);
      if (!config?.play_enabled) return alive && setLoad("unavailable");
      const session = await api.session().catch(() => null);
      const authed = Boolean(session?.authenticated);
      const [summary, account] = authed
        ? await Promise.all([api.billingMe().catch(() => null), api.playAccount().catch(() => null)])
        : [null, null];
      let priced: PlayOffer[] = [];
      try {
        priced = await playProducts(config.plans.filter((p) => p.purchasable).map((p) => p.key));
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Google Play prices couldn't be loaded.");
      }
      if (!alive) return;
      setCfg(config);
      setSignedIn(authed);
      setMe(summary);
      setAcct(account);
      setOffers(priced);
      if (account?.current) setPeriod(account.current.base_plan_id);
      setLoad("ready");
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (load === "loading") {
    return <p style={{ textAlign: "center", fontSize: 14, color: "var(--color-neutral-500)" }}>Loading plans…</p>;
  }
  if (load === "unavailable" || !cfg) {
    return (
      <p style={{ textAlign: "center", fontSize: 14, color: "var(--color-neutral-400)" }}>
        Subscriptions aren&apos;t available in this version of the app.
      </p>
    );
  }

  const trial = cfg.plans.find((p) => p.is_free);
  const paid = cfg.plans.filter((p) => p.purchasable);
  const periods = (["monthly", "semiannual", "yearly"] as BillingPeriod[]).filter((p) =>
    offers.some((o) => o.basePlanId === p),
  );
  const offerFor = (plan: BillingPlan) =>
    offers.find((o) => o.productId === plan.key && o.basePlanId === period);
  const current = acct?.current ?? null;
  const elsewhere = Boolean(acct?.billed_elsewhere);

  async function subscribe(plan: BillingPlan) {
    setBusy(plan.key);
    setError(null);
    setNotice(null);
    try {
      const account = acct ?? (await api.playAccount());
      const result = await playSubscribe({
        productId: plan.key,
        basePlanId: period,
        accountRef: account.account_ref,
        replaceToken: account.current?.purchase_token,
      });
      if (result.status === "purchased" && result.purchaseToken) {
        const verified = await api.playVerify(result.purchaseToken);
        setMe(verified.summary);
        setAcct(await api.playAccount().catch(() => account));
        setNotice(`You're on ${plan.name} now. It's ready to use in the console.`);
      } else if (result.status === "pending") {
        setNotice("Google Play is waiting for your payment to clear. Your plan starts as soon as it does.");
      } else if (result.status === "owned") {
        setNotice("Your Google account already has this subscription. It will show here within a few minutes.");
      } else if (result.status === "error") {
        setError(result.error || "Google Play couldn't complete the purchase.");
      }
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "The purchase couldn't be completed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {periods.length > 1 && (
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 24 }}>
          <div
            role="group"
            aria-label="Billing period"
            style={{
              display: "inline-flex", gap: 2, padding: 3, borderRadius: 10,
              border: "1px solid var(--color-divider)", background: "var(--color-surface)",
            }}
          >
            {periods.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPeriod(p)}
                aria-pressed={period === p}
                className={`btn ${period === p ? "btn-solid" : "btn-ghost"}`}
                style={{ fontSize: 13, padding: "7px 14px", borderRadius: 8 }}
              >
                {PERIOD_LABEL[p]}
              </button>
            ))}
          </div>
        </div>
      )}

      {elsewhere && (
        <p style={{ textAlign: "center", fontSize: 13.5, color: "var(--color-neutral-400)", margin: "0 0 20px" }}>
          Your {me?.plan_name ?? "current"} plan is billed outside the app, so there&apos;s nothing to buy here.
        </p>
      )}
      <div ref={outcome} />
      {notice && (
        <p role="status" className="tag tag-ok" style={{ display: "block", textAlign: "center", fontSize: 13, margin: "0 0 20px", whiteSpace: "normal", padding: "10px 12px" }}>
          {notice} <Link href="/console">Open the console</Link>
        </p>
      )}
      {error && (
        <p role="alert" style={{ textAlign: "center", fontSize: 13, color: "var(--color-bad)", margin: "0 0 20px" }}>
          {error}
        </p>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 20, alignItems: "stretch" }}>
        {trial && (
          <Card
            name="Trial"
            price={`${trial.quota ?? 0} searches`}
            suffix="free"
            blurb="Try every tool before you subscribe."
            features={trial.features.filter((f) => !/free searches/i.test(f))}
            tag={signedIn && me?.tier === trial.key ? "Your trial" : undefined}
          >
            {!signedIn ? (
              <Link href="/signup?next=/pricing" className="btn btn-secondary btn-block">Create an account</Link>
            ) : me?.tier === trial.key && me.usage.limit !== null ? (
              <p style={{ fontSize: 13, color: "var(--color-neutral-400)", margin: 0, textAlign: "center" }}>
                {me.usage.remaining} of {me.usage.limit} searches left
              </p>
            ) : null}
          </Card>
        )}

        {paid.map((plan) => {
          const offer = offerFor(plan);
          const isCurrent = Boolean(current && current.product_id === plan.key && current.base_plan_id === period);
          return (
            <Card
              key={plan.key}
              name={plan.name}
              price={offer?.price ?? "—"}
              suffix={offer ? PERIOD_SUFFIX[period] : ""}
              blurb={plan.blurb}
              features={plan.features}
              featured={plan.featured}
              tag={isCurrent ? "Current plan" : plan.featured ? "Most used" : undefined}
            >
              {!signedIn ? (
                <Link href="/signup?next=/pricing" className="btn btn-primary btn-block">Create an account</Link>
              ) : elsewhere ? (
                <button type="button" className="btn btn-secondary btn-block" disabled>Billed outside the app</button>
              ) : isCurrent ? (
                <a href={playManageUrl(acct!.package_name, plan.key)} className="btn btn-secondary btn-block">
                  Manage in Google Play
                </a>
              ) : !offer ? (
                <button type="button" className="btn btn-secondary btn-block" disabled>Not available</button>
              ) : (
                <button
                  type="button"
                  className={`btn btn-block ${plan.featured ? "btn-solid" : "btn-primary"}`}
                  disabled={busy !== null}
                  onClick={() => subscribe(plan)}
                >
                  {busy === plan.key ? "Opening Google Play…" : current ? "Switch to this plan" : "Subscribe"}
                </button>
              )}
            </Card>
          );
        })}
      </div>

      <p style={{ fontSize: 12.5, color: "var(--color-neutral-500)", margin: "24px auto 0", maxWidth: "62ch", lineHeight: 1.6, textAlign: "center" }}>
        Subscriptions are billed through Google Play at the price shown and renew
        automatically until you cancel. Cancel any time in Google Play under
        Subscriptions; your plan stays active until the end of the period you paid for.
        {current ? " Switching plans credits the time left on your current one." : ""}
      </p>
    </>
  );
}

function Card({
  name, price, suffix, blurb, features, featured, tag, children,
}: {
  name: string;
  price: string;
  suffix: string;
  blurb: string;
  features: string[];
  featured?: boolean;
  tag?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="card"
      style={{
        padding: "28px 26px",
        gap: 0,
        borderColor: featured ? "var(--color-accent)" : undefined,
        background: featured
          ? "linear-gradient(180deg, color-mix(in srgb, var(--color-accent) 8%, var(--color-surface)), var(--color-surface))"
          : undefined,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 18, margin: 0 }}>{name}</h2>
        {tag && (
          <span className={`tag ${tag === "Most used" ? "tag-accent" : "tag-ok"}`} style={{ fontSize: 10 }}>
            {tag}
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 5, margin: "16px 0 6px" }}>
        <span style={{ fontSize: 32, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--color-text)", fontVariantNumeric: "tabular-nums" }}>
          {price}
        </span>
        {suffix && <span style={{ fontSize: 14, color: "var(--color-neutral-500)" }}>{suffix}</span>}
      </div>
      <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: "0 0 20px", lineHeight: 1.55 }}>{blurb}</p>
      <ul
        style={{
          margin: 0, padding: "20px 0 0", borderTop: "1px solid var(--color-divider)", listStyle: "none",
          display: "flex", flexDirection: "column", gap: 9, flex: 1,
        }}
      >
        {features.map((f) => (
          <li key={f} style={{ display: "flex", gap: 10, fontSize: 13.5, color: "var(--color-neutral-300)" }}>
            <Check size={14} style={{ color: "var(--color-accent)", flex: "none", marginTop: 3 }} />
            <span>{f}</span>
          </li>
        ))}
      </ul>
      <div style={{ marginTop: 24 }}>{children}</div>
    </div>
  );
}

"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { AppDef, AppKey } from "@/lib/apps";
import type { BillingSummary, CurrentUser, HealthResponse } from "@/lib/types";
import { Shield, Search, CaretDown, Check, UserCircle, LifeBuoy } from "@/components/icons";

interface Props {
  apps: AppDef[];
  active: AppKey;
  menuOpen: boolean;
  health: HealthResponse | null;
  /** Who is signed in — shown top-right as the way into the profile page. */
  user: CurrentUser | null;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onSelect: (k: AppKey) => void;
  onOpenPalette: () => void;
}

export function StatusBar({
  apps, active, menuOpen, health, user, onToggleMenu, onCloseMenu, onSelect, onOpenPalette,
}: Props) {
  const [clock, setClock] = React.useState("--:--:--");
  React.useEffect(() => {
    const tick = () => setClock(new Date().toTimeString().slice(0, 8));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  const cur = apps.find((a) => a.key === active) ?? apps[0];
  const torOk = health?.tor;

  // The plan chip. Fetched here rather than threaded down from Console so the
  // status bar stays self-contained; it fails silently, because a billing
  // hiccup should never take the console's chrome with it. The bootstrap
  // operator has no billing account and simply gets no chip.
  const [plan, setPlan] = React.useState<BillingSummary | null>(null);
  React.useEffect(() => {
    let alive = true;
    api.billingMe().then((b) => alive && setPlan(b)).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Worth interrupting someone for: prepaid access about to lapse cannot renew
  // itself, so nobody finds out unless we say so.
  const expiring =
    plan && !plan.renews && plan.days_left !== null && plan.days_left <= 7;
  const meter = plan?.usage && plan.usage.limit !== null ? plan.usage : null;
  const spent = Boolean(meter && meter.remaining === 0);

  // What to call the signed-in person. Accounts made without a username fall
  // back to the part of their address before the @; the bootstrap operator has
  // neither, and its address (operator@localhost) lands on "operator".
  const who = user?.username || user?.email?.split("@")[0] || "account";

  return (
    <div
      style={{
        position: "relative", zIndex: 50, display: "flex", alignItems: "center",
        gap: 14, height: 52, padding: "0 14px",
        borderBottom: "1px solid var(--color-divider)",
        background: "color-mix(in srgb, black 26%, var(--color-bg))",
      }}
    >
      <Link href="/" style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--color-accent)", paddingLeft: 4 }}>
        <Shield size={16} />
        <span style={{ fontWeight: 600, fontSize: 12, letterSpacing: "0.18em", color: "var(--color-text)" }}>DECINT</span>
      </Link>

      <span style={{ width: 1, height: 22, background: "var(--color-divider)" }} />

      {/* app switcher dropdown */}
      <div style={{ position: "relative" }}>
        <button type="button" onClick={onToggleMenu} className="sw-btn">
          <span className="tty">{cur?.tty}</span>
          <span style={{ fontSize: 13, fontWeight: 500, minWidth: 56, textAlign: "left" }}>{cur?.name}</span>
          <CaretDown size={12} style={{ color: "var(--color-neutral-500)" }} />
        </button>
        {menuOpen && (
          <>
            <div className="dc-fade" style={{
              position: "absolute", top: "calc(100% + 8px)", left: 0, width: 328,
              background: "color-mix(in srgb, black 32%, var(--color-surface))",
              border: "1px solid var(--color-divider)", borderRadius: 10,
              boxShadow: "0 18px 44px rgba(0,0,0,.62)", padding: 6, backdropFilter: "blur(10px)", zIndex: 60,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 10px 6px" }}>
                <span style={{ fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>Sessions</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--color-neutral-600)" }}>{apps.length} running</span>
              </div>
              {apps.map((app) => (
                <button key={app.key} type="button" onClick={() => onSelect(app.key)} className="sw-row">
                  {app.key === active && <span className="active-bar" />}
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-500)", width: 32 }}>{app.tty}</span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--color-neutral-100)", width: 66 }}>{app.name}</span>
                  <span style={{ fontSize: 12, color: "var(--color-neutral-500)", flex: 1 }}>{app.desc}</span>
                  {app.key === active && <Check size={14} style={{ color: "var(--color-accent)" }} />}
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)", width: 26, textAlign: "right" }}>{app.hot}</span>
                </button>
              ))}
            </div>
            <div onClick={onCloseMenu} style={{ position: "fixed", inset: 0, zIndex: 55 }} />
          </>
        )}
      </div>

      <span style={{ flex: 1 }} />

      <button type="button" onClick={onOpenPalette} className="switch-btn">
        <Search size={13} />
        <span style={{ fontSize: 12 }}>Switch</span>
        <span className="kbd">⌘K</span>
      </button>

      {plan && (
        <Link
          href={plan.usage?.is_free ? "/pricing" : "/billing"}
          className="switch-btn"
          title={
            expiring
              ? `${plan.plan_name} — ${plan.days_left} day(s) of prepaid access left`
              : meter
                ? `${plan.plan_name} — ${meter.used} of ${meter.limit} searches used${meter.window === "monthly" ? " this month" : ""}`
                : `${plan.plan_name} plan — manage billing`
          }
          style={{
            textDecoration: "none",
            color: expiring || spent ? "var(--color-warn)" : undefined,
          }}
        >
          <span style={{ fontSize: 12 }}>{plan.plan_name}</span>
          {expiring && (
            <span className="kbd" style={{ color: "var(--color-warn)" }}>{plan.days_left}d</span>
          )}
          {/* The meter, for anyone who has one. On the free tier it counts
              down a fixed trial, so "0 left" is the moment to show pricing. */}
          {meter && !expiring && (
            <span className="kbd" style={{ color: spent ? "var(--color-warn)" : undefined }}>
              {meter.remaining} left
            </span>
          )}
        </Link>
      )}

      <Link href="/support" title="Support" className="switch-btn" style={{ textDecoration: "none" }}>
        <LifeBuoy size={14} />
        <span style={{ fontSize: 12 }}>Support</span>
      </Link>

      {/* The username is the way into the profile page, where the password and
          email are changed. Sign-out is its own control beside it. */}
      <Link
        href="/account"
        title={user?.email ? `${user.email} — your profile` : "Your profile"}
        className="switch-btn"
        style={{ textDecoration: "none" }}
      >
        <UserCircle size={14} />
        <span className="who">{who}</span>
      </Link>

      <button
        type="button"
        title="Sign out"
        onClick={async () => {
          await api.logout().catch(() => {});
          window.location.replace("/login");
        }}
        className="signout"
      >
        Sign out
      </button>

      <span style={{ display: "flex", alignItems: "center", gap: 6 }} title={health?.tor_detail || "tor"}>
        <span style={{
          width: 7, height: 7, borderRadius: "50%",
          background: torOk ? "#7fce9e" : "#e0b57f",
          boxShadow: `0 0 8px ${torOk ? "#7fce9e" : "#e0b57f"}`,
          animation: "dc-pulse 2.4s ease-in-out infinite",
        }} />
        <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--color-neutral-400)" }}>{clock}</span>
      </span>

      <style jsx>{`
        .sw-btn { display: flex; align-items: center; gap: 9px; height: 32px; padding: 0 9px 0 7px;
          background: color-mix(in srgb, var(--color-accent) 10%, var(--color-surface));
          border: 1px solid color-mix(in srgb, var(--color-accent) 42%, transparent);
          border-radius: 8px; color: var(--color-text); cursor: pointer; font-family: var(--font-body); }
        .sw-btn:hover { border-color: var(--color-accent); background: color-mix(in srgb, var(--color-accent) 16%, var(--color-surface)); }
        .tty { font-family: var(--mono); font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
          color: var(--color-accent-300); background: color-mix(in srgb, var(--color-accent) 20%, transparent);
          padding: 2px 5px; border-radius: 4px; }
        .sw-row { position: relative; display: flex; align-items: center; gap: 12px; width: 100%;
          padding: 9px 10px; background: none; border: 0; border-radius: 7px; cursor: pointer;
          color: var(--color-text); font-family: var(--font-body); text-align: left; }
        .sw-row:hover { background: color-mix(in srgb, var(--color-text) 6%, transparent); }
        .active-bar { position: absolute; left: 0; top: 8px; bottom: 8px; width: 2px; border-radius: 2px;
          background: var(--color-accent); box-shadow: 0 0 9px var(--color-accent); }
        .switch-btn { display: flex; align-items: center; gap: 7px; height: 28px; padding: 0 9px;
          background: none; border: 1px solid var(--color-divider); border-radius: 7px;
          color: var(--color-neutral-400); cursor: pointer; font-family: var(--font-body); }
        .switch-btn:hover { border-color: var(--color-neutral-700); color: var(--color-text); }
        .kbd { font-family: var(--mono); font-size: 10.5px; color: var(--color-neutral-600);
          border: 1px solid var(--color-divider); border-radius: 4px; padding: 1px 5px; }
        .who { font-family: var(--mono); font-size: 12px; max-width: 160px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .signout { font-family: var(--mono); font-size: 12px; color: var(--color-neutral-500);
          background: none; border: 0; padding: 2px 4px; border-radius: 5px; cursor: pointer; }
        .signout:hover { color: var(--color-accent-300); background: color-mix(in srgb, var(--color-accent) 12%, transparent); }
      `}</style>
    </div>
  );
}

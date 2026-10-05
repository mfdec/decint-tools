"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { HealthResponse, SessionResponse } from "@/lib/types";

/**
 * Alpha-only diagnostics. Answers "is this browser actually signed in, and what
 * does the backend think?" without needing a terminal.
 *
 * Deliberately not linked from anywhere — you reach it by typing /debug.
 *
 * It exposes nothing a visitor can't already get: /auth/session only ever
 * describes the caller's own session, and /health is public. Account details
 * are shown only when there IS a session, which means the viewer is that
 * account. Delete this folder before launch anyway — it advertises internals
 * and there's no reason to leave a map lying around.
 */

type Probe<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "err"; msg: string };

function Row({ label, value, mono = true }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 14, padding: "7px 0", borderBottom: "1px solid var(--color-divider)" }}>
      <span style={{ minWidth: 168, fontSize: 12.5, color: "var(--color-neutral-500)" }}>{label}</span>
      <span
        style={{
          fontFamily: mono ? "var(--mono)" : undefined,
          fontSize: 12.5,
          wordBreak: "break-all",
          color: "var(--color-text)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

export default function DebugPage() {
  const [sess, setSess] = React.useState<Probe<SessionResponse>>({ state: "loading" });
  const [health, setHealth] = React.useState<Probe<HealthResponse>>({ state: "loading" });
  const [checkedAt, setCheckedAt] = React.useState("");
  const [cookieVisible, setCookieVisible] = React.useState(false);

  const probe = React.useCallback(() => {
    setSess({ state: "loading" });
    setHealth({ state: "loading" });
    api
      .session()
      .then((d) => setSess({ state: "ok", data: d }))
      .catch((e) => setSess({ state: "err", msg: e instanceof Error ? e.message : "failed" }));
    api
      .health()
      .then((d) => setHealth({ state: "ok", data: d }))
      .catch((e) => setHealth({ state: "err", msg: e instanceof Error ? e.message : "failed" }));
    // The session cookie is httpOnly, so document.cookie must NOT contain it.
    // If this ever reads true, the cookie lost its httpOnly flag — that's a bug.
    setCookieVisible(document.cookie.includes("decint_session"));
    setCheckedAt(new Date().toISOString());
  }, []);

  React.useEffect(() => probe(), [probe]);

  const user = sess.state === "ok" ? sess.data.user : undefined;
  const authed = sess.state === "ok" && sess.data.authenticated;

  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "56px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 24, margin: 0 }}>Debug</h1>
        <span className="tag tag-neutral">alpha</span>
        <button className="btn btn-secondary" style={{ height: 30 }} onClick={probe}>Re-check</button>
        <Link href="/" style={{ fontSize: 13 }}>← home</Link>
      </div>
      <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: "0 0 22px" }}>
        What this browser and the backend currently agree on.
      </p>

      <section className="card" style={{ padding: "18px 20px", marginBottom: 18 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Session</h2>
        {sess.state === "loading" && <div style={{ fontSize: 13 }}>checking…</div>}
        {sess.state === "err" && <div className="tag tag-bad">{sess.msg}</div>}
        {sess.state === "ok" && (
          <>
            <Row
              label="signed in"
              value={
                <span className={`tag ${authed ? "tag-ok" : "tag-bad"}`}>
                  {authed ? "yes" : "no"}
                </span>
              }
            />
            <Row label="email" value={user?.email ?? "—"} />
            <Row label="username" value={user?.username || "—"} />
            <Row label="role" value={user?.role ?? "—"} />
            <Row label="tier" value={user?.tier ?? "—"} />
            <Row label="status" value={user?.status ?? "—"} />
            <Row label="second factor" value={user?.mfa?.length ? user.mfa.join(", ") : "none enrolled"} />
            {user?.break_glass && (
              <Row label="break-glass" value={<span className="tag tag-warn">operator-token login</span>} />
            )}
            <Row
              label="cookie readable by JS"
              value={
                <span className={`tag ${cookieVisible ? "tag-bad" : "tag-ok"}`}>
                  {cookieVisible ? "YES — httpOnly is broken" : "no (correct)"}
                </span>
              }
            />
          </>
        )}
      </section>

      <section className="card" style={{ padding: "18px 20px", marginBottom: 18 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Backend</h2>
        {health.state === "loading" && <div style={{ fontSize: 13 }}>checking…</div>}
        {health.state === "err" && (
          <div className="tag tag-bad">
            {health.msg} — the API is unreachable through this origin
          </div>
        )}
        {health.state === "ok" && (
          <>
            <Row label="status" value={health.data.status} />
            <Row label="version" value={health.data.version} />
            <Row label="auth enabled" value={String(health.data.auth_enabled)} />
            <Row label="tor" value={`${health.data.tor} — ${health.data.tor_detail ?? ""}`} />
            <Row label="sniffer" value={String(health.data.sniffer_enabled)} />
            <Row label="leak providers" value={(health.data.leak_providers ?? []).join(", ") || "—"} />
          </>
        )}
      </section>

      <section className="card" style={{ padding: "18px 20px" }}>
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Browser</h2>
        <Row label="origin" value={typeof window !== "undefined" ? window.location.origin : "—"} />
        <Row label="protocol" value={typeof window !== "undefined" ? window.location.protocol : "—"} />
        <Row label="user agent" value={typeof navigator !== "undefined" ? navigator.userAgent : "—"} />
        <Row label="checked at" value={checkedAt || "—"} />
      </section>
    </main>
  );
}

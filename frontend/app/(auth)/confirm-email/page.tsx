"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Shield } from "@/components/icons";

/**
 * Landing page of the email-change link.
 *
 * The address is swapped on arrival — there is nothing to confirm here, the
 * click was the confirmation. No session is needed: the link is mailed to the
 * NEW address and may well be opened in a different browser than the one the
 * change was requested from.
 */

type Outcome = "checking" | "changed" | "dead";

export default function ConfirmEmailPage() {
  const [outcome, setOutcome] = React.useState<Outcome>("checking");
  const [email, setEmail] = React.useState("");

  // The link is single-use. Under React strict mode (dev) effects run twice, and
  // a second request would be refused and overwrite the success — so ask once.
  const asked = React.useRef(false);

  React.useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    const token = new URLSearchParams(window.location.search).get("token") || "";
    if (!token) { setOutcome("dead"); return; }
    api.confirmEmailChange(token)
      .then((r) => { setEmail(r.email); setOutcome("changed"); })
      .catch(() => setOutcome("dead"));
  }, []);

  return (
    <div style={{
      minHeight: "100vh", display: "grid", placeItems: "center", padding: 24,
      background: "radial-gradient(700px 360px at 50% -10%, color-mix(in srgb, var(--color-accent-900) 55%, transparent), transparent 62%)",
    }}>
      <div style={{ width: "min(400px, 100%)" }}>
        <div className="card elev-lg" style={{ padding: 28, gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Shield size={22} style={{ color: "var(--color-accent)" }} />
            <span style={{ fontWeight: 600, letterSpacing: "0.16em" }}>DECINT</span>
          </div>

          {outcome === "checking" ? (
            <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
              Confirming your new address…
            </p>
          ) : outcome === "changed" ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Email updated</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                Your account now uses <span className="mono" style={{ color: "var(--color-text)" }}>{email}</span>.
                Sign in with it from now on — password resets and notices go there too.
              </p>
              <Link href="/account" className="btn btn-primary btn-block">Back to your account</Link>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Link expired</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                This confirmation link has expired, was already used, or isn&apos;t
                valid. Start the change again from your account page and use the
                new link straight away.
              </p>
              <Link href="/account" className="btn btn-primary btn-block">Go to your account</Link>
            </>
          )}
        </div>
        <p style={{ textAlign: "center", fontSize: 12, color: "var(--color-neutral-600)", marginTop: 14 }}>
          <Link href="/">← back</Link> · OSINT · Network intelligence
        </p>
      </div>
    </div>
  );
}

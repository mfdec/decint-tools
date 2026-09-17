"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { Captcha, type CaptchaHandle } from "@/components/Captcha";
import { Shield } from "@/components/icons";

/**
 * Two jobs on one route.
 *
 * With `?token=` it is the landing page of the activation email: the account
 * is activated on arrival — there is nothing to confirm, the click was the
 * confirmation — and the visitor is pointed at sign-in, carrying the page
 * they were on before signing up (usually pricing) so the purchase they
 * started is one sign-in away.
 *
 * Without a token it is the "send me another link" form, reached from the
 * signup screen and from a sign-in refused for a not-yet-activated account.
 * Like the reset form it says nothing about whether the address exists.
 */

type Outcome = "checking" | "activated" | "already" | "dead";

export default function ActivatePage() {
  const [token, setToken] = React.useState<string | null>(null);
  const [outcome, setOutcome] = React.useState<Outcome>("checking");
  const [next, setNext] = React.useState("");

  // resend form
  const [email, setEmail] = React.useState("");
  const [siteKey, setSiteKey] = React.useState("");
  const [needCaptcha, setNeedCaptcha] = React.useState(false);
  const [captchaToken, setCaptchaToken] = React.useState("");
  const [enabled, setEnabled] = React.useState(true);
  const [note, setNote] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const capRef = React.useRef<CaptchaHandle>(null);

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get("token") || "";
    setToken(t);
    setEmail(params.get("email") || "");
    if (t) {
      api.activate(t)
        .then((r) => {
          setNext(r.next || "");
          setOutcome(r.already ? "already" : "activated");
        })
        .catch(() => setOutcome("dead"));
    }
    api.signupInfo()
      .then((i) => {
        setSiteKey(i.captcha_site_key);
        setNeedCaptcha(i.captcha_on_login);
        setEnabled(Boolean(i.email_activation));
      })
      .catch(() => {});
  }, []);

  async function resend(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.activationResend(email, captchaToken);
      setNote(r.note);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send a new link.");
      setCaptchaToken("");
      capRef.current?.reset();
    } finally {
      setBusy(false);
    }
  }

  const signIn = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  const resendForm = (
    <form onSubmit={resend} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="field">
        <label>Email</label>
        <input className="input" type="email" value={email} autoFocus
               autoComplete="email"
               onChange={(e) => setEmail(e.target.value)} />
      </div>

      {needCaptcha && siteKey && (
        <Captcha ref={capRef} siteKey={siteKey}
                 onVerify={setCaptchaToken}
                 onExpire={() => setCaptchaToken("")} />
      )}

      {error && <div className="tag tag-bad">{error}</div>}

      <button type="submit" className="btn btn-primary btn-block"
              disabled={busy || !email.trim() || (needCaptcha && !!siteKey && !captchaToken)}>
        {busy ? "…" : "Send a new link"}
      </button>
    </form>
  );

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

          {token === null ? null : token && outcome === "checking" ? (
            <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
              Activating your account…
            </p>
          ) : token && (outcome === "activated" || outcome === "already") ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>
                {outcome === "already" ? "Already active" : "You're in"}
              </h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                {outcome === "already"
                  ? "This account was activated earlier. Sign in to continue."
                  : next
                    ? "Your account is active. Sign in and you'll be taken straight back to where you left off."
                    : "Your account is active. Sign in to open the console."}
              </p>
              <Link href={signIn} className="btn btn-primary btn-block">Sign in</Link>
            </>
          ) : token && outcome === "dead" ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Link expired</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                Activation links time out, and one stops working if the address on
                the account changes. Enter your email and we&apos;ll send a fresh one.
              </p>
              {resendForm}
            </>
          ) : note ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Check your email</h1>
              {/* Says nothing about whether the address exists — the endpoint
                  answers identically either way. */}
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>{note}</p>
              <Link href="/login" className="btn btn-secondary btn-block">Back to sign in</Link>
            </>
          ) : !enabled ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Activation by email is off</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                On this server an operator approves new accounts. You&apos;ll get an
                email when that happens.
              </p>
              <Link href="/login" className="btn btn-secondary btn-block">Back to sign in</Link>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: 26, margin: "4px 0 0" }}>Resend activation link</h1>
              <p style={{ fontSize: 13, color: "var(--color-neutral-400)", margin: 0 }}>
                Enter the address you signed up with and we&apos;ll send another link.
              </p>
              {resendForm}
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

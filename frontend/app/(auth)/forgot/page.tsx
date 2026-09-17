"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Captcha, type CaptchaHandle } from "@/components/Captcha";
import { Shield } from "@/components/icons";

export default function ForgotPasswordPage() {
  const [email, setEmail] = React.useState("");
  const [note, setNote] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const [siteKey, setSiteKey] = React.useState("");
  const [needCaptcha, setNeedCaptcha] = React.useState(false);
  const [captchaToken, setCaptchaToken] = React.useState("");
  const capRef = React.useRef<CaptchaHandle>(null);

  React.useEffect(() => {
    api.signupInfo()
      .then((i) => {
        setSiteKey(i.captcha_site_key);
        setNeedCaptcha(i.captcha_on_login);
      })
      .catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.passwordForgot(email.trim(), captchaToken);
      setNote(r.note);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send a reset link.");
      setCaptchaToken("");
      capRef.current?.reset();
    } finally {
      setBusy(false);
    }
  }

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

          {note ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Check your email</h1>
              {/* Deliberately says nothing about whether the address exists —
                  the endpoint answers identically either way, and a page that
                  said "no such account" would undo that. */}
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                {note}
              </p>
              <Link href="/login" className="btn btn-secondary btn-block">Back to sign in</Link>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: 26, margin: "4px 0 0" }}>Reset password</h1>
              <p style={{ fontSize: 13, color: "var(--color-neutral-400)", margin: 0 }}>
                Enter the address on your account and we&apos;ll send a link that
                lets you set a new password.
              </p>

              <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
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
                  {busy ? "…" : "Send reset link"}
                </button>
              </form>

              <div style={{ display: "flex", gap: 6, justifyContent: "center", fontSize: 12, color: "var(--color-neutral-500)" }}>
                <span>Remembered it?</span>
                <Link href="/login">Sign in</Link>
              </div>
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

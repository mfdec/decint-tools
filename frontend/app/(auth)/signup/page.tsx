"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Captcha, type CaptchaHandle } from "@/components/Captcha";
import { Shield } from "@/components/icons";

export default function SignupPage() {
  const [email, setEmail] = React.useState("");
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [captchaToken, setCaptchaToken] = React.useState("");

  const [siteKey, setSiteKey] = React.useState("");
  const [needCaptcha, setNeedCaptcha] = React.useState(false);
  const [enabled, setEnabled] = React.useState(true);
  // What happens after the form: an activation email, an operator's
  // approval, or straight to sign-in. The server decides; the page just
  // promises the right thing.
  const [activation, setActivation] = React.useState(false);
  // Where to go after signing in — the pricing page sends people here with
  // the plan they were about to buy. Local paths only; anything else is
  // dropped, the same rule the login page applies.
  const nextRef = React.useRef("");

  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const capRef = React.useRef<CaptchaHandle>(null);

  React.useEffect(() => {
    const n = new URLSearchParams(window.location.search).get("next");
    if (n && /^\/(?!\/|\\)/.test(n)) nextRef.current = n;
    api.signupInfo()
      .then((i) => {
        setSiteKey(i.captcha_site_key);
        setNeedCaptcha(i.captcha_on_signup);
        setEnabled(i.signup_enabled);
        setActivation(Boolean(i.email_activation));
      })
      .catch(() => {});
  }, []);

  const pwTooShort = password.length > 0 && password.length < 12;
  const mismatch = confirm.length > 0 && confirm !== password;
  const ready =
    email && username && password.length >= 12 && confirm === password &&
    (!needCaptcha || captchaToken);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await api.signup(email, username, password, captchaToken, nextRef.current);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the account.");
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
      <div style={{ width: "min(420px, 100%)" }}>
        <div className="card elev-lg" style={{ padding: 28, gap: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Shield size={22} style={{ color: "var(--color-accent)" }} />
            <span style={{ fontWeight: 600, letterSpacing: "0.16em" }}>DECINT</span>
          </div>

          {done && activation ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Check your email</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                If that address is new, an activation link is on its way to{" "}
                <strong style={{ color: "var(--color-text)" }}>{email}</strong>.
                One click and you&apos;re in — nothing else is needed from you.
              </p>
              <p style={{ fontSize: 12.5, color: "var(--color-neutral-500)", margin: 0 }}>
                Nothing after a few minutes? Check your spam folder, or{" "}
                <Link href={`/activate?email=${encodeURIComponent(email)}`}>request a new link</Link>.
              </p>
              <Link
                href={nextRef.current ? `/login?next=${encodeURIComponent(nextRef.current)}` : "/login"}
                className="btn btn-secondary btn-block"
              >
                Already activated? Sign in
              </Link>
            </>
          ) : done ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Account ready</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                If that address is new, your account has been created. Sign in to
                open the console.
              </p>
              <Link
                href={nextRef.current ? `/login?next=${encodeURIComponent(nextRef.current)}` : "/login"}
                className="btn btn-primary btn-block"
              >
                Sign in
              </Link>
            </>
          ) : !enabled ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Registration is closed</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                DECINT isn&apos;t accepting new accounts right now.
              </p>
              <Link href="/" className="btn btn-secondary btn-block">Back to site</Link>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: 26, margin: "4px 0 0" }}>Create account</h1>

              <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="field">
                  <label>Email</label>
                  <input className="input" type="email" value={email} autoFocus
                         autoComplete="email"
                         onChange={(e) => setEmail(e.target.value)} />
                </div>

                <div className="field">
                  <label>Username</label>
                  <input className="input" value={username} autoComplete="username"
                         onChange={(e) => setUsername(e.target.value)}
                         placeholder="3–24 chars · letters, numbers, . _ -" />
                </div>

                <div className="field">
                  <label>Password</label>
                  <input className="input" type="password" value={password}
                         autoComplete="new-password"
                         onChange={(e) => setPassword(e.target.value)} />
                  <span style={{ fontSize: 11.5, color: pwTooShort ? "#e0b57f" : "var(--color-neutral-600)" }}>
                    At least 12 characters. A passphrase beats a short complex one.
                  </span>
                </div>

                <div className="field">
                  <label>Confirm password</label>
                  <input className="input" type="password" value={confirm}
                         autoComplete="new-password"
                         onChange={(e) => setConfirm(e.target.value)} />
                  {mismatch && (
                    <span style={{ fontSize: 11.5, color: "#e8908f" }}>
                      Passwords don&apos;t match.
                    </span>
                  )}
                </div>

                {needCaptcha && siteKey && (
                  <Captcha ref={capRef} siteKey={siteKey}
                           onVerify={setCaptchaToken}
                           onExpire={() => setCaptchaToken("")} />
                )}

                {error && <div className="tag tag-bad">{error}</div>}

                <button type="submit" className="btn btn-primary btn-block" disabled={busy || !ready}>
                  {busy ? "…" : "Create account"}
                </button>
              </form>

              <div style={{ display: "flex", gap: 6, justifyContent: "center", fontSize: 12, color: "var(--color-neutral-500)" }}>
                <span>Already have an account?</span>
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

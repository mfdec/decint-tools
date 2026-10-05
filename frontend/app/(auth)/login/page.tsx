"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Captcha, asCaptchaProvider, type CaptchaHandle, type CaptchaProvider } from "@/components/Captcha";
import { Shield } from "@/components/icons";
import { SocialLogin, oauthErrorMessage, oauthNoticeMessage } from "@/components/SocialLogin";

type Stage = "password" | "mfa";

const METHOD_LABEL: Record<string, string> = {
  totp: "Authenticator app",
  email: "Emailed code",
  sms: "Text message",
  recovery: "Recovery code",
};

export default function LoginPage() {
  const nextRef = React.useRef("/console");
  const [stage, setStage] = React.useState<Stage>("password");

  const [hasUsers, setHasUsers] = React.useState<boolean | null>(null);
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [token, setToken] = React.useState("");

  const [challenge, setChallenge] = React.useState("");
  const [methods, setMethods] = React.useState<string[]>([]);
  const [method, setMethod] = React.useState("totp");
  const [code, setCode] = React.useState("");
  const [sentNote, setSentNote] = React.useState<string | null>(null);

  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [oauthProviders, setOauthProviders] = React.useState<string[]>([]);

  const [siteKey, setSiteKey] = React.useState("");
  const [captchaProvider, setCaptchaProvider] = React.useState<CaptchaProvider>("hcaptcha");
  const [needCaptcha, setNeedCaptcha] = React.useState(false);
  const [captchaToken, setCaptchaToken] = React.useState("");
  const capRef = React.useRef<CaptchaHandle>(null);

  const [tokenLoginEnabled, setTokenLoginEnabled] = React.useState(false);
  const [resetEnabled, setResetEnabled] = React.useState(false);
  const [authMode, setAuthMode] = React.useState<"email" | "token">("email");
  const [keyInput, setKeyInput] = React.useState("");

  React.useEffect(() => {
    // Only same-origin relative paths are honoured. Without this check an
    // attacker could send ?next=https://evil.example and use our own login as
    // an open redirect once the victim authenticates.
    const qs = new URLSearchParams(window.location.search);
    const n = qs.get("next");
    if (n && /^\/(?!\/|\\)/.test(n)) nextRef.current = n;

    // Coming back from "Continue with Google": the backend redirects here with
    // a short code for a refusal, or — when the account has two-factor on —
    // with the MFA challenge in the #fragment (so it never reaches a server
    // log). Google proved the email; it is not a second factor.
    const failure = qs.get("oauth_error");
    if (failure) setError(oauthErrorMessage(failure));
    const note = qs.get("oauth_notice");
    if (note) setNotice(oauthNoticeMessage(note));

    const frag = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const mfa = frag.get("mfa");
    if (mfa) {
      const offered = (frag.get("methods") || "").split(",").filter(Boolean);
      const after = frag.get("next");
      if (after && /^\/(?!\/|\\)/.test(after)) nextRef.current = after;
      setChallenge(mfa);
      setMethods(offered);
      setMethod(offered[0] || "totp");
      // The backend has already sent the code for email and text-message 2FA.
      setSentNote(offered[0] === "email" || offered[0] === "sms" ? `Code sent by ${offered[0]}.` : null);
      setStage("mfa");
    }
    if (failure || note || mfa) window.history.replaceState(null, "", window.location.pathname);

    api.bootstrap()
      .then((b) => setHasUsers(b.has_users))
      .catch(() => setHasUsers(true));
    api.signupInfo()
      .then((i) => {
        setSiteKey(i.captcha_site_key);
        setCaptchaProvider(asCaptchaProvider(i.captcha_provider));
        setNeedCaptcha(i.captcha_on_login);
        setTokenLoginEnabled(!!i.login_token_enabled);
        setOauthProviders(i.oauth_providers || []);
        // Hidden unless the backend actually has a relay to send through,
        // so the link never leads somewhere that cannot help.
        setResetEnabled(!!i.password_reset_enabled);
      })
      .catch(() => {});
  }, []);

  async function submitToken(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.loginToken(keyInput.trim());
      if (r.authenticated) {
        window.location.replace(nextRef.current);
        return;
      }
      if (r.mfa_required) {
        setChallenge(r.challenge || "");
        setMethods(r.methods || []);
        setMethod((r.methods || ["totp"])[0]);
        setSentNote(r.sent ? `Code sent by ${r.sent}.` : null);
        setStage("mfa");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.login(email, password, token, captchaToken);
      if (r.authenticated) {
        window.location.replace(nextRef.current);
        return;
      }
      if (r.mfa_required) {
        setChallenge(r.challenge || "");
        setMethods(r.methods || []);
        setMethod((r.methods || ["totp"])[0]);
        setSentNote(r.sent ? `Code sent by ${r.sent}.` : null);
        setStage("mfa");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      // A failure may have pushed this IP over the risk threshold, so ask the
      // server again whether a captcha is now required.
      setCaptchaToken("");
      capRef.current?.reset();
      api.signupInfo()
        .then((i) => {
          setSiteKey(i.captcha_site_key);
          setCaptchaProvider(asCaptchaProvider(i.captcha_provider));
          setNeedCaptcha(i.captcha_on_login);
        })
        .catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.mfaVerify(challenge, method, code);
      if (r.authenticated) window.location.replace(nextRef.current);
      else setError("That code wasn't accepted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resend(m: string) {
    setMethod(m); setError(null); setCode("");
    if (m === "totp" || m === "recovery") { setSentNote(null); return; }
    try {
      await api.mfaSend(challenge, m);
      setSentNote(`Code sent by ${m}.`);
    } catch {
      setError("Could not send a code.");
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

          {stage === "password" ? (
            <>
              <h1 style={{ fontSize: 26, margin: "4px 0 0" }}>Sign in</h1>
              {notice && <div className="tag tag-ok" style={{ whiteSpace: "normal" }}>{notice}</div>}

              {/* Two ways in: email + password, or a per-account login token.
                  The chooser is hidden during first-run (operator-token) setup
                  and whenever token sign-in is switched off on the backend. */}
              {tokenLoginEnabled && hasUsers !== false ? (
                <div className="seg" style={{ alignSelf: "stretch" }}>
                  {(["email", "token"] as const).map((m) => (
                    <label key={m} className={`seg-opt ${authMode === m ? "active" : ""}`}
                           style={{ flex: 1, textAlign: "center" }}>
                      <input type="radio" checked={authMode === m}
                             onChange={() => { setAuthMode(m); setError(null); }}
                             style={{ display: "none" }} />
                      {m === "email" ? "Email" : "Login token"}
                    </label>
                  ))}
                </div>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--color-neutral-600)", fontSize: 12 }}>
                  <span style={{ flex: 1, height: 1, background: "var(--color-divider)" }} />
                  {hasUsers === false ? "first-run setup" : "email"}
                  <span style={{ flex: 1, height: 1, background: "var(--color-divider)" }} />
                </div>
              )}

              {authMode === "token" && hasUsers !== false ? (
                <form onSubmit={submitToken} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="field">
                    <label>Login token</label>
                    <input className="input" value={keyInput} autoFocus
                           onChange={(e) => setKeyInput(e.target.value)}
                           placeholder="XXXXX-XXXXX-XXXXX" autoComplete="off" spellCheck={false}
                           style={{ fontFamily: "var(--mono)", letterSpacing: "0.12em" }} />
                  </div>
                  <p style={{ fontSize: 11.5, color: "var(--color-neutral-500)", margin: 0 }}>
                    The token your operator issued you. Case-insensitive; dashes optional.
                  </p>
                  {error && <div className="tag tag-bad">{error}</div>}
                  <button type="submit" className="btn btn-primary btn-block"
                          disabled={busy || !keyInput.trim()}>
                    {busy ? "…" : "Continue"}
                  </button>
                </form>
              ) : (
                <form onSubmit={submitPassword} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {hasUsers === false ? (
                    <>
                      <div className="field">
                        <label>Operator token</label>
                        <input className="input" type="password" value={token}
                               onChange={(e) => setToken(e.target.value)} autoFocus
                               placeholder="from backend/.env" />
                      </div>
                      <p style={{ fontSize: 11.5, color: "var(--color-neutral-500)", margin: 0 }}>
                        No accounts exist yet. Sign in with the operator token, then create an
                        admin account — this bootstrap login closes as soon as one exists.
                      </p>
                    </>
                  ) : (
                    <>
                      <div className="field">
                        <label>Email</label>
                        <input className="input" type="email" value={email} autoFocus
                               onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
                      </div>
                      <div className="field">
                        <label>Password</label>
                        <input className="input" type="password" value={password}
                               onChange={(e) => setPassword(e.target.value)}
                               autoComplete="current-password" />
                      </div>
                    </>
                  )}
                  {needCaptcha && siteKey && (
                    <Captcha ref={capRef} siteKey={siteKey} provider={captchaProvider}
                             onVerify={setCaptchaToken}
                             onExpire={() => setCaptchaToken("")} />
                  )}
                  {error && <div className="tag tag-bad">{error}</div>}
                  {/* A sign-in refused because the account was never activated
                      is fixed by another email, not another password attempt. */}
                  {error && /activated/i.test(error) && (
                    <p style={{ fontSize: 12.5, margin: "-4px 0 0", color: "var(--color-neutral-400)" }}>
                      Didn&apos;t get the email?{" "}
                      <Link href={`/activate?email=${encodeURIComponent(email)}`}>Send a new activation link</Link>
                    </p>
                  )}
                  <button type="submit" className="btn btn-primary btn-block"
                          disabled={busy || (needCaptcha && !!siteKey && !captchaToken)}>
                    {busy ? "…" : "Continue"}
                  </button>
                </form>
              )}

              {hasUsers !== false && (
                <SocialLogin providers={oauthProviders} next={nextRef.current} />
              )}

              {hasUsers !== false && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, fontSize: 12, color: "var(--color-neutral-500)" }}>
                  {resetEnabled && authMode === "email" && (
                    <Link href="/forgot">Forgot your password?</Link>
                  )}
                  <div style={{ display: "flex", gap: 6 }}>
                    <span>No account?</span>
                    <Link href="/signup">Create one</Link>
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Two-factor</h1>
              <p style={{ fontSize: 13, color: "var(--color-neutral-400)", margin: 0 }}>
                {method === "totp"
                  ? "Enter the 6-digit code from your authenticator app."
                  : method === "recovery"
                  ? "Enter one of your recovery codes."
                  : sentNote || "Enter the code we sent you."}
              </p>

              <form onSubmit={submitCode} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <input className="input" value={code} autoFocus inputMode="text"
                       onChange={(e) => setCode(e.target.value)}
                       placeholder={method === "recovery" ? "xxxx-xxxx-xxxx" : "123456"}
                       style={{ fontFamily: "var(--mono)", fontSize: 18, letterSpacing: "0.18em", textAlign: "center" }} />
                {error && <div className="tag tag-bad">{error}</div>}
                <button type="submit" className="btn btn-primary btn-block" disabled={busy || !code}>
                  {busy ? "…" : "Verify"}
                </button>
              </form>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "center" }}>
                {[...methods, "recovery"].filter((m) => m !== method).map((m) => (
                  <button key={m} type="button" className="btn btn-ghost" style={{ fontSize: 12 }}
                          onClick={() => resend(m)}>
                    {METHOD_LABEL[m] || m}
                  </button>
                ))}
              </div>

              <button type="button" className="btn btn-ghost btn-block" style={{ fontSize: 12 }}
                      onClick={() => { setStage("password"); setCode(""); setError(null); }}>
                ← Back
              </button>
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

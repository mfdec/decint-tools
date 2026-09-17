"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Shield } from "@/components/icons";

export default function ResetPasswordPage() {
  const [token, setToken] = React.useState("");
  // null = still checking. Checking up front means a dead link says so before
  // someone types a new password twice.
  const [valid, setValid] = React.useState<boolean | null>(null);

  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [done, setDone] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("token") || "";
    setToken(t);
    if (!t) { setValid(false); return; }
    api.passwordResetCheck(t)
      .then((r) => setValid(r.valid))
      .catch(() => setValid(false));
  }, []);

  const tooShort = password.length > 0 && password.length < 12;
  const mismatch = confirm.length > 0 && confirm !== password;
  const ready = password.length >= 12 && confirm === password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await api.passwordReset(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password.");
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

          {done ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Password changed</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                Every session on the account was signed out. Sign in with the new
                password.
              </p>
              <Link href="/login" className="btn btn-primary btn-block">Sign in</Link>
            </>
          ) : valid === null ? (
            <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
              Checking that link…
            </p>
          ) : !valid ? (
            <>
              <h1 style={{ fontSize: 24, margin: "4px 0 0" }}>Link expired</h1>
              <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: 0 }}>
                Reset links work once and time out quickly. Request a fresh one
                and use it straight away.
              </p>
              <Link href="/forgot" className="btn btn-primary btn-block">
                Send a new link
              </Link>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: 26, margin: "4px 0 0" }}>Set a new password</h1>

              <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="field">
                  <label>New password</label>
                  <input className="input" type="password" value={password} autoFocus
                         autoComplete="new-password"
                         onChange={(e) => setPassword(e.target.value)} />
                  <span style={{ fontSize: 11.5, color: tooShort ? "#e0b57f" : "var(--color-neutral-600)" }}>
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

                {error && <div className="tag tag-bad">{error}</div>}

                <button type="submit" className="btn btn-primary btn-block"
                        disabled={busy || !ready}>
                  {busy ? "…" : "Change password"}
                </button>
              </form>
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

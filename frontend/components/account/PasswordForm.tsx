"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

/**
 * Change the password from inside a session.
 *
 * The current password is required: a stolen session cookie on its own must not
 * be enough to take an account, and the backend counts wrong guesses against
 * the same lockout the login uses. Every OTHER session is signed out; this one
 * stays, so saving doesn't bounce you to the login page.
 *
 * `unavailable` replaces the form with an explanation — used for the bootstrap
 * operator session, which has no account behind it to change.
 */
export function PasswordForm({ unavailable }: { unavailable?: string }) {
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState<string | null>(null);

  const tooShort = next.length > 0 && next.length < 12;
  const mismatch = confirm.length > 0 && confirm !== next;
  const ready = current.length > 0 && next.length >= 12 && confirm === next;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setDone(null);
    try {
      const r = await api.changePassword(current, next);
      setDone(`Password changed. ${r.note}`);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "22px 22px", gap: 14 }}>
      <h3 style={{ fontSize: 16, margin: 0 }}>Change password</h3>

      {unavailable ? (
        <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: 0 }}>{unavailable}</p>
      ) : (
        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="field">
            <label htmlFor="pw-current">Current password</label>
            <input id="pw-current" className="input" type="password" value={current}
                   autoComplete="current-password"
                   onChange={(e) => setCurrent(e.target.value)} />
            <span style={{ fontSize: 11.5, color: "var(--color-neutral-600)" }}>
              Don&apos;t remember it? <Link href="/forgot">Reset it by email</Link>.
            </span>
          </div>

          <div className="field">
            <label htmlFor="pw-new">New password</label>
            <input id="pw-new" className="input" type="password" value={next}
                   autoComplete="new-password"
                   onChange={(e) => setNext(e.target.value)} />
            <span style={{ fontSize: 11.5, color: tooShort ? "#e0b57f" : "var(--color-neutral-600)" }}>
              At least 12 characters. A passphrase beats a short complex one.
            </span>
          </div>

          <div className="field">
            <label htmlFor="pw-confirm">Confirm new password</label>
            <input id="pw-confirm" className="input" type="password" value={confirm}
                   autoComplete="new-password"
                   onChange={(e) => setConfirm(e.target.value)} />
            {mismatch && (
              <span style={{ fontSize: 11.5, color: "#e8908f" }}>Passwords don&apos;t match.</span>
            )}
          </div>

          {error && <div className="tag tag-bad" role="alert">{error}</div>}
          {done && <div className="tag tag-ok" role="status">{done}</div>}

          <button type="submit" className="btn btn-primary" disabled={busy || !ready}
                  style={{ alignSelf: "flex-start" }}>
            {busy ? "…" : "Change password"}
          </button>
        </form>
      )}
    </div>
  );
}

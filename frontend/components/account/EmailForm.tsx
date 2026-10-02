"use client";

import * as React from "react";
import { api, ApiError } from "@/lib/api";

/**
 * Change the account email.
 *
 * Nothing changes when this is submitted: a confirmation link goes to the NEW
 * address, and the account keeps its current one until that link is opened.
 * That is what stops a typo from locking someone out, and an address nobody has
 * proved they hold from receiving password-reset links. The server answers the
 * same whether or not the address is already taken, so the message here is
 * deliberately conditional ("if that address can be used…").
 *
 * `unavailable` replaces the form with an explanation — the bootstrap operator
 * has no account to change, and a server with no mail relay can't send the link.
 */
export function EmailForm({
  currentEmail,
  unavailable,
}: {
  currentEmail: string;
  unavailable?: string;
}) {
  const [address, setAddress] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [sentTo, setSentTo] = React.useState<string | null>(null);

  const ready = /\S+@\S+\.\S+/.test(address.trim()) && password.length > 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const target = address.trim();
    setBusy(true); setError(null); setSentTo(null);
    try {
      await api.requestEmailChange(target, password);
      setSentTo(target);
      setAddress(""); setPassword("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the email change.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "22px 22px", gap: 14 }}>
      <h3 style={{ fontSize: 16, margin: 0 }}>Change email</h3>
      <p style={{ fontSize: 13, color: "var(--color-neutral-500)", margin: 0 }}>
        Current address: <span className="mono" style={{ color: "var(--color-text)" }}>{currentEmail}</span>
      </p>

      {unavailable ? (
        <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: 0 }}>{unavailable}</p>
      ) : (
        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="field">
            <label htmlFor="em-new">New email address</label>
            <input id="em-new" className="input" type="email" value={address}
                   autoComplete="email"
                   onChange={(e) => setAddress(e.target.value)} />
            <span style={{ fontSize: 11.5, color: "var(--color-neutral-600)" }}>
              We&apos;ll send a link to it. Your address changes only once you open the link.
            </span>
          </div>

          <div className="field">
            <label htmlFor="em-password">Current password</label>
            <input id="em-password" className="input" type="password" value={password}
                   autoComplete="current-password"
                   onChange={(e) => setPassword(e.target.value)} />
          </div>

          {error && <div className="tag tag-bad" role="alert">{error}</div>}
          {sentTo && (
            <div className="tag tag-ok" role="status" style={{ whiteSpace: "normal" }}>
              If {sentTo} can be used, a confirmation link is on its way to it. Until you
              open it, keep signing in with {currentEmail}.
            </div>
          )}

          <button type="submit" className="btn btn-primary" disabled={busy || !ready}
                  style={{ alignSelf: "flex-start" }}>
            {busy ? "…" : "Send confirmation link"}
          </button>

          <p style={{ fontSize: 11.5, color: "var(--color-neutral-600)", margin: 0 }}>
            If you sign in with Google or GitHub, those match on this address — after
            changing it, signing in with the old one would start a separate account.
          </p>
        </form>
      )}
    </div>
  );
}

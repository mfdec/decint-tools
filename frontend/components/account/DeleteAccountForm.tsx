"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

/**
 * Delete the account, from inside a session.
 *
 * Irreversible, so it takes the current password AND the word DELETE typed out —
 * the backend checks both. A card subscription is cancelled before anything is
 * removed, and if that fails the account stays exactly as it was. On success
 * the session is gone with the account, so the page leaves for the home page.
 *
 * `unavailable` replaces the form with an explanation — used for the bootstrap
 * operator session, which has no account behind it to delete.
 */
export function DeleteAccountForm({ unavailable }: { unavailable?: string }) {
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const ready = password.length > 0 && confirm === "DELETE";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await api.deleteAccount(password, confirm);
      window.location.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the account.");
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "22px 22px", gap: 14, borderColor: "color-mix(in srgb, var(--color-bad) 45%, transparent)" }}>
      <h3 style={{ fontSize: 16, margin: 0 }}>Delete account</h3>

      {unavailable ? (
        <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: 0 }}>{unavailable}</p>
      ) : (
        <>
          <p style={{ fontSize: 13.5, color: "var(--color-neutral-400)", margin: 0, lineHeight: 1.55 }}>
            Permanently deletes your account, plan, payment history and support
            tickets. A card subscription is cancelled and won&apos;t renew. This
            can&apos;t be undone. <Link href="/delete-account">What gets deleted</Link>.
          </p>
          <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="field">
              <label htmlFor="del-password">Current password</label>
              <input id="del-password" className="input" type="password" value={password}
                     autoComplete="current-password"
                     onChange={(e) => setPassword(e.target.value)} />
            </div>

            <div className="field">
              <label htmlFor="del-confirm">Type DELETE to confirm</label>
              <input id="del-confirm" className="input" value={confirm}
                     autoComplete="off" autoCapitalize="characters" spellCheck={false}
                     onChange={(e) => setConfirm(e.target.value)} />
            </div>

            {error && <div className="tag tag-bad" role="alert">{error}</div>}

            <button type="submit" className="btn btn-danger" disabled={busy || !ready}
                    style={{ alignSelf: "flex-start" }}>
              {busy ? "…" : "Delete my account"}
            </button>
          </form>
        </>
      )}
    </div>
  );
}

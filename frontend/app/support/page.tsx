"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import type { TicketReason } from "@/lib/types";

/** Where the top bar's "Support" button lands: open a new ticket. */

const REASONS: { value: TicketReason; label: string; hint: string }[] = [
  { value: "billing", label: "Billing", hint: "A charge, a plan, a refund, an invoice." },
  { value: "technical", label: "Technical", hint: "Something isn't working as it should." },
  { value: "other", label: "Other", hint: "Anything else." },
];

export default function NewTicketPage() {
  const router = useRouter();
  const [reason, setReason] = React.useState<TicketReason>("technical");
  const [subject, setSubject] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const t = await api.openTicket(reason, subject.trim(), message.trim());
      router.push(`/support/${t.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open the ticket.");
      setBusy(false);
    }
  }

  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />

      <section style={{ maxWidth: 640, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">Support</p>
        <h1 style={{ fontSize: "clamp(28px, 5vw, 40px)", margin: "0 0 10px" }}>
          Open a ticket.
        </h1>
        <p style={{ fontSize: 14, color: "var(--color-neutral-500)", margin: "0 0 28px" }}>
          We reply by email and in{" "}
          <Link href="/account">your account</Link>.
        </p>

        <form onSubmit={submit} className="card" style={{ padding: "24px", gap: 18 }}>
          <div className="field">
            <label>Reason</label>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              {REASONS.map((r) => (
                <label
                  key={r.value}
                  className={`seg-opt ${reason === r.value ? "active" : ""}`}
                  title={r.hint}
                  style={{
                    flex: "1 1 140px",
                    border: "1px solid var(--color-divider)",
                    borderRadius: "var(--radius-md)",
                    padding: "10px 12px",
                    background: reason === r.value
                      ? "color-mix(in srgb, var(--color-accent) 14%, var(--color-surface))"
                      : "var(--color-surface)",
                    borderColor: reason === r.value
                      ? "color-mix(in srgb, var(--color-accent) 55%, transparent)"
                      : undefined,
                  }}
                >
                  <input
                    type="radio"
                    name="reason"
                    checked={reason === r.value}
                    onChange={() => setReason(r.value)}
                    style={{ display: "none" }}
                  />
                  <div style={{ fontSize: 13.5, fontWeight: 500 }}>{r.label}</div>
                  <div style={{ fontSize: 11.5, color: "var(--color-neutral-500)", marginTop: 2 }}>{r.hint}</div>
                </label>
              ))}
            </div>
          </div>

          <div className="field">
            <label>Subject</label>
            <input
              className="input"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={200}
              placeholder="A short summary"
              autoFocus
            />
          </div>

          <div className="field">
            <label>Message</label>
            <textarea
              className="input"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="What's going on? Include anything that'll save us a round trip — order ID, what you expected, what happened instead."
              style={{ minHeight: 160, resize: "vertical", lineHeight: 1.5 }}
            />
          </div>

          {error && <div className="tag tag-bad">{error}</div>}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={busy || !subject.trim() || !message.trim()}
          >
            {busy ? "Opening…" : "Open ticket"}
          </button>
        </form>
      </section>

      <div style={{ height: 76 }} />
      <SiteFooter />
    </main>
  );
}

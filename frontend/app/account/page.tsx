"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import { PasswordForm } from "@/components/account/PasswordForm";
import { EmailForm } from "@/components/account/EmailForm";
import { DeleteAccountForm } from "@/components/account/DeleteAccountForm";
import { LifeBuoy } from "@/components/icons";
import type { CurrentUser, Ticket, TicketReason, TicketStatus } from "@/lib/types";

/**
 * The account's own profile — reached by clicking your username in either
 * header — with the two things you can change about yourself (password, email),
 * and, living on the same page as a section rather than a separate destination,
 * its support tickets. Opening a new ticket is its own page (linked from here
 * and from the console's top bar); this page is where you come back to see
 * what's open.
 */

const NO_ACCOUNT =
  "You're signed in with the bootstrap operator token, which has no account " +
  "behind it. Create an account first.";
const NO_MAIL =
  "Changing your email needs email delivery, which isn't set up on this " +
  "server. Ask an operator to change it for you.";

const REASON_LABEL: Record<TicketReason, string> = {
  billing: "Billing",
  technical: "Technical",
  other: "Other",
};

const STATUS_TAG: Record<TicketStatus, string> = {
  open: "tag-warn",
  resolved: "tag-ok",
  closed: "tag-neutral",
};

const date = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

export default function AccountPage() {
  const [me, setMe] = React.useState<CurrentUser | null | undefined>(undefined);
  const [tickets, setTickets] = React.useState<Ticket[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // Whether the server can send mail. Assumed yes until it says otherwise: if
  // this lookup fails the backend still answers the email form honestly (503).
  const [mailOk, setMailOk] = React.useState(true);

  React.useEffect(() => {
    api.session()
      .then((s) => setMe(s.user ?? null))
      .catch(() => setMe(null));
    api.bootstrap()
      .then((b) => setMailOk(Boolean(b.password_reset_enabled)))
      .catch(() => {});
    api.myTickets()
      .then((r) => setTickets(r.tickets))
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load your tickets."));
  }, []);

  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav current="/account" />

      <section style={{ maxWidth: 860, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">Account</p>
        <h1 style={{ fontSize: "clamp(28px, 5vw, 40px)", margin: "0 0 14px" }}>
          Your profile.
        </h1>

        {me === undefined ? (
          <p style={{ fontSize: 14, color: "var(--color-neutral-500)" }}>Loading…</p>
        ) : me === null ? (
          <p style={{ fontSize: 14, color: "var(--color-bad)" }}>
            Could not load your account. <Link href="/login">Sign in again</Link>.
          </p>
        ) : (
          <>
            <div className="card" style={{ padding: "26px 24px", marginTop: 24 }}>
              <dl
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                  gap: 18,
                  margin: 0,
                }}
              >
                <Field label="Email" value={me.email} />
                <Field label="Username" value={me.username || "—"} />
                <Field label="Role" value={cap(me.role)} />
                <Field label="Plan" value={cap(me.tier)} />
              </dl>
              <div style={{ display: "flex", gap: 12, marginTop: 22, flexWrap: "wrap" }}>
                <Link href="/billing" className="btn btn-secondary">Billing</Link>
                <Link href="/console" className="btn btn-ghost">Back to the console</Link>
              </div>
            </div>

            <h3 style={{ fontSize: 16, margin: "44px 0 12px" }}>Security</h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
                gap: 16,
                alignItems: "start",
              }}
            >
              <PasswordForm unavailable={me.break_glass ? NO_ACCOUNT : undefined} />
              <EmailForm
                currentEmail={me.email}
                unavailable={me.break_glass ? NO_ACCOUNT : !mailOk ? NO_MAIL : undefined}
              />
            </div>

            <div style={{ marginTop: 16, maxWidth: 520 }}>
              <DeleteAccountForm unavailable={me.break_glass ? NO_ACCOUNT : undefined} />
            </div>
          </>
        )}

        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginTop: 44 }}>
          <h3 style={{ fontSize: 16, margin: 0 }}>Support tickets</h3>
          <Link href="/support" className="btn btn-primary" style={{ gap: 7 }}>
            <LifeBuoy size={13} /> New ticket
          </Link>
        </div>

        {error && <p style={{ fontSize: 13.5, color: "var(--color-bad)", marginTop: 12 }}>{error}</p>}

        {tickets === null ? (
          !error && <p style={{ fontSize: 14, color: "var(--color-neutral-500)", marginTop: 12 }}>Loading…</p>
        ) : tickets.length === 0 ? (
          <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", marginTop: 12 }}>
            No tickets yet. Something wrong, or a question about your plan? Open one above.
          </p>
        ) : (
          <div className="card" style={{ padding: 0, gap: 0, overflowX: "auto", marginTop: 12 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--color-neutral-500)" }}>
                  {["Subject", "Reason", "Status", "Updated", ""].map((h) => (
                    <th key={h} style={{ padding: "12px 16px", fontWeight: 500, whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tickets.map((t) => (
                  <tr key={t.id} style={{ borderTop: "1px solid var(--color-divider)" }}>
                    <td style={{ padding: "12px 16px" }}>{t.subject}</td>
                    <td style={{ padding: "12px 16px" }}>{REASON_LABEL[t.reason]}</td>
                    <td style={{ padding: "12px 16px" }}>
                      <span className={`tag ${STATUS_TAG[t.status]}`} style={{ fontSize: 10 }}>{t.status}</span>
                    </td>
                    <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{date(t.updated_at)}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>
                      <Link href={`/support/${t.id}`} className="btn btn-ghost" style={{ padding: "4px 10px", fontSize: 12.5 }}>
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div style={{ height: 76 }} />
      <SiteFooter />
    </main>
  );
}

function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-600)" }}>
        {label}
      </dt>
      <dd style={{ margin: "6px 0 0", fontSize: 17, color: "var(--color-text)" }}>{value}</dd>
    </div>
  );
}

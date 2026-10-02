"use client";

import * as React from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import type { CurrentUser, TicketDetail, TicketReason, TicketStatus } from "@/lib/types";

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

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });

export default function TicketPage({ params }: { params: { id: string } }) {
  const ticketId = Number(params.id);

  const [me, setMe] = React.useState<CurrentUser | null>(null);
  const [ticket, setTicket] = React.useState<TicketDetail | null>(null);
  const [notFound, setNotFound] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const [reply, setReply] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [statusBusy, setStatusBusy] = React.useState(false);

  const load = React.useCallback(() => {
    api.ticketDetail(ticketId)
      .then(setTicket)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setNotFound(true);
        else setError(e instanceof ApiError ? e.message : "Could not load this ticket.");
      });
  }, [ticketId]);

  React.useEffect(() => {
    api.session().then((s) => setMe(s.user ?? null)).catch(() => setMe(null));
    load();
  }, [load]);

  const isStaff = me?.role === "admin" || me?.role === "operator";
  // Staff viewing their OWN ticket is just the customer, same rule the
  // backend applies when deciding who a reply is "from".
  const asStaff = isStaff && ticket && me!.id !== ticket.user_id;
  const closed = ticket?.status === "closed";

  async function submitReply(e: React.FormEvent) {
    e.preventDefault();
    if (!reply.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.ticketReply(ticketId, reply.trim());
      setTicket(updated);
      setReply("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send that reply.");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: TicketStatus) {
    setStatusBusy(true);
    setError(null);
    try {
      setTicket(await api.adminSetTicketStatus(ticketId, status));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the ticket.");
    } finally {
      setStatusBusy(false);
    }
  }

  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />

      <section style={{ maxWidth: 720, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">Support</p>

        {notFound ? (
          <>
            <h1 style={{ fontSize: "clamp(26px, 5vw, 36px)", margin: "0 0 10px" }}>
              Ticket not found.
            </h1>
            <p style={{ fontSize: 14, color: "var(--color-neutral-500)" }}>
              Either it doesn&apos;t exist, or it isn&apos;t yours. <Link href="/account">Back to your tickets</Link>.
            </p>
          </>
        ) : !ticket ? (
          <p style={{ fontSize: 14, color: "var(--color-neutral-500)" }}>Loading…</p>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <h1 style={{ fontSize: "clamp(24px, 4vw, 32px)", margin: 0 }}>{ticket.subject}</h1>
              <span className={`tag ${STATUS_TAG[ticket.status]}`} style={{ fontSize: 10 }}>{ticket.status}</span>
              <span className="tag tag-outline" style={{ fontSize: 10 }}>{REASON_LABEL[ticket.reason]}</span>
            </div>
            {asStaff && (ticket.user_username || ticket.user_email) && (
              <p style={{ fontSize: 12.5, color: "var(--color-neutral-500)", margin: "6px 0 0" }}>
                from {ticket.user_username || ticket.user_email}
              </p>
            )}

            {asStaff && (
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                {ticket.status !== "resolved" && (
                  <button type="button" className="btn btn-secondary" disabled={statusBusy}
                          onClick={() => setStatus("resolved")}>
                    Mark resolved
                  </button>
                )}
                {ticket.status !== "closed" && (
                  <button type="button" className="btn btn-secondary" disabled={statusBusy}
                          onClick={() => setStatus("closed")}>
                    Close
                  </button>
                )}
                {ticket.status !== "open" && (
                  <button type="button" className="btn btn-ghost" disabled={statusBusy}
                          onClick={() => setStatus("open")}>
                    Reopen
                  </button>
                )}
              </div>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 28 }}>
              {ticket.messages.map((m) => {
                const staffMsg = Boolean(m.is_staff);
                return (
                  <div
                    key={m.id}
                    className="card"
                    style={{
                      padding: "14px 16px",
                      gap: 4,
                      alignSelf: staffMsg ? "flex-start" : "stretch",
                      background: staffMsg
                        ? "color-mix(in srgb, var(--color-accent) 8%, var(--color-surface))"
                        : undefined,
                      borderColor: staffMsg
                        ? "color-mix(in srgb, var(--color-accent) 35%, transparent)"
                        : undefined,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: staffMsg ? "var(--color-accent)" : "var(--color-text)" }}>
                        {staffMsg ? "Admin" : m.author_label}
                      </span>
                      <span style={{ fontSize: 11, color: "var(--color-neutral-600)", whiteSpace: "nowrap" }}>
                        {when(m.created_at)}
                      </span>
                    </div>
                    <p style={{ margin: 0, fontSize: 13.5, whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                      {m.body}
                    </p>
                  </div>
                );
              })}
            </div>

            {error && <div className="tag tag-bad" style={{ marginTop: 16 }}>{error}</div>}

            {closed ? (
              <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", marginTop: 20 }}>
                This ticket is closed.{" "}
                {!asStaff && <Link href="/support">Open a new one</Link>}
                {asStaff && (
                  <>
                    {" "}
                    <button type="button" className="btn btn-ghost" style={{ padding: "2px 8px" }}
                            disabled={statusBusy} onClick={() => setStatus("open")}>
                      reopen it
                    </button>
                    {" "}to keep replying.
                  </>
                )}
              </p>
            ) : (
              <form onSubmit={submitReply} style={{ marginTop: 20, display: "flex", flexDirection: "column", gap: 10 }}>
                <div className="field">
                  <label>{asStaff ? "Reply as Admin" : "Reply"}</label>
                  <textarea
                    className="input"
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    placeholder="Write a reply…"
                    style={{ minHeight: 100, resize: "vertical", lineHeight: 1.5 }}
                  />
                </div>
                <button type="submit" className="btn btn-primary" disabled={busy || !reply.trim()} style={{ alignSelf: "flex-start" }}>
                  {busy ? "Sending…" : "Send reply"}
                </button>
              </form>
            )}

            <p style={{ marginTop: 32 }}>
              <Link href="/account">← Back to your tickets</Link>
            </p>
          </>
        )}
      </section>

      <div style={{ height: 76 }} />
      <SiteFooter />
    </main>
  );
}

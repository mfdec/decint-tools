"use client";

/**
 * The staff side of the support tickets: every account's tickets in one inbox,
 * and the same thread the customer sees, with a reply box.
 *
 * Staff replies are always shown as "Admin" — the customer is told who
 * answered by role, not by which admin happened to type it (the audit log
 * still records the individual). The backend enforces that too: it snapshots
 * "Admin" as the author label on a staff reply.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Ticket, TicketDetail, TicketReason, TicketStatus } from "@/lib/types";

type Filter = TicketStatus | "all";

const REASON_LABEL: Record<TicketReason, string> = {
  billing: "Billing",
  technical: "Technical",
  other: "Other",
};

const STATUS_COLOR: Record<TicketStatus, string> = {
  open: "var(--color-warn)",
  resolved: "var(--color-ok)",
  closed: "var(--color-neutral-500)",
};

const card: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-divider)",
  borderRadius: "var(--radius-lg)",
};
const btn: React.CSSProperties = {
  fontFamily: "var(--font-body)", fontSize: 14, cursor: "pointer",
  padding: "7px 12px", borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-divider)", background: "transparent",
  color: "var(--color-text)",
};
const btnPrimary: React.CSSProperties = {
  ...btn, background: "var(--color-accent)", borderColor: "var(--color-accent)",
  color: "#fff", fontWeight: 600,
};
const input: React.CSSProperties = {
  fontFamily: "var(--font-body)", fontSize: 14, color: "var(--color-text)",
  background: "var(--color-bg)", border: "1px solid var(--color-divider)",
  borderRadius: "var(--radius-md)", padding: "7px 10px", width: "100%",
};

function Pill({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      fontSize: 12, fontFamily: "var(--mono)", color, border: `1px solid ${color}`,
      borderRadius: "var(--radius-sm)", padding: "1px 6px", whiteSpace: "nowrap",
    }}>{text}</span>
  );
}

const when = (iso: string) => iso.slice(0, 16).replace("T", " ");

export function SupportInbox({
  meId, onChanged, flash,
}: {
  meId: number;
  /** Called after anything that changes how many tickets are open. */
  onChanged: () => void;
  flash: (kind: "ok" | "err", text: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("open");
  const [rows, setRows] = useState<Ticket[] | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);

  const loadList = useCallback(() => {
    api.adminTickets(filter === "all" ? undefined : filter)
      .then((r) => setRows(r.tickets))
      .catch((e) => flash("err", e instanceof ApiError ? e.message : "Failed to load tickets"));
  }, [filter, flash]);

  useEffect(() => { loadList(); }, [loadList]);

  useEffect(() => {
    setTicket(null);
    setReply("");
    if (openId === null) return;
    api.ticketDetail(openId)
      .then(setTicket)
      .catch((e) => {
        flash("err", e instanceof ApiError ? e.message : "Failed to load the ticket");
        setOpenId(null);
      });
  }, [openId, flash]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!ticket || !reply.trim()) return;
    setBusy(true);
    try {
      setTicket(await api.ticketReply(ticket.id, reply.trim()));
      setReply("");
      flash("ok", "Reply sent");
      loadList();
      onChanged();
    } catch (err) {
      flash("err", err instanceof ApiError ? err.message : "Could not send the reply");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: TicketStatus) {
    if (!ticket) return;
    setBusy(true);
    try {
      setTicket(await api.adminSetTicketStatus(ticket.id, status));
      flash("ok", `Ticket ${status === "open" ? "reopened" : status}`);
      loadList();
      onChanged();
    } catch (err) {
      flash("err", err instanceof ApiError ? err.message : "Could not update the ticket");
    } finally {
      setBusy(false);
    }
  }

  // ── a single ticket ──
  if (openId !== null) {
    // Backend rule: a reply on your OWN ticket is a customer reply, not staff.
    const own = ticket ? ticket.user_id === meId : false;
    return (
      <div>
        <button style={{ ...btn, marginBottom: 14 }} onClick={() => { setOpenId(null); loadList(); }}>
          ← All tickets
        </button>

        {!ticket ? (
          <p style={{ opacity: 0.6 }}>Loading…</p>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <h2 style={{ fontSize: 20, margin: 0 }}>{ticket.subject}</h2>
              <Pill text={ticket.status} color={STATUS_COLOR[ticket.status]} />
              <Pill text={REASON_LABEL[ticket.reason]} color="var(--color-text)" />
            </div>
            <p style={{ fontSize: 13, opacity: 0.6, margin: "6px 0 0" }}>
              from {ticket.user_username ? `${ticket.user_username} · ` : ""}{ticket.user_email}
              {" · opened "}{when(ticket.created_at)}
            </p>

            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
              {ticket.status !== "resolved" && (
                <button style={btn} disabled={busy} onClick={() => setStatus("resolved")}>Mark resolved</button>
              )}
              {ticket.status !== "closed" && (
                <button style={btn} disabled={busy} onClick={() => setStatus("closed")}>Close</button>
              )}
              {ticket.status !== "open" && (
                <button style={btn} disabled={busy} onClick={() => setStatus("open")}>Reopen</button>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 20, maxWidth: 760 }}>
              {ticket.messages.map((m) => {
                const staff = Boolean(m.is_staff);
                return (
                  <div key={m.id} style={{
                    ...card, padding: "12px 14px",
                    borderColor: staff ? "color-mix(in srgb, var(--color-accent) 40%, transparent)" : undefined,
                    background: staff ? "color-mix(in srgb, var(--color-accent) 8%, var(--color-surface))" : undefined,
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 4 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: staff ? "var(--color-accent)" : undefined }}>
                        {staff ? "Admin" : m.author_label}
                      </span>
                      <span style={{ fontSize: 12, opacity: 0.5, fontFamily: "var(--mono)" }}>{when(m.created_at)}</span>
                    </div>
                    <p style={{ margin: 0, fontSize: 14, whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{m.body}</p>
                  </div>
                );
              })}
            </div>

            {ticket.status === "closed" ? (
              <p style={{ fontSize: 13, opacity: 0.6, marginTop: 18 }}>
                This ticket is closed — reopen it to reply.
              </p>
            ) : (
              <form onSubmit={send} style={{ marginTop: 18, maxWidth: 760 }}>
                <label style={{ fontSize: 12, opacity: 0.7, display: "block", marginBottom: 4 }}>
                  {own ? "Reply (this is your own ticket, so it posts as you, not as Admin)" : "Reply as Admin"}
                </label>
                <textarea
                  style={{ ...input, minHeight: 110, resize: "vertical", lineHeight: 1.5 }}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder="Write a reply…"
                />
                <button type="submit" style={{ ...btnPrimary, marginTop: 10 }} disabled={busy || !reply.trim()}>
                  {busy ? "Sending…" : "Send reply"}
                </button>
              </form>
            )}
          </>
        )}
      </div>
    );
  }

  // ── the inbox ──
  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        {(["open", "resolved", "closed", "all"] as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)} style={{
            ...btn, textTransform: "capitalize",
            borderColor: filter === f ? "var(--color-accent)" : undefined,
            opacity: filter === f ? 1 : 0.65,
          }}>{f}</button>
        ))}
      </div>

      <div style={{ ...card, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ textAlign: "left", fontSize: 12, opacity: 0.6 }}>
              {["Subject", "From", "Reason", "Status", "Msgs", "Updated"].map((h) => (
                <th key={h} style={{ padding: "10px 12px", fontWeight: 500, borderBottom: "1px solid var(--color-divider)" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((t) => (
              <tr key={t.id} onClick={() => setOpenId(t.id)}
                style={{ cursor: "pointer", borderBottom: "1px solid var(--color-divider)" }}>
                <td style={{ padding: "10px 12px" }}>{t.subject}</td>
                <td style={{ padding: "10px 12px", opacity: 0.8 }}>{t.user_username || t.user_email}</td>
                <td style={{ padding: "10px 12px" }}>{REASON_LABEL[t.reason]}</td>
                <td style={{ padding: "10px 12px" }}><Pill text={t.status} color={STATUS_COLOR[t.status]} /></td>
                <td style={{ padding: "10px 12px", fontFamily: "var(--mono)", opacity: 0.7 }}>{t.message_count}</td>
                <td style={{ padding: "10px 12px", fontSize: 12, opacity: 0.7, fontFamily: "var(--mono)", whiteSpace: "nowrap" }}>{when(t.updated_at)}</td>
              </tr>
            ))}
            {rows !== null && rows.length === 0 && (
              <tr><td colSpan={6} style={{ padding: 24, textAlign: "center", opacity: 0.5 }}>
                No {filter === "all" ? "" : `${filter} `}tickets.
              </td></tr>
            )}
            {rows === null && (
              <tr><td colSpan={6} style={{ padding: 24, textAlign: "center", opacity: 0.5 }}>Loading…</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

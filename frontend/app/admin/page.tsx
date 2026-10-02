"use client";

/**
 * Admin console — account and site management only.
 *
 * Talks exclusively to the require_admin-gated /api/v1/admin/* endpoints
 * (users, stats, audit) via the shared api client. It deliberately contains no
 * executor / stressor / methods / fleet functionality: those live on a separate
 * branch and are not part of this application.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AdminUser, AdminStats, AuditEntry, CurrentUser } from "@/lib/types";
import { SupportInbox } from "@/components/admin/SupportInbox";

type Gate = "checking" | "denied" | "ok";
type Tab = "users" | "support" | "audit";

const card: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-divider)",
  borderRadius: "var(--radius-lg)",
};

function Badge({ text, tone }: { text: string; tone?: "ok" | "warn" | "bad" | "muted" }) {
  const color =
    tone === "ok" ? "var(--color-ok)" :
    tone === "warn" ? "var(--color-warn)" :
    tone === "bad" ? "var(--color-bad)" : "var(--color-text)";
  return (
    <span style={{
      fontSize: 12, fontFamily: "var(--mono)", color,
      border: `1px solid ${color}`, borderRadius: "var(--radius-sm)",
      padding: "1px 6px", opacity: tone === "muted" ? 0.6 : 1, whiteSpace: "nowrap",
    }}>{text}</span>
  );
}

function statusTone(s: string): "ok" | "warn" | "bad" | "muted" {
  if (s === "active") return "ok";
  if (s === "pending") return "warn";
  if (s === "suspended") return "bad";
  return "muted";
}

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
const btnDanger: React.CSSProperties = { ...btn, color: "var(--color-bad)", borderColor: "var(--color-bad)" };
const input: React.CSSProperties = {
  fontFamily: "var(--font-body)", fontSize: 14, color: "var(--color-text)",
  background: "var(--color-bg)", border: "1px solid var(--color-divider)",
  borderRadius: "var(--radius-md)", padding: "7px 10px", width: "100%",
};
const label: React.CSSProperties = { fontSize: 12, opacity: 0.7, display: "block", marginBottom: 4 };

export default function AdminPage() {
  const [gate, setGate] = useState<Gate>("checking");
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [tab, setTab] = useState<Tab>("users");

  const [stats, setStats] = useState<AdminStats | null>(null);
  const [rows, setRows] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [fRole, setFRole] = useState("");
  const [fTier, setFTier] = useState("");
  const [fStatus, setFStatus] = useState("");

  const [sel, setSel] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  // How many support tickets are waiting on an answer — shown on the menu button.
  const [openTickets, setOpenTickets] = useState<number | null>(null);

  const flash = useCallback((kind: "ok" | "err", text: string) => {
    setMsg({ kind, text });
    setTimeout(() => setMsg(null), 4000);
  }, []);

  const loadTicketCount = useCallback(() => {
    api.adminTickets("open").then((r) => setOpenTickets(r.tickets.length)).catch(() => {});
  }, []);

  // ── gate: must be signed in AND role=admin ──
  useEffect(() => {
    api.session()
      .then((s) => {
        if (s.authenticated && s.user && s.user.role === "admin") {
          setMe(s.user); setGate("ok");
        } else {
          setGate("denied");
        }
      })
      .catch(() => setGate("denied"));
  }, []);

  const loadStats = useCallback(() => {
    api.adminStats().then(setStats).catch(() => {});
  }, []);

  const loadUsers = useCallback(() => {
    const params: Record<string, string> = {};
    if (q) params.q = q;
    if (fRole) params.role = fRole;
    if (fTier) params.tier = fTier;
    if (fStatus) params.status = fStatus;
    api.adminUsers(params)
      .then((r) => { setRows(r.users); setTotal(r.total); })
      .catch((e) => flash("err", e instanceof ApiError ? e.message : "Failed to load users"));
  }, [q, fRole, fTier, fStatus, flash]);

  useEffect(() => { if (gate === "ok") { loadStats(); loadUsers(); } }, [gate, loadStats, loadUsers]);
  useEffect(() => { if (gate === "ok") loadTicketCount(); }, [gate, loadTicketCount]);
  useEffect(() => { if (gate === "ok" && tab === "audit") api.adminAudit(200).then((r) => setAudit(r.entries)).catch(() => {}); }, [gate, tab]);

  const refresh = useCallback(() => { loadStats(); loadUsers(); if (sel) api.adminUser(sel.id).then(setSel).catch(() => {}); }, [loadStats, loadUsers, sel]);

  if (gate === "checking") {
    return <Centered>Checking access…</Centered>;
  }
  if (gate === "denied") {
    return (
      <Centered>
        <div style={{ textAlign: "center" }}>
          <p style={{ fontSize: 18, marginBottom: 8 }}>Admins only</p>
          <p style={{ opacity: 0.7, marginBottom: 16 }}>This area requires an administrator account.</p>
          <a href="/console" style={{ ...btn, textDecoration: "none" }}>Back to console</a>
        </div>
      </Centered>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)" }}>
      {/* header */}
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "14px 20px", borderBottom: "1px solid var(--color-divider)", position: "sticky", top: 0,
        background: "var(--color-bg)", zIndex: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontWeight: 700, letterSpacing: "var(--wordmark-track)" }}>DECINT</span>
          <span style={{ opacity: 0.5 }}>·</span>
          <span style={{ opacity: 0.8 }}>Admin</span>
          <nav style={{ display: "flex", gap: 4, marginLeft: 16 }}>
            {(["users", "support", "audit"] as Tab[]).map((t) => (
              <button key={t} onClick={() => setTab(t)} style={{
                ...btn, borderColor: tab === t ? "var(--color-accent)" : "transparent",
                color: tab === t ? "var(--color-text)" : "var(--color-text)", opacity: tab === t ? 1 : 0.6,
                textTransform: "capitalize",
              }}>
                {t}
                {t === "support" && openTickets ? (
                  <span style={{
                    marginLeft: 7, fontSize: 11, fontFamily: "var(--mono)", color: "var(--color-warn)",
                    border: "1px solid var(--color-warn)", borderRadius: "var(--radius-sm)", padding: "0 5px",
                  }}>{openTickets}</span>
                ) : null}
              </button>
            ))}
          </nav>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
          <span style={{ opacity: 0.6 }}>{me?.email}</span>
          <a href="/console" style={{ ...btn, textDecoration: "none", padding: "5px 10px" }}>Console</a>
          <button style={{ ...btn, padding: "5px 10px" }} onClick={() => api.logout().finally(() => (window.location.href = "/login"))}>Sign out</button>
        </div>
      </header>

      {msg && (
        <div style={{ position: "fixed", bottom: 20, left: "50%", transform: "translateX(-50%)", zIndex: 50,
          background: msg.kind === "ok" ? "var(--color-ok)" : "var(--color-bad)", color: "#0b0c13",
          padding: "10px 16px", borderRadius: "var(--radius-md)", fontSize: 14, fontWeight: 600, boxShadow: "var(--shadow-md)" }}>
          {msg.text}
        </div>
      )}

      <main style={{ maxWidth: 1180, margin: "0 auto", padding: "22px 20px 80px" }}>
        {tab === "users" && (
          <>
            {/* stat tiles */}
            {stats && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 22 }}>
                <Tile k="Users" v={stats.total} />
                {stats.billing && (
                  <Tile
                    k="Paying customers"
                    v={stats.billing.paying}
                    tone="good"
                    sub={tierBreakdown(stats.billing.paying_by_tier) || `of ${stats.billing.customers} customers`}
                  />
                )}
                {stats.billing && (
                  <Tile
                    k="Paid share"
                    v={`${stats.billing.paid_pct}%`}
                    sub={`${stats.billing.paying} paid · ${stats.billing.free} free`}
                  />
                )}
                <Tile k="With MFA" v={stats.with_mfa} />
                <Tile k="Active sessions" v={stats.active_sessions} />
                <Tile k="Pending" v={stats.by_status.pending || 0} tone={(stats.by_status.pending || 0) > 0 ? "warn" : undefined} />
              </div>
            )}

            {/* controls */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 14 }}>
              <input style={{ ...input, maxWidth: 260 }} placeholder="Search email / username / notes"
                value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && loadUsers()} />
              <Select value={fRole} onChange={setFRole} placeholder="role" options={stats?.roles || []} />
              <Select value={fTier} onChange={setFTier} placeholder="tier" options={stats?.tiers || []} />
              <Select value={fStatus} onChange={setFStatus} placeholder="status" options={stats?.statuses || []} />
              <button style={btn} onClick={loadUsers}>Apply</button>
              <div style={{ flex: 1 }} />
              <button style={btnPrimary} onClick={() => { setCreating(true); setSel(null); }}>+ New user</button>
            </div>

            <div style={{ ...card, overflow: "hidden" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
                <thead>
                  <tr style={{ textAlign: "left", fontSize: 12, opacity: 0.6 }}>
                    {["Email", "Role", "Tier", "Status", "MFA", "Created", "Last login"].map((h) => (
                      <th key={h} style={{ padding: "10px 12px", fontWeight: 500, borderBottom: "1px solid var(--color-divider)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((u) => {
                    const mfa = u.totp_enabled || u.email_otp_enabled || u.sms_otp_enabled;
                    return (
                      <tr key={u.id} onClick={() => { setSel(u); setCreating(false); }}
                        style={{ cursor: "pointer", borderBottom: "1px solid var(--color-divider)", background: sel?.id === u.id ? "var(--color-bg)" : "transparent" }}>
                        <td style={{ padding: "10px 12px" }}>
                          <div>{u.email}</div>
                          {u.username && <div style={{ fontSize: 12, opacity: 0.5 }}>{u.username}</div>}
                        </td>
                        <td style={{ padding: "10px 12px" }}><Badge text={u.role} tone={u.role === "admin" ? "warn" : "muted"} /></td>
                        <td style={{ padding: "10px 12px" }}>{u.tier}</td>
                        <td style={{ padding: "10px 12px" }}><Badge text={u.status} tone={statusTone(u.status)} /></td>
                        <td style={{ padding: "10px 12px" }}>{mfa ? <Badge text="on" tone="ok" /> : <span style={{ opacity: 0.4 }}>—</span>}</td>
                        <td style={{ padding: "10px 12px", fontSize: 12, opacity: 0.7, fontFamily: "var(--mono)" }}>{(u.created_at || "").slice(0, 10)}</td>
                        <td style={{ padding: "10px 12px", fontSize: 12, opacity: 0.7, fontFamily: "var(--mono)" }}>{u.last_login_at ? u.last_login_at.slice(0, 16).replace("T", " ") : "—"}</td>
                      </tr>
                    );
                  })}
                  {rows.length === 0 && (
                    <tr><td colSpan={7} style={{ padding: 24, textAlign: "center", opacity: 0.5 }}>No users match.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <p style={{ fontSize: 12, opacity: 0.5, marginTop: 8 }}>{rows.length} of {total} shown</p>
          </>
        )}

        {tab === "support" && me && (
          <SupportInbox meId={me.id} onChanged={loadTicketCount} flash={flash} />
        )}

        {tab === "audit" && (
          <div style={{ ...card, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", fontSize: 12, opacity: 0.6 }}>
                  {["When", "Actor", "Action", "Target", "Detail", "IP"].map((h) => (
                    <th key={h} style={{ padding: "10px 12px", fontWeight: 500, borderBottom: "1px solid var(--color-divider)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {audit.map((a) => (
                  <tr key={a.id} style={{ borderBottom: "1px solid var(--color-divider)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)", fontSize: 12, opacity: 0.8, whiteSpace: "nowrap" }}>{(a.ts || "").slice(0, 19).replace("T", " ")}</td>
                    <td style={{ padding: "8px 12px", opacity: 0.85 }}>{a.actor || "—"}</td>
                    <td style={{ padding: "8px 12px" }}><Badge text={a.action} tone="muted" /></td>
                    <td style={{ padding: "8px 12px", opacity: 0.85 }}>{a.target || "—"}</td>
                    <td style={{ padding: "8px 12px", fontSize: 12, opacity: 0.6 }}>{a.detail || ""}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "var(--mono)", fontSize: 12, opacity: 0.6 }}>{a.ip || "—"}</td>
                  </tr>
                ))}
                {audit.length === 0 && <tr><td colSpan={6} style={{ padding: 24, textAlign: "center", opacity: 0.5 }}>No audit entries.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {(sel || creating) && (
        <UserPanel
          key={sel?.id ?? "new"}
          user={sel}
          creating={creating}
          stats={stats}
          meId={me?.id}
          onClose={() => { setSel(null); setCreating(false); }}
          onFlash={flash}
          onChanged={() => { refresh(); }}
        />
      )}
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center",
      background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)" }}>
      {children}
    </div>
  );
}

function Tile({ k, v, tone, sub }: { k: string; v: number | string; tone?: "warn" | "good"; sub?: string }) {
  const color = tone === "warn" ? "var(--color-warn)" : tone === "good" ? "var(--color-accent)" : "var(--color-text)";
  return (
    <div style={{ ...card, padding: "14px 16px" }}>
      <div style={{ fontSize: 12, opacity: 0.6 }}>{k}</div>
      <div style={{ fontSize: 26, fontWeight: 600, color }}>{v}</div>
      {sub && <div style={{ fontSize: 11, opacity: 0.5, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// "starter: 2, pro: 1" → "2 Starter · 1 Pro", for the paying-customers tile.
function tierBreakdown(byTier: Record<string, number>): string {
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  return Object.entries(byTier)
    .filter(([, n]) => n > 0)
    .map(([tier, n]) => `${n} ${cap(tier)}`)
    .join(" · ");
}

function Select({ value, onChange, placeholder, options }:
  { value: string; onChange: (v: string) => void; placeholder: string; options: string[] }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      style={{ ...input, width: "auto", minWidth: 120, textTransform: "capitalize" }}>
      <option value="">{`all ${placeholder}`}</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

// ── side panel: create or edit a single user ──
function UserPanel({ user, creating, stats, meId, onClose, onFlash, onChanged }: {
  user: AdminUser | null;
  creating: boolean;
  stats: AdminStats | null;
  meId?: number;
  onClose: () => void;
  onFlash: (k: "ok" | "err", t: string) => void;
  onChanged: () => void;
}) {
  const [form, setForm] = useState({
    email: user?.email ?? "",
    username: user?.username ?? "",
    role: user?.role ?? "user",
    tier: user?.tier ?? "free",
    status: user?.status ?? "active",
    phone: user?.phone ?? "",
    notes: user?.notes ?? "",
    password: "",
  });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const isSelf = !creating && user?.id === meId;

  const err = (e: unknown) => onFlash("err", e instanceof ApiError ? e.message : "Something went wrong");

  async function save() {
    setBusy(true);
    try {
      if (creating) {
        await api.adminCreateUser({
          email: form.email, password: form.password, role: form.role,
          tier: form.tier, status: form.status,
          username: form.username || null, phone: form.phone || null, notes: form.notes || null,
        });
        onFlash("ok", "User created");
      } else if (user) {
        await api.adminUpdateUser(user.id, {
          email: form.email, username: form.username || null, role: form.role,
          tier: form.tier, status: form.status, phone: form.phone || null, notes: form.notes || null,
        });
        onFlash("ok", "Saved");
      }
      onChanged();
      if (creating) onClose();
    } catch (e) { err(e); } finally { setBusy(false); }
  }

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try { await fn(); onFlash("ok", ok); onChanged(); } catch (e) { err(e); } finally { setBusy(false); }
  }

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 40 }} />
      <aside style={{ position: "fixed", top: 0, right: 0, height: "100vh", width: "min(440px, 100vw)",
        background: "var(--color-surface)", borderLeft: "1px solid var(--color-divider)", zIndex: 41,
        overflowY: "auto", padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: 18, margin: 0 }}>{creating ? "New user" : form.email}</h2>
          <button style={{ ...btn, padding: "4px 10px" }} onClick={onClose}>✕</button>
        </div>
        {!creating && user && (
          <div style={{ fontSize: 12, opacity: 0.5, fontFamily: "var(--mono)" }}>
            id {user.id} · created {(user.created_at || "").slice(0, 10)}
            {user.last_login_ip && ` · last ip ${user.last_login_ip}`}
          </div>
        )}

        <Field label="Email"><input style={input} value={form.email} onChange={(e) => set("email", e.target.value)} /></Field>
        <Field label="Username"><input style={input} value={form.username} onChange={(e) => set("username", e.target.value)} /></Field>
        <div style={{ display: "flex", gap: 10 }}>
          <Field label="Role" flex><PickOne value={form.role} onChange={(v) => set("role", v)} options={stats?.roles || ["user", "admin"]} /></Field>
          <Field label="Tier" flex><PickOne value={form.tier} onChange={(v) => set("tier", v)} options={stats?.tiers || ["free"]} /></Field>
        </div>
        <Field label="Status"><PickOne value={form.status} onChange={(v) => set("status", v)} options={stats?.statuses || ["active", "pending", "suspended"]} /></Field>
        <Field label="Phone"><input style={input} value={form.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
        <Field label="Notes"><textarea style={{ ...input, minHeight: 60, resize: "vertical" }} value={form.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        {creating && (
          <Field label="Password"><input style={input} type="text" value={form.password} onChange={(e) => set("password", e.target.value)} placeholder="set an initial password" /></Field>
        )}
        {isSelf && <p style={{ fontSize: 12, color: "var(--color-warn)" }}>This is your own account — you can’t remove your admin role or suspend yourself.</p>}

        <button style={{ ...btnPrimary, marginTop: 4 }} disabled={busy} onClick={save}>{creating ? "Create user" : "Save changes"}</button>

        {!creating && user && (
          <>
            <div style={{ borderTop: "1px solid var(--color-divider)", margin: "6px 0" }} />
            <span style={{ ...label, marginBottom: 0 }}>Account actions</span>
            <SetPasswordRow onSet={(pw) => act(() => api.adminSetPassword(user.id, pw), "Password set; sessions signed out")} busy={busy} />
            <button style={btn} disabled={busy} onClick={() => act(() => api.adminResetMfa(user.id), "MFA reset")}>Reset MFA</button>
            <button style={btn} disabled={busy} onClick={() => act(() => api.adminRevokeSessions(user.id), "Sessions revoked")}>Revoke all sessions</button>
            <button style={btnDanger} disabled={busy || isSelf}
              onClick={() => { if (confirm(`Delete ${user.email}? This cannot be undone.`)) act(() => api.adminDeleteUser(user.id), "User deleted"); }}>
              Delete user
            </button>
          </>
        )}
      </aside>
    </>
  );
}

function Field({ label: l, children, flex }: { label: string; children: React.ReactNode; flex?: boolean }) {
  return <div style={{ flex: flex ? 1 : undefined }}><span style={label}>{l}</span>{children}</div>;
}

function PickOne({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={{ ...input, textTransform: "capitalize" }}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function SetPasswordRow({ onSet, busy }: { onSet: (pw: string) => void; busy: boolean }) {
  const [pw, setPw] = useState("");
  const [open, setOpen] = useState(false);
  if (!open) return <button style={btn} disabled={busy} onClick={() => setOpen(true)}>Set password…</button>;
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <input style={input} value={pw} onChange={(e) => setPw(e.target.value)} placeholder="new password" />
      <button style={btnPrimary} disabled={busy || pw.length < 1} onClick={() => { onSet(pw); setPw(""); setOpen(false); }}>Set</button>
    </div>
  );
}

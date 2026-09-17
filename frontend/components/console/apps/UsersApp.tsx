"use client";

import * as React from "react";
import { api } from "@/lib/api";
import type { AdminStats, AdminUser, AuditEntry } from "@/lib/types";

type Tab = "users" | "audit";

const ROLE_TAG: Record<string, string> = {
  admin: "tag-bad", operator: "tag-warn", user: "tag-neutral",
};
const STATUS_TAG: Record<string, string> = {
  active: "tag-ok", suspended: "tag-bad", pending: "tag-warn",
};

export function UsersApp() {
  const [tab, setTab] = React.useState<Tab>("users");
  const [stats, setStats] = React.useState<AdminStats | null>(null);
  const [rows, setRows] = React.useState<AdminUser[]>([]);
  const [audit, setAudit] = React.useState<AuditEntry[]>([]);
  const [q, setQ] = React.useState("");
  const [fRole, setFRole] = React.useState("");
  const [fTier, setFTier] = React.useState("");
  const [fStatus, setFStatus] = React.useState("");
  const [sel, setSel] = React.useState<AdminUser | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [msg, setMsg] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      const params: Record<string, string> = {};
      if (q) params.q = q;
      if (fRole) params.role = fRole;
      if (fTier) params.tier = fTier;
      if (fStatus) params.status = fStatus;
      const [u, s] = await Promise.all([api.adminUsers(params), api.adminStats()]);
      setRows(u.users); setStats(s);
      if (tab === "audit") setAudit((await api.adminAudit(200)).entries);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to load");
    } finally { setBusy(false); }
  }, [q, fRole, fTier, fStatus, tab]);

  React.useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<unknown>, okMsg: string) {
    setErr(null); setMsg(null);
    try { await fn(); setMsg(okMsg); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
  }

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--color-neutral-600)" }}>
          ~/users$ admin
        </span>
        <div className="seg">
          {(["users", "audit"] as Tab[]).map((t) => (
            <label key={t} className={`seg-opt ${tab === t ? "active" : ""}`}>
              <input type="radio" checked={tab === t} onChange={() => setTab(t)} style={{ display: "none" }} />{t}
            </label>
          ))}
        </div>
        <button className="btn btn-primary" style={{ height: 30 }} onClick={() => setCreating(true)}>
          + New account
        </button>
        <button className="btn btn-secondary" style={{ height: 30 }} onClick={load} disabled={busy}>
          {busy ? "…" : "Refresh"}
        </button>
      </div>

      {err && <div className="tag tag-bad" style={{ marginBottom: 10 }}>{err}</div>}
      {msg && <div className="tag tag-ok" style={{ marginBottom: 10 }}>{msg}</div>}

      {stats && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginBottom: 18 }}>
          <Stat label="Accounts" value={stats.total} />
          <Stat label="With 2FA" value={stats.with_mfa} />
          <Stat label="Active sessions" value={stats.active_sessions} />
          <Group label="By tier" data={stats.by_tier} />
          <Group label="By role" data={stats.by_role} />
          <Group label="By status" data={stats.by_status} />
        </div>
      )}

      {tab === "users" && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            <input className="input" placeholder="search email, username, notes…" value={q}
                   onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} />
            <Select value={fRole} onChange={setFRole} opts={stats?.roles || []} label="role" />
            <Select value={fTier} onChange={setFTier} opts={stats?.tiers || []} label="tier" />
            <Select value={fStatus} onChange={setFStatus} opts={stats?.statuses || []} label="status" />
          </div>

          <div style={{ border: "1px solid var(--color-divider)", borderRadius: 10, overflow: "auto" }}>
            <table className="table">
              <thead>
                <tr><th>ID</th><th>Email</th><th>Role</th><th>Tier</th><th>Status</th>
                    <th>2FA</th><th>Last login</th><th></th></tr>
              </thead>
              <tbody>
                {rows.map((u) => (
                  <tr key={u.id}>
                    <td style={{ color: "var(--color-neutral-600)" }}>{u.id}</td>
                    <td style={{ color: "var(--color-neutral-200)" }}>{u.email}</td>
                    <td><span className={`tag ${ROLE_TAG[u.role] || "tag-neutral"}`} style={{ fontSize: 10 }}>{u.role}</span></td>
                    <td><span className="tag tag-outline" style={{ fontSize: 10 }}>{u.tier}</span></td>
                    <td><span className={`tag ${STATUS_TAG[u.status] || "tag-neutral"}`} style={{ fontSize: 10 }}>{u.status}</span></td>
                    <td style={{ color: "var(--color-neutral-500)" }}>
                      {[u.totp_enabled && "totp", u.email_otp_enabled && "email", u.sms_otp_enabled && "sms"]
                        .filter(Boolean).join(",") || "—"}
                    </td>
                    <td style={{ color: "var(--color-neutral-600)" }}>
                      {u.last_login_at ? u.last_login_at.slice(0, 16).replace("T", " ") : "never"}
                    </td>
                    <td>
                      <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => setSel(u)}>manage</button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={8} style={{ color: "var(--color-neutral-500)", fontFamily: "var(--font-body)" }}>
                    No accounts match.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "audit" && (
        <div style={{ border: "1px solid var(--color-divider)", borderRadius: 10, overflow: "auto" }}>
          <table className="table">
            <thead><tr><th>Time</th><th>Action</th><th>Actor</th><th>Target</th><th>Detail</th><th>IP</th></tr></thead>
            <tbody>
              {audit.map((a) => (
                <tr key={a.id}>
                  <td style={{ color: "var(--color-neutral-600)", whiteSpace: "nowrap" }}>{a.ts.slice(0, 19).replace("T", " ")}</td>
                  <td style={{ color: a.action.includes("failed") ? "#e8908f" : "var(--color-accent-300)" }}>{a.action}</td>
                  <td style={{ color: "var(--color-neutral-300)" }}>{a.actor || "—"}</td>
                  <td style={{ color: "var(--color-neutral-400)" }}>{a.target || "—"}</td>
                  <td style={{ color: "var(--color-neutral-600)", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.detail || "—"}</td>
                  <td style={{ color: "var(--color-neutral-600)" }}>{a.ip || "—"}</td>
                </tr>
              ))}
              {audit.length === 0 && <tr><td colSpan={6} style={{ color: "var(--color-neutral-500)", fontFamily: "var(--font-body)" }}>Nothing recorded yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {sel && (
        <ManageDialog
          user={sel}
          stats={stats}
          onClose={() => setSel(null)}
          onAct={act}
        />
      )}
      {creating && (
        <CreateDialog
          stats={stats}
          onClose={() => setCreating(false)}
          onDone={async () => { setCreating(false); await load(); }}
          onError={setErr}
        />
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card" style={{ padding: "12px 14px", gap: 2 }}>
      <div style={{ fontSize: 24, fontWeight: 600, color: "var(--color-accent-300)" }}>{value}</div>
      <div style={{ fontSize: 11.5, color: "var(--color-neutral-500)" }}>{label}</div>
    </div>
  );
}

function Group({ label, data }: { label: string; data: Record<string, number> }) {
  return (
    <div className="card" style={{ padding: "12px 14px", gap: 4 }}>
      <div className="card-kicker">{label}</div>
      {Object.entries(data).map(([k, v]) => (
        <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--color-neutral-400)" }}>{k}</span>
          <span style={{ fontFamily: "var(--mono)", color: "var(--color-neutral-200)" }}>{v}</span>
        </div>
      ))}
      {Object.keys(data).length === 0 && <span style={{ fontSize: 12, color: "var(--color-neutral-600)" }}>—</span>}
    </div>
  );
}

function Select({ value, onChange, opts, label }: {
  value: string; onChange: (v: string) => void; opts: string[]; label: string;
}) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}
            style={{ width: "auto", minHeight: 36 }}>
      <option value="">all {label}s</option>
      {opts.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function ManageDialog({ user, stats, onClose, onAct }: {
  user: AdminUser;
  stats: AdminStats | null;
  onClose: () => void;
  onAct: (fn: () => Promise<unknown>, msg: string) => Promise<void>;
}) {
  const [role, setRole] = React.useState(user.role);
  const [tier, setTier] = React.useState(user.tier);
  const [status, setStatus] = React.useState(user.status);
  const [pw, setPw] = React.useState("");

  return (
    <div className="dialog-backdrop" onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 300, display: "grid", placeItems: "center", background: "color-mix(in srgb, black 55%, transparent)" }}>
      <div className="dialog" onClick={(e) => e.stopPropagation()} style={{ width: "min(460px, 92vw)", maxHeight: "86vh", overflow: "auto" }}>
        <div className="dialog-title">{user.email}</div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-600)" }}>
          #{user.id} · created {user.created_at?.slice(0, 10)} · last IP {user.last_login_ip || "—"}
        </div>

        <div className="field"><label>Role</label>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
            {(stats?.roles || []).map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div className="field"><label>Tier</label>
          <select className="input" value={tier} onChange={(e) => setTier(e.target.value)}>
            {(stats?.tiers || []).map((t) => (
              <option key={t} value={t}>
                {t}
                {stats?.tier_quota?.[t] == null
                  ? " — unmetered"
                  : stats.tier_quota_window?.[t] === "lifetime"
                    ? ` — ${stats.tier_quota[t]} searches, ever`
                    : ` — ${stats.tier_quota[t]} searches/mo`}
              </option>
            ))}
          </select>
        </div>
        <div className="field"><label>Status</label>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {(stats?.statuses || []).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        <button className="btn btn-primary btn-block"
                onClick={() => onAct(() => api.adminUpdateUser(user.id, { role, tier, status }), "Account updated.").then(onClose)}>
          Save changes
        </button>

        <div className="hr" />

        <div className="field"><label>Set a new password</label>
          <input className="input" type="password" value={pw} onChange={(e) => setPw(e.target.value)}
                 placeholder="min 12 characters" />
        </div>
        <button className="btn btn-secondary btn-block" disabled={pw.length < 12}
                onClick={() => onAct(() => api.adminSetPassword(user.id, pw), "Password reset; their sessions were signed out.").then(onClose)}>
          Reset password
        </button>

        <div className="hr" />

        <button className="btn btn-secondary btn-block"
                onClick={() => onAct(() => api.adminResetMfa(user.id), "2FA cleared — they can re-enrol.")}>
          Reset 2FA
        </button>
        <button className="btn btn-secondary btn-block"
                onClick={() => onAct(() => api.adminRevokeSessions(user.id), "Sessions revoked.")}>
          Sign out everywhere
        </button>
        <button className="btn btn-block" style={{ color: "#e8908f", borderColor: "#e8908f" }}
                onClick={() => {
                  if (confirm(`Permanently delete ${user.email}? This cannot be undone.`)) {
                    onAct(() => api.adminDeleteUser(user.id), "Account deleted.").then(onClose);
                  }
                }}>
          Delete account
        </button>

        <button className="btn btn-ghost btn-block" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

function CreateDialog({ stats, onClose, onDone, onError }: {
  stats: AdminStats | null;
  onClose: () => void;
  onDone: () => void;
  onError: (m: string) => void;
}) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [role, setRole] = React.useState("user");
  const [tier, setTier] = React.useState("free");
  const [busy, setBusy] = React.useState(false);

  async function submit() {
    setBusy(true);
    try { await api.adminCreateUser({ email, password, role, tier }); onDone(); }
    catch (e) { onError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 300, display: "grid", placeItems: "center", background: "color-mix(in srgb, black 55%, transparent)" }}>
      <div className="dialog" onClick={(e) => e.stopPropagation()} style={{ width: "min(420px, 92vw)" }}>
        <div className="dialog-title">New account</div>
        <div className="field"><label>Email</label>
          <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </div>
        <div className="field"><label>Password</label>
          <input className="input" type="password" value={password}
                 onChange={(e) => setPassword(e.target.value)} placeholder="min 12 characters" />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <div className="field" style={{ flex: 1 }}><label>Role</label>
            <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
              {(stats?.roles || []).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div className="field" style={{ flex: 1 }}><label>Tier</label>
            <select className="input" value={tier} onChange={(e) => setTier(e.target.value)}>
              {(stats?.tiers || []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        </div>
        <button className="btn btn-primary btn-block" disabled={busy || !email || password.length < 12} onClick={submit}>
          {busy ? "…" : "Create account"}
        </button>
        <button className="btn btn-ghost btn-block" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

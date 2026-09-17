"use client";

import * as React from "react";
import { api } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type { DiscordLookupResponse, HealthResponse } from "@/lib/types";
import { Search } from "@/components/icons";

type Mode = "auto" | "user" | "invite" | "guild";

export function DiscordApp({
  initialQuery, onConsumed, health,
}: {
  initialQuery?: string;
  onConsumed: () => void;
  health: HealthResponse | null;
}) {
  const [q, setQ] = React.useState(initialQuery ?? "");
  const [mode, setMode] = React.useState<Mode>("auto");
  const [data, setData] = React.useState<DiscordLookupResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);

  const doLookup = React.useCallback(async (value: string, m: Mode) => {
    const v = value.trim();
    if (!v) return;
    setLoading(true); setError(null); setPaywall(null);
    try {
      const isId = /^\d{17,20}$/.test(v);
      let res: DiscordLookupResponse;
      if (m === "invite" || (m === "auto" && !isId)) res = await api.discordInvite(v);
      else if (m === "guild") res = await api.discordGuildWidget(v);
      else if (m === "user") res = await api.discordUser(v);
      else res = health?.discord_enabled ? await api.discordUser(v) : await api.discordSnowflake(v);
      setData(res);
    } catch (e) {
      if (isQuotaError(e)) { setPaywall(e.message); setData(null); return; }
      setError(e instanceof Error ? e.message : "lookup failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [health]);

  React.useEffect(() => {
    if (initialQuery) { setQ(initialQuery); doLookup(initialQuery, "auto"); onConsumed(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  const sf = data?.snowflake;
  const user = data?.user;
  const inv = data?.invite;

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/discord$ lookup {q || "--help"}
        <span style={{ marginLeft: 10, color: health?.discord_enabled ? "#7fce9e" : "#e0b57f" }}>
          {health?.discord_enabled ? "token loaded" : "no token — snowflake + invites only"}
        </span>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); doLookup(q, mode); }}
        style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
          <Search size={15} style={{ color: "var(--color-neutral-500)" }} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="user/guild ID, or invite code…" autoFocus
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 13 }} />
        </div>
        <div className="seg">
          {(["auto", "user", "invite", "guild"] as Mode[]).map((m) => (
            <label key={m} className={`seg-opt ${mode === m ? "active" : ""}`}>
              <input type="radio" checked={mode === m} onChange={() => setMode(m)} style={{ display: "none" }} />{m}
            </label>
          ))}
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={loading}>
          {loading ? "…" : "Lookup"}
        </button>
      </form>

      {error && <div className="tag tag-bad">{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}
      {data?.note && <div style={{ fontSize: 12, color: "var(--color-neutral-500)", marginBottom: 12 }}>{data.note}</div>}

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-start" }}>
        {user && (
          <div className="card" style={{ minWidth: 300 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              {user.avatar_url
                ? <img src={user.avatar_url} alt="" width={48} height={48} style={{ borderRadius: 10 }} />
                : <div style={{ width: 48, height: 48, borderRadius: 10, background: "var(--color-accent-800)", display: "grid", placeItems: "center", color: "var(--color-accent-200)", fontFamily: "var(--mono)" }}>{(user.username || "?").slice(0, 2)}</div>}
              <div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{user.global_name || user.username}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--color-neutral-500)" }}>
                  @{user.username}{user.discriminator && user.discriminator !== "0" ? `#${user.discriminator}` : ""}{user.bot ? " · bot" : ""}
                </div>
              </div>
            </div>
            <Row k="ID" v={user.id} mono />
            <Row k="Created" v={user.created_at?.replace("T", " ").replace("Z", " UTC")} />
            {user.flags_decoded.length > 0 && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {user.flags_decoded.map((f) => <span key={f} className="tag tag-accent" style={{ fontSize: 10 }}>{f}</span>)}
              </div>
            )}
          </div>
        )}

        {inv && (
          <div className="card" style={{ minWidth: 300 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{inv.guild_name || "Guild"}</div>
            <Row k="Invite" v={inv.code} mono />
            <Row k="Channel" v={inv.channel_name ? `#${inv.channel_name}` : undefined} />
            <Row k="Members" v={inv.approximate_member_count?.toLocaleString()} />
            <Row k="Online" v={inv.approximate_presence_count?.toLocaleString()} />
            <Row k="Guild ID" v={inv.guild_id} mono />
            <Row k="Created" v={inv.created_at?.replace("T", " ").replace("Z", " UTC")} />
            {inv.inviter && <Row k="Inviter" v={`@${inv.inviter}`} />}
          </div>
        )}

        {sf && !user && !inv && (
          <div className="card" style={{ minWidth: 300 }}>
            <div className="card-kicker">Snowflake</div>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{sf.created_at.replace("T", " ").replace("Z", " UTC")}</div>
            <Row k="ID" v={sf.id} mono />
            <Row k="Worker" v={String(sf.worker_id)} mono />
            <Row k="Process" v={String(sf.process_id)} mono />
            <Row k="Increment" v={String(sf.increment)} mono />
          </div>
        )}

        {data?.widget && (
          <div className="card" style={{ minWidth: 300 }}>
            <div className="card-kicker">Guild widget</div>
            <Row k="Name" v={String((data.widget as any).name ?? "")} />
            <Row k="Online" v={String((data.widget as any).presence_count ?? "")} />
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-600)", marginTop: 6 }}>
              {Array.isArray((data.widget as any).channels) ? `${(data.widget as any).channels.length} public channel(s)` : ""}
            </div>
          </div>
        )}
      </div>

      {!data && !loading && !error && !paywall && (
        <div className="card" style={{ maxWidth: 520 }}>
          <div className="card-kicker">Discord OSINT</div>
          <div className="card-title">Decode IDs, resolve users, inspect invites</div>
          <p className="card-body">
            Paste a user/guild snowflake ID or an invite code. Snowflake creation
            time works with no token; live user lookups need a bot token
            (DISCORD_BOT_TOKEN); invites and guild widgets are public.
          </p>
        </div>
      )}
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v?: string | null; mono?: boolean }) {
  if (!v) return null;
  return (
    <div style={{ display: "flex", gap: 10, fontSize: 12.5, padding: "3px 0" }}>
      <span style={{ width: 74, color: "var(--color-neutral-500)" }}>{k}</span>
      <span style={{ color: "var(--color-text)", fontFamily: mono ? "var(--mono)" : "inherit", wordBreak: "break-all" }}>{v}</span>
    </div>
  );
}

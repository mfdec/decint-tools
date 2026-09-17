"use client";

import * as React from "react";
import { api } from "@/lib/api";
import type { AnalyticsConfig, AnalyticsSummary, TopRow, VisitRow } from "@/lib/types";

type Tab = "overview" | "log";

export function VisitorsApp() {
  const [tab, setTab] = React.useState<Tab>("overview");
  const [days, setDays] = React.useState(7);
  const [bots, setBots] = React.useState(false);
  const [sum, setSum] = React.useState<AnalyticsSummary | null>(null);
  const [rows, setRows] = React.useState<VisitRow[]>([]);
  const [cfg, setCfg] = React.useState<AnalyticsConfig | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      const [s, r, c] = await Promise.all([
        api.analyticsSummary(days, bots),
        api.analyticsRecent(300, bots),
        api.analyticsConfig().catch(() => null),
      ]);
      setSum(s); setRows(r.rows); if (c) setCfg(c);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to load");
    } finally {
      setBusy(false);
    }
  }, [days, bots]);

  React.useEffect(() => { load(); }, [load]);

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--color-neutral-600)" }}>
          ~/visitors$ report --days {days}
        </span>
        <div className="seg">
          {([["overview", "overview"], ["log", "log"]] as [Tab, string][]).map(([k, l]) => (
            <label key={k} className={`seg-opt ${tab === k ? "active" : ""}`}>
              <input type="radio" checked={tab === k} onChange={() => setTab(k)} style={{ display: "none" }} />{l}
            </label>
          ))}
        </div>
        <div className="seg">
          {[1, 7, 30, 90].map((d) => (
            <label key={d} className={`seg-opt ${days === d ? "active" : ""}`}>
              <input type="radio" checked={days === d} onChange={() => setDays(d)} style={{ display: "none" }} />{d}d
            </label>
          ))}
        </div>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--color-neutral-400)", cursor: "pointer" }}>
          <input type="checkbox" checked={bots} onChange={(e) => setBots(e.target.checked)} />
          include bots
        </label>
        <button className="btn btn-secondary" style={{ height: 30 }} onClick={load} disabled={busy}>
          {busy ? "…" : "Refresh"}
        </button>
      </div>

      {err && <div className="tag tag-bad">{err}</div>}

      {cfg && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
          <span className={`tag ${cfg.enabled ? "tag-ok" : "tag-bad"}`}>
            {cfg.enabled ? "collecting" : "disabled"}
          </span>
          <span className="tag tag-neutral">ip: {cfg.ip_mode}</span>
          <span className="tag tag-neutral">id: {cfg.id_mode}</span>
          <span className="tag tag-neutral">retention: {cfg.retention_days}d</span>
          {sum && !sum.geo_enabled && (
            <span className="tag tag-warn" title="Install GeoLite2-City.mmdb in backend/data/geoip to resolve country/city/ASN">
              geo db not installed
            </span>
          )}
        </div>
      )}

      {tab === "overview" && sum && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 20 }}>
            <Stat label="Unique visitors" value={sum.visitors} />
            <Stat label="Sessions" value={sum.sessions} />
            <Stat label="Events" value={sum.events} />
            <Stat label="Bots filtered" value={sum.bots_filtered} muted />
          </div>

          {sum.by_day.length > 0 && <Spark data={sum.by_day} />}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, marginTop: 20 }}>
            <Top title="Pages" rows={sum.top_pages} />
            <Top title="Countries" rows={sum.top_countries} />
            <Top title="Referrers" rows={sum.top_referrers} />
            <Top title="Browsers" rows={sum.top_browsers} />
            <Top title="Operating systems" rows={sum.top_os} />
            <Top title="Devices" rows={sum.top_devices} />
            <Top title="Networks (ASN org)" rows={sum.top_orgs} />
          </div>
        </>
      )}

      {tab === "log" && (
        <div style={{ border: "1px solid var(--color-divider)", borderRadius: 10, overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Time</th><th>Event</th><th>IP</th><th>Location</th><th>Network</th>
                <th>Device</th><th>Browser / OS</th><th>Screen</th><th>HW</th><th>Page</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ color: "var(--color-neutral-600)", whiteSpace: "nowrap" }}>
                    {r.ts.replace("T", " ").replace("+00:00", "")}
                  </td>
                  <td>
                    <span className={`tag ${r.event === "pageview" ? "tag-outline" : "tag-neutral"}`} style={{ fontSize: 10 }}>
                      {r.event}
                    </span>
                  </td>
                  <td style={{ color: "var(--color-neutral-200)" }}>{r.ip || "—"}</td>
                  <td style={{ color: "var(--color-neutral-400)" }}>
                    {[r.city, r.region, r.country].filter(Boolean).join(", ") || "—"}
                  </td>
                  <td style={{ color: "var(--color-neutral-500)", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.org || ""}>
                    {r.org || r.asn || "—"}
                  </td>
                  <td style={{ color: "var(--color-neutral-400)" }}>
                    {r.device_model || r.device_type || "—"}
                  </td>
                  <td style={{ color: "var(--color-neutral-400)" }}>
                    {[r.browser, r.browser_version].filter(Boolean).join(" ")}
                    {r.os ? ` · ${r.os}${r.os_version ? " " + r.os_version : ""}` : ""}
                  </td>
                  <td style={{ color: "var(--color-neutral-600)" }}>
                    {r.screen_w ? `${r.screen_w}×${r.screen_h}${r.pixel_ratio && r.pixel_ratio !== 1 ? `@${r.pixel_ratio}x` : ""}` : "—"}
                  </td>
                  <td style={{ color: "var(--color-neutral-600)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={[r.gpu, r.cpu_cores && `${r.cpu_cores} cores`, r.device_memory && `${r.device_memory}GB`, r.tz_client, r.connection].filter(Boolean).join(" · ")}>
                    {[r.cpu_cores && `${r.cpu_cores}c`, r.device_memory && `${r.device_memory}GB`].filter(Boolean).join(" ") || "—"}
                  </td>
                  <td style={{ color: "#8fb2e6", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.path || ""}>
                    {r.path || "—"}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={10} style={{ color: "var(--color-neutral-500)", fontFamily: "var(--font-body)" }}>
                  No visits recorded yet.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, muted }: { label: string; value: number; muted?: boolean }) {
  return (
    <div className="card" style={{ padding: "14px 16px", gap: 2 }}>
      <div style={{ fontSize: 26, fontWeight: 600, color: muted ? "var(--color-neutral-500)" : "var(--color-accent-300)" }}>
        {value.toLocaleString()}
      </div>
      <div style={{ fontSize: 12, color: "var(--color-neutral-500)" }}>{label}</div>
    </div>
  );
}

function Top({ title, rows }: { title: string; rows: TopRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <div className="card" style={{ padding: "14px 16px", gap: 8 }}>
      <div className="card-kicker">{title}</div>
      {rows.length === 0 && <div style={{ fontSize: 12, color: "var(--color-neutral-600)" }}>no data</div>}
      {rows.map((r) => (
        <div key={r.label} style={{ position: "relative", display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12.5, padding: "3px 6px", borderRadius: 4, overflow: "hidden" }}>
          <span style={{ position: "absolute", inset: 0, width: `${(r.n / max) * 100}%`, background: "color-mix(in srgb, var(--color-accent) 16%, transparent)" }} />
          <span style={{ position: "relative", color: "var(--color-neutral-200)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.label}>
            {r.label}
          </span>
          <span style={{ position: "relative", fontFamily: "var(--mono)", color: "var(--color-neutral-500)" }}>{r.n}</span>
        </div>
      ))}
    </div>
  );
}

function Spark({ data }: { data: { day: string; n: number; visitors: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.visitors));
  return (
    <div className="card" style={{ padding: "14px 16px", gap: 10 }}>
      <div className="card-kicker">Visitors per day</div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 90 }}>
        {data.map((d) => (
          <div key={d.day} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }} title={`${d.day} · ${d.visitors} visitors · ${d.n} events`}>
            <div style={{ width: "100%", height: `${(d.visitors / max) * 100}%`, minHeight: 2, background: "var(--color-accent)", borderRadius: "3px 3px 0 0", opacity: 0.85 }} />
            <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--color-neutral-600)" }}>{d.day.slice(5)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

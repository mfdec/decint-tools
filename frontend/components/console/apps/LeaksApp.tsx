"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type { LeakKind, LeakSearchResponse } from "@/lib/types";
import { Search, Copy } from "@/components/icons";

const KINDS: { key: LeakKind; label: string }[] = [
  { key: "auto", label: "auto" },
  { key: "email", label: "email" },
  { key: "username", label: "username" },
  { key: "domain", label: "domain" },
  { key: "name", label: "name" },
];

/** "jane doe" — what a hit says about the person, when the dataset knew it. */
function personName(h: { first_name?: string | null; last_name?: string | null }) {
  return [h.first_name, h.last_name].filter(Boolean).join(" ");
}

export function LeaksApp({
  initialQuery, initialKind, canReveal, onConsumed,
}: {
  initialQuery?: string;
  initialKind?: LeakKind;
  /** Paid plans and staff: results come back unmasked. Everyone else sees them masked. */
  canReveal: boolean;
  onConsumed: () => void;
}) {
  const [query, setQuery] = React.useState(initialQuery ?? "");
  const [kind, setKind] = React.useState<LeakKind>(initialKind ?? "auto");
  // On by default where the plan allows it: a paid account came for the
  // passwords, not for the dots. Unticking masks them again (screen-sharing).
  const [reveal, setReveal] = React.useState(canReveal);
  const [loading, setLoading] = React.useState(false);
  const [data, setData] = React.useState<LeakSearchResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);

  const doSearch = React.useCallback(async (q: string, k: LeakKind, rv: boolean) => {
    if (!q.trim()) return;
    setLoading(true); setError(null); setPaywall(null);
    try {
      setData(await api.searchLeaks(q.trim(), k, rv));
    } catch (e) {
      if (isQuotaError(e)) { setPaywall(e.message); setData(null); return; }
      setError(e instanceof Error ? e.message : "search failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (initialQuery) {
      const k = initialKind ?? "auto";
      setQuery(initialQuery);
      setKind(k);
      doSearch(initialQuery, k, canReveal && reveal);
      onConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/leaks$ query {query ? `--q ${query}` : "--help"}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); doSearch(query, kind, reveal); }}
        style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}
      >
        <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
          <Search size={15} style={{ color: "var(--color-neutral-500)" }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="email, username, domain, or a name…"
            autoFocus
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 13 }}
          />
        </div>
        <div className="seg">
          {KINDS.map((kd) => (
            <label key={kd.key} className={`seg-opt ${kind === kd.key ? "active" : ""}`}>
              <input type="radio" name="kind" checked={kind === kd.key} onChange={() => setKind(kd.key)} style={{ display: "none" }} />
              {kd.label}
            </label>
          ))}
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={loading}>
          {loading ? "Searching…" : "Run"}
        </button>
      </form>

      <label
        style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--color-neutral-400)", marginBottom: 16, cursor: canReveal ? "pointer" : "not-allowed" }}
        title={canReveal ? undefined : "Revealing leaked passwords needs a paid plan"}
      >
        <input
          type="checkbox"
          checked={canReveal && reveal}
          disabled={!canReveal}
          onChange={(e) => { setReveal(e.target.checked); if (data) doSearch(query, kind, e.target.checked); }}
        />
        reveal secrets
        {!canReveal && <Link href="/pricing" style={{ color: "var(--color-accent)", marginLeft: 4 }}>paid plans only</Link>}
      </label>

      {error && <div className="tag tag-bad" style={{ marginLeft: 12 }}>{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {data && (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "6px 0 14px" }}>
            {data.sources.map((s) => (
              <span key={s.key} className={`tag ${s.ok ? (s.count ? "tag-ok" : "tag-neutral") : "tag-bad"}`} title={s.status}>
                {s.label} · {s.count}
              </span>
            ))}
          </div>

          <div style={{ border: "1px solid var(--color-divider)", borderRadius: 10, overflow: "hidden" }}>
            <table className="table">
              <thead>
                <tr><th>Identifier</th><th>Source / breach</th><th>Seen</th><th>Secret</th></tr>
              </thead>
              <tbody>
                {data.hits.map((h, i) => (
                  <tr key={i}>
                    <td style={{ color: "#e4e7f5" }}>
                      {h.email || h.username || (h.line ? h.line.split(/[:;|]/)[0] : "") || personName(h) || "—"}
                      {personName(h) && (h.email || h.username || h.line) ? (
                        <span style={{ color: "#9397ab", fontSize: 10, marginLeft: 6, textTransform: "capitalize" }}>{personName(h)}</span>
                      ) : null}
                    </td>
                    <td style={{ color: "#b2b6ca" }}>
                      {h.breach || "—"}{" "}
                      <span style={{ color: "var(--color-neutral-600)", fontSize: 10 }}>{h.source_label}</span>
                    </td>
                    <td style={{ color: "#75798c" }}>{h.date || "—"}</td>
                    <td style={{ color: h.password ? "#e0b57f" : "#75798c" }}>
                      {h.password || (h.line ? h.line.split(/[:;|]/).slice(1).join(":") : null) || "••••••"}
                      {h.fields?.length ? <span style={{ color: "#9397ab", fontSize: 10, marginLeft: 6 }}>{h.fields.slice(0, 3).join(",")}</span> : null}
                    </td>
                  </tr>
                ))}
                {data.hits.length === 0 && (
                  <tr><td colSpan={4} style={{ color: "var(--color-neutral-500)", fontFamily: "var(--font-body)" }}>No results from the free sources for this query.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)" }}>
            <span>{data.total} hit(s) · kind: {data.kind} · {data.masked ? "masked" : "revealed"}</span>
            <span>{data.note}</span>
          </div>
        </>
      )}

      {!data && !loading && !error && !paywall && (
        <div className="card" style={{ maxWidth: 520, marginTop: 8 }}>
          <div className="card-kicker">Leak database</div>
          <div className="card-title">Search free public breach sources</div>
          <p className="card-body">
            Enter an email, username, domain, or a first and last name (names are
            matched in uploaded datasets). Results are aggregated from free public
            sources (XposedOrNot, ProxyNova COMB, LeakCheck, HIBP catalog), deduped and
            tagged by source. Secrets are revealed on paid plans and masked on the free trial.
          </p>
        </div>
      )}
    </div>
  );
}

"use client";

import * as React from "react";
import { api, pollDarkweb } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type { DarkwebJob, DarkwebMode, HealthResponse } from "@/lib/types";
import { Search } from "@/components/icons";

export function DarkwebApp({
  initialQuery, onConsumed, health,
}: {
  initialQuery?: string;
  onConsumed: () => void;
  health: HealthResponse | null;
}) {
  const [query, setQuery] = React.useState(initialQuery ?? "");
  const [mode, setMode] = React.useState<DarkwebMode>("ahmia");
  const [job, setJob] = React.useState<DarkwebJob | null>(null);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);

  const doSearch = React.useCallback(async (q: string, m: DarkwebMode) => {
    if (!q.trim()) return;
    setRunning(true); setError(null); setPaywall(null); setJob(null);
    try {
      const { job_id } = await api.startDarkweb(q.trim(), m, 25);
      const final = await pollDarkweb(job_id, (j) => setJob(j));
      setJob(final);
      if (final.status === "error") setError(final.error || "search failed");
    } catch (e) {
      if (isQuotaError(e)) { setPaywall(e.message); return; }
      setError(e instanceof Error ? e.message : "search failed");
    } finally {
      setRunning(false);
    }
  }, []);

  React.useEffect(() => {
    if (initialQuery) {
      setQuery(initialQuery);
      doSearch(initialQuery, "ahmia");
      onConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  const results = job?.results ?? [];
  const hash = (job?.manifest?.sha256 as string) || "";

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/darkweb$ search {query ? `"${query}" --mode ${mode}` : "--help"}
        {health && (
          <span style={{ marginLeft: 10, color: health.tor ? "#7fce9e" : "#e0b57f" }}>
            {health.tor ? "tor up" : "tor down"}
          </span>
        )}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); doSearch(query, mode); }}
        style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
          <Search size={15} style={{ color: "var(--color-neutral-500)" }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="keyword…" autoFocus
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 13 }} />
        </div>
        <div className="seg">
          <label className={`seg-opt ${mode === "ahmia" ? "active" : ""}`} title="ahmia.fi index, reranked — fast, no Tor">
            <input type="radio" checked={mode === "ahmia"} onChange={() => setMode("ahmia")} style={{ display: "none" }} />ahmia
          </label>
          <label className={`seg-opt ${mode === "tor" ? "active" : ""}`} title="multi-source over Tor — slower, live onions">
            <input type="radio" checked={mode === "tor"} onChange={() => setMode("tor")} style={{ display: "none" }} />tor
          </label>
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={running}>
          {running ? "Searching…" : "Search"}
        </button>
      </form>

      {running && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ height: 3, background: "var(--color-neutral-900)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.round((job?.progress ?? 0.1) * 100)}%`, background: "var(--color-accent)", transition: "width .3s" }} />
          </div>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)", marginTop: 6 }}>{job?.message || "querying…"}</div>
        </div>
      )}

      {error && <div className="tag tag-bad">{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {results.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {results.map((r, i) => (
            <div key={i} className="card" style={{ padding: "12px 14px", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--color-accent-300)", background: "color-mix(in srgb, var(--color-accent) 20%, transparent)", padding: "2px 6px", borderRadius: 4 }}>
                  {r.score.toFixed(1)}
                </span>
                <span style={{ fontSize: 14, fontWeight: 500 }}>{r.title}</span>
                {typeof r.coverage === "number" && (
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--color-neutral-500)" }}>cov {Math.round(r.coverage * 100)}%</span>
                )}
                {r.corroboration && r.corroboration > 1 && (
                  <span className="tag tag-outline" style={{ fontSize: 9 }}>×{r.corroboration}</span>
                )}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "#8fb2e6", wordBreak: "break-all" }}>{r.url}</div>
              {r.snippet && <div style={{ fontSize: 12, color: "var(--color-neutral-400)" }}>{r.snippet}</div>}
              {r.entities && Object.keys(r.entities).length > 0 && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {Object.entries(r.entities).flatMap(([t, vals]) =>
                    (Array.isArray(vals) ? vals : [vals]).slice(0, 3).map((v, k) => (
                      <span key={t + k} className="tag tag-neutral" style={{ fontSize: 10 }}>{t}: {String(v)}</span>
                    ))
                  )}
                </div>
              )}
            </div>
          ))}
          {hash && (
            <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)", marginTop: 4 }}>
              evidence sha256: {hash.slice(0, 32)}…
            </div>
          )}
        </div>
      )}

      {job && job.status === "done" && results.length === 0 && (
        <div className="card" style={{ maxWidth: 520 }}>
          <div className="card-title">No results</div>
          <p className="card-body">
            {mode === "tor"
              ? "No live .onion mirror returned matches — seed addresses rotate. Try ahmia mode."
              : "The ahmia index returned nothing for this keyword. Try broader terms."}
          </p>
        </div>
      )}

      {!job && !running && !error && !paywall && (
        <div className="card" style={{ maxWidth: 520 }}>
          <div className="card-kicker">Dark web</div>
          <div className="card-title">Search .onion indexes</div>
          <p className="card-body">
            <strong>ahmia</strong> ranks the ahmia.fi index over clearnet (fast). <strong>tor</strong> queries
            multiple onion search mirrors directly over Tor with corroboration + an evidence hash.
          </p>
        </div>
      )}
    </div>
  );
}

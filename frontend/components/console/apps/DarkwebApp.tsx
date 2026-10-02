"use client";

import * as React from "react";
import { api, pollDarkweb } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type {
  DarkwebEngine, DarkwebEngineStatus, DarkwebJob, DarkwebMode, DarkwebPruneReason,
  DarkwebPruned, DarkwebResult, DarkwebRosterEntry, HealthResponse,
} from "@/lib/types";
import { Search, Copy, Check } from "@/components/icons";

/** Switches the recon shell can pass in (`darkweb foo --mode tor --pages 2`). */
export interface DarkwebOpenOptions {
  mode?: DarkwebMode;
  pages?: number;
  experimental?: boolean;
}

const MODES: { key: DarkwebMode; label: string; blurb: string }[] = [
  { key: "gateway", label: "gateway", blurb: "fast mode · the engines with a clearnet gateway · seconds · no Tor" },
  { key: "tor", label: "tor", blurb: "full Tor mode · every onion engine, one isolated circuit each · about a minute" },
];

const STATUS_COLOR: Record<DarkwebEngineStatus, string> = {
  ok: "var(--color-ok)",
  empty: "var(--color-neutral-400)",
  timeout: "var(--color-warn)",
  blocked: "var(--color-warn)",
  error: "var(--color-bad)",
  benched: "var(--color-neutral-600)",
  skipped: "var(--color-neutral-600)",
};

const PRUNE_LABEL: Record<DarkwebPruneReason, string> = {
  sponsored: "Paid ads",
  spam: "Scam or spam signals",
  low_relevance: "Nothing in common with the search",
  near_duplicate: "Duplicate of a page already shown",
  non_onion: "Clearnet links",
  dead_v2: "Dead v2 addresses (offline since 2021)",
  invalid_address: "Invalid address — a look-alike or typo",
  self_link: "Links back to search engines",
  empty: "No usable title or text",
  excluded: "Contains a −term you excluded",
  phrase_missing: "Missing your “quoted phrase”",
};

type Tone = "ok" | "warn" | "bad" | "neutral";

/** How loudly a quality flag should read. Scam signals are the one a reader must not miss. */
function flagTone(flag: string): Tone {
  if (flag === "scam-signals" || flag === "risky") return "bad";
  if (["advertiser", "emoji-spam", "keyword-stuffing", "shouting", "warning", "no-title", "search-page"].includes(flag)) return "warn";
  if (flag === "verified") return "ok";
  return "neutral";
}

const TONE_CLASS: Record<Tone, string> = {
  ok: "tag tag-ok", warn: "tag tag-warn", bad: "tag tag-bad", neutral: "tag tag-neutral",
};

const HIDDEN_FLAGS = new Set(["has-mirrors"]); // shown as its own expandable instead

const mono = "var(--mono)";

// ───────────────────────────── small helpers ─────────────────────────────

function CopyButton({ text, label = "copy" }: { text: string; label?: string }) {
  const [done, setDone] = React.useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost"
      title={`Copy ${label}`}
      style={{ height: 22, padding: "0 6px", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch {
          /* clipboard blocked: nothing useful to do */
        }
      }}
    >
      {done ? <Check size={12} /> : <Copy size={12} />}
      {done ? "copied" : label}
    </button>
  );
}

function download(filename: string, mime: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * One CSV cell. Titles and snippets come from onion sites anyone can publish, and an analyst
 * will open this file in a spreadsheet: a cell starting with = + - @ would run as a formula
 * (CSV injection), so those get a leading apostrophe, which spreadsheets show but never evaluate.
 */
export function csvCell(value: unknown): string {
  let s = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return `"${s.replace(/"/g, '""')}"`;
}

function toCsv(results: DarkwebResult[]): string {
  const head = ["rank", "title", "url", "snippet", "score", "indexes", "engines", "quality", "flags", "mirrors"];
  const rows = results.map((r, i) => [
    i + 1, r.title, r.url, r.snippet, r.score, r.corroboration, r.engines.join(";"), r.quality,
    r.flags.join(";"), r.mirrors.map((m) => m.url).join(";"),
  ]);
  return [head, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "search";
}

function fmtMs(ms: number | null): string {
  if (ms == null) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// ───────────────────────────── pieces ─────────────────────────────

export function EngineChip({ e }: { e: DarkwebEngine }) {
  const detail = [e.status, e.error, e.endpoint && `via ${e.endpoint}`].filter(Boolean).join(" · ");
  return (
    <span
      title={detail}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 8px", borderRadius: 6,
        border: "1px solid var(--color-divider)", background: "var(--color-surface)",
        fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-300)",
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: 7, background: STATUS_COLOR[e.status] }} />
      {e.label || e.engine}
      {e.status === "ok" && <span style={{ color: "var(--color-ok)" }}>{e.results}</span>}
      {e.status !== "ok" && e.status !== "empty" && <span style={{ color: STATUS_COLOR[e.status] }}>{e.status}</span>}
      {e.latency_ms != null && e.status !== "benched" && (
        <span style={{ color: "var(--color-neutral-600)" }}>{fmtMs(e.latency_ms)}</span>
      )}
    </span>
  );
}

function RelatedList({ title, items }: { title: string; items: { url: string; title: string; engines: string[] }[] }) {
  if (!items.length) return null;
  return (
    <details style={{ fontSize: 11.5 }}>
      <summary style={{ cursor: "pointer", color: "var(--color-neutral-400)" }}>
        {title} ({items.length})
      </summary>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6, paddingLeft: 10, borderLeft: "2px solid var(--color-divider)" }}>
        {items.map((m) => (
          <div key={m.url}>
            <div style={{ color: "var(--color-neutral-300)" }}>{m.title}</div>
            <div style={{ fontFamily: mono, fontSize: 10.5, color: "#8fb2e6", wordBreak: "break-all" }}>
              {m.url} <CopyButton text={m.url} label="url" />
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

export function ResultCard({ r }: { r: DarkwebResult }) {
  const b = r.breakdown;
  const why =
    `relevance ${b.relevance.toFixed(2)} · rank fusion ${b.fusion.toFixed(2)} · ` +
    `consensus ${b.consensus.toFixed(2)} · quality ${b.quality.toFixed(2)}` +
    (b.freshness ? ` · freshness +${b.freshness}` : "");
  const flags = r.flags.filter((f) => !HIDDEN_FLAGS.has(f));
  const entityRows = Object.entries(r.entities ?? {}).flatMap(([kind, vals]) =>
    Array.isArray(vals) ? vals.slice(0, 3).map((v) => `${kind}: ${v}`) : vals ? [kind] : []
  );
  return (
    <div className="card" style={{ padding: "12px 14px", gap: 7 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span
          title={why}
          style={{
            fontFamily: mono, fontSize: 10, color: "var(--color-accent-300)", cursor: "help",
            background: "color-mix(in srgb, var(--color-accent) 20%, transparent)", padding: "2px 6px", borderRadius: 4,
          }}
        >
          {Math.round(r.score * 100)}
        </span>
        <span style={{ fontSize: 14, fontWeight: 500, wordBreak: "break-word" }}>{r.title}</span>
        {r.corroboration > 1 && (
          <span className="tag tag-outline" style={{ fontSize: 9 }} title={`${r.corroboration} independent search indexes returned this`}>
            ×{r.corroboration}
          </span>
        )}
        {r.badge && <span className={TONE_CLASS[flagTone(r.badge.toLowerCase())]} style={{ fontSize: 9 }}>{r.badge}</span>}
        {flags.filter((f) => f !== (r.badge ?? "").toLowerCase()).map((f) => (
          <span key={f} className={TONE_CLASS[flagTone(f)]} style={{ fontSize: 9 }}>{f}</span>
        ))}
      </div>

      {/* Plain text on purpose: never a link. These are untrusted addresses that only resolve in
          Tor Browser, and what to open is the reader's call. */}
      <div style={{ fontFamily: mono, fontSize: 11, color: "#8fb2e6", wordBreak: "break-all" }}>
        {r.url} <CopyButton text={r.url} label="url" />
      </div>
      {r.snippet && <div style={{ fontSize: 12, color: "var(--color-neutral-400)", wordBreak: "break-word" }}>{r.snippet}</div>}

      <div style={{ fontFamily: mono, fontSize: 10, color: "var(--color-neutral-600)" }}>
        {r.engines.slice(0, 5).join(", ")}{r.engines.length > 5 ? ` +${r.engines.length - 5}` : ""}
        {r.last_seen ? ` · last seen ${new Date(r.last_seen).toLocaleDateString()}` : ""}
      </div>

      {entityRows.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {entityRows.map((t, i) => (
            <span key={i} className="tag tag-neutral" style={{ fontSize: 10, wordBreak: "break-all" }}>{t}</span>
          ))}
        </div>
      )}
      <RelatedList title="Mirrors / possible clones" items={r.mirrors} />
      <RelatedList title="More from this site" items={r.more_from_site} />
    </div>
  );
}

export function PrunedPanel({ pruned, counts, withheld }: { pruned: DarkwebPruned[]; counts: Record<string, number>; withheld: number }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total && !withheld) return null;
  const byReason = new Map<string, DarkwebPruned[]>();
  for (const p of pruned) byReason.set(p.reason, [...(byReason.get(p.reason) ?? []), p]);
  return (
    <details style={{ fontSize: 12 }}>
      <summary style={{ cursor: "pointer", color: "var(--color-neutral-400)", fontFamily: mono, fontSize: 11 }}>
        {total} hit{total === 1 ? "" : "s"} dropped — see why
      </summary>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 10 }}>
        {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([reason, n]) => {
          const items = byReason.get(reason) ?? [];
          return (
            <div key={reason}>
              <div style={{ color: "var(--color-neutral-300)", marginBottom: 4 }}>
                {PRUNE_LABEL[reason as DarkwebPruneReason] ?? reason} <span style={{ color: "var(--color-neutral-600)" }}>· {n}</span>
              </div>
              {items.map((p) => (
                <div key={p.url + p.reason} style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-500)", wordBreak: "break-all", paddingLeft: 10 }}>
                  {p.title !== p.url ? `${p.title} · ` : ""}{p.url}{p.detail ? ` — ${p.detail}` : ""}
                </div>
              ))}
              {n > items.length && (
                <div style={{ fontSize: 10.5, color: "var(--color-neutral-600)", paddingLeft: 10 }}>…and {n - items.length} more</div>
              )}
            </div>
          );
        })}
        {withheld > 0 && (
          <div style={{ color: "var(--color-neutral-400)" }}>
            {withheld} result{withheld === 1 ? " was" : "s were"} withheld by the child-safety filter. These are counted, never listed.
          </div>
        )}
      </div>
    </details>
  );
}

// ───────────────────────────── the app ─────────────────────────────

export function DarkwebApp({
  initialQuery, initialOpts, onConsumed, health,
}: {
  initialQuery?: string;
  initialOpts?: DarkwebOpenOptions;
  onConsumed: () => void;
  health: HealthResponse | null;
}) {
  const [query, setQuery] = React.useState(initialQuery ?? "");
  const [mode, setMode] = React.useState<DarkwebMode>(initialOpts?.mode ?? "gateway");
  const [pages, setPages] = React.useState<number>(initialOpts?.pages ?? 1);
  const [experimental, setExperimental] = React.useState<boolean>(initialOpts?.experimental ?? false);
  const [job, setJob] = React.useState<DarkwebJob | null>(null);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);
  const [roster, setRoster] = React.useState<DarkwebRosterEntry[] | null>(null);
  const alive = React.useRef(true);

  React.useEffect(() => () => { alive.current = false; }, []);

  // What a search in this mode would query — shown before the first search, so the choice is informed.
  React.useEffect(() => {
    let stale = false;
    setRoster(null);
    api.darkwebEngines(mode).then((r) => { if (!stale) setRoster(r); }).catch(() => { /* cosmetic */ });
    return () => { stale = true; };
  }, [mode]);

  const doSearch = React.useCallback(async (q: string, m: DarkwebMode, p: number, exp: boolean) => {
    if (!q.trim()) return;
    setRunning(true); setError(null); setPaywall(null); setJob(null);
    try {
      const { job_id } = await api.startDarkweb(q.trim(), {
        mode: m, pages: p, experimental: m === "tor" && exp, limit: 25,
      });
      const final = await pollDarkweb(job_id, (j) => { if (alive.current) setJob(j); });
      if (!alive.current) return;
      setJob(final);
      if (final.status === "error") setError(final.error || "search failed");
    } catch (e) {
      if (!alive.current) return;
      if (isQuotaError(e)) { setPaywall(e.message); return; }
      setError(e instanceof Error ? String(e.message) : "search failed");
    } finally {
      if (alive.current) setRunning(false);
    }
  }, []);

  React.useEffect(() => {
    if (initialQuery) {
      const m = initialOpts?.mode ?? "gateway";
      const p = initialOpts?.pages ?? 1;
      const x = initialOpts?.experimental ?? false;
      setQuery(initialQuery); setMode(m); setPages(p); setExperimental(x);
      doSearch(initialQuery, m, p, x);
      onConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  const results = job?.results ?? [];
  const stats = job?.stats ?? null;
  const settled = job != null && (job.status === "done" || job.status === "error");
  const op = job?.operators ?? {};
  const hasOperators = !!(op.phrases?.length || op.excluded?.length || op.excluded_phrases?.length);
  const sha = job?.manifest?.sha256;
  const torDown = mode === "tor" && health != null && !health.tor;

  const exportJson = () => {
    if (!job) return;
    download(
      `decint-darkweb-${slug(job.query)}-${new Date().toISOString().slice(0, 10)}.json`,
      "application/json",
      JSON.stringify(
        { manifest: job.manifest, query: job.query, operators: job.operators, stats: job.stats, results: job.results },
        null, 2
      )
    );
  };
  const exportCsv = () => {
    if (!job) return;
    download(`decint-darkweb-${slug(job.query)}-${new Date().toISOString().slice(0, 10)}.csv`, "text/csv", toCsv(job.results));
  };

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: mono, fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/darkweb$ search {query ? `"${query}" --mode ${mode}` : "--help"}
        {health && (
          <span style={{ marginLeft: 10, color: health.tor ? "#7fce9e" : "#e0b57f" }}>
            {health.tor ? "tor up" : "tor down"}
          </span>
        )}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); doSearch(query, mode, pages, experimental); }}
        style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}
      >
        <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
          <Search size={15} style={{ color: "var(--color-neutral-500)" }} />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)} placeholder='keyword, "exact phrase", -exclude' autoFocus maxLength={300}
            aria-label="Search the dark web"
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: mono, fontSize: 13 }}
          />
        </div>
        <div className="seg" role="radiogroup" aria-label="Search mode">
          {MODES.map((m) => (
            <label key={m.key} className={`seg-opt ${mode === m.key ? "active" : ""}`} title={m.blurb}>
              <input type="radio" checked={mode === m.key} onChange={() => setMode(m.key)} style={{ display: "none" }} />
              {m.label}
            </label>
          ))}
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={running || !query.trim()}>
          {running ? "Searching…" : "Search"}
        </button>
      </form>

      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", marginBottom: 16, fontFamily: mono, fontSize: 11, color: "var(--color-neutral-500)" }}>
        <span>{MODES.find((m) => m.key === mode)?.blurb}</span>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }} title="Read more result pages from each engine: more results, slower">
          pages
          <select value={pages} onChange={(e) => setPages(Number(e.target.value))}
            style={{ background: "var(--color-surface)", color: "var(--color-text)", border: "1px solid var(--color-divider)", borderRadius: 6, fontFamily: mono, fontSize: 11, padding: "2px 4px" }}>
            {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        {mode === "tor" && (
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }} title="Unvetted engines — many are dead or slow, a few turn up things the main ones miss">
            <input type="checkbox" checked={experimental} onChange={(e) => setExperimental(e.target.checked)} />
            experimental engines
          </label>
        )}
        {torDown && <span className="tag tag-warn">Tor is down — tor mode will fail. Gateway mode still works.</span>}
      </div>

      {running && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ height: 3, background: "var(--color-neutral-900)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.round((job?.progress ?? 0.05) * 100)}%`, background: "var(--color-accent)", transition: "width .3s" }} />
          </div>
          <div style={{ fontFamily: mono, fontSize: 11, color: "var(--color-neutral-500)", marginTop: 6 }}>{job?.message || "starting…"}</div>
        </div>
      )}

      {error && <div className="tag tag-bad" style={{ marginBottom: 12, whiteSpace: "normal" }}>{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {job && job.engines.length > 0 && (
        <details open={running} style={{ marginBottom: 14 }}>
          <summary style={{ cursor: "pointer", fontFamily: mono, fontSize: 11, color: "var(--color-neutral-400)" }}>
            engines · {job.engines.length}{job.engines_planned ? `/${job.engines_planned}` : ""} answered
            {stats ? ` · ${stats.engines_with_results} with results` : ""}
            {job.transport ? ` · via ${job.transport}` : ""}
          </summary>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
            {job.engines.map((e) => <EngineChip key={e.engine} e={e} />)}
          </div>
        </details>
      )}

      {settled && stats && !job.error && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12, fontFamily: mono, fontSize: 11, color: "var(--color-neutral-500)" }}>
          <span>{stats.shown} result{stats.shown === 1 ? "" : "s"}</span>
          <span>· {stats.raw_results} raw hits → {stats.unique_urls} unique</span>
          {stats.mirror_clusters > 0 && <span>· {stats.mirror_clusters} mirror cluster{stats.mirror_clusters === 1 ? "" : "s"}</span>}
          {typeof job.manifest.elapsed_seconds === "number" && <span>· {job.manifest.elapsed_seconds}s</span>}
          {hasOperators && (
            <>
              {op.phrases?.map((p) => <span key={"p" + p} className="tag tag-outline" style={{ fontSize: 9 }}>“{p}”</span>)}
              {op.excluded?.map((x) => <span key={"x" + x} className="tag tag-neutral" style={{ fontSize: 9 }}>−{x}</span>)}
              {op.excluded_phrases?.map((x) => <span key={"xp" + x} className="tag tag-neutral" style={{ fontSize: 9 }}>−“{x}”</span>)}
            </>
          )}
        </div>
      )}

      {results.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {results.map((r) => <ResultCard key={r.url} r={r} />)}

          <div style={{ fontSize: 11, color: "var(--color-neutral-600)" }}>
            Addresses are shown as text. Open them in Tor Browser — many onion services are scams, illegal, or both.
          </div>

          {settled && job.status === "done" && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" className="btn btn-secondary" style={{ height: 30, fontSize: 12 }} onClick={exportJson}>Export JSON</button>
              <button type="button" className="btn btn-secondary" style={{ height: 30, fontSize: 12 }} onClick={exportCsv}>Export CSV</button>
              {sha && (
                <span style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-600)", wordBreak: "break-all" }}
                  title="SHA-256 of the result list as [{url, title, score}, …] in order, JSON with sorted keys and no spaces. Recompute it from an export to show a report was not altered.">
                  evidence sha256: {sha.slice(0, 32)}… <CopyButton text={sha} label="hash" />
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {settled && stats && (
        <div style={{ marginTop: 14 }}>
          <PrunedPanel pruned={job.pruned} counts={stats.pruned_by_reason} withheld={stats.safety_blocked} />
        </div>
      )}

      {job && job.status === "done" && results.length === 0 && (
        <div className="card" style={{ maxWidth: 560, marginTop: 12 }}>
          <div className="card-title">No results</div>
          <p className="card-body">
            {hasOperators
              ? "Your “phrases” and −exclusions removed everything the engines returned. Loosen them and try again — the list above shows what was dropped and why."
              : mode === "gateway"
                ? "The gateway engines found nothing for this. Try broader words, or switch to tor mode to ask the full set of onion engines."
                : "None of the onion engines had a match. Try broader words, or add the experimental engines."}
          </p>
        </div>
      )}

      {!job && !running && !error && !paywall && (
        <div className="card" style={{ maxWidth: 600 }}>
          <div className="card-kicker">Dark web</div>
          <div className="card-title">Search .onion indexes</div>
          <p className="card-body">
            One search goes to many onion search engines at once. The hits are cleaned, merged across engines,
            scored, and the junk — paid ads, scam listings, dead addresses, phishing look-alikes — is dropped,
            with the reason kept so you can see what was removed.
          </p>
          <p className="card-body">
            <strong>gateway</strong> asks the engines that publish a clearnet gateway: fast, no Tor.{" "}
            <strong>tor</strong> asks every onion engine over its own isolated Tor circuit, and adds extracted
            entities and an evidence SHA-256.
          </p>
          <p className="card-body" style={{ fontFamily: mono, fontSize: 11.5 }}>
            {'"exact phrase" · -excluded · a -"phrase" works too'}
          </p>
          {roster && roster.length > 0 && (
            <p className="card-body" style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-500)" }}>
              {mode} mode queries {roster.length}: {roster.map((r) => r.label || r.name).join(", ")}
            </p>
          )}
          <p className="card-body" style={{ fontSize: 11.5 }}>
            Only search-engine pages are read; the sites in the results are never visited. Searches for child
            sexual abuse material are refused, and matching results are withheld.
          </p>
        </div>
      )}
    </div>
  );
}

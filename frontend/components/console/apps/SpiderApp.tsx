"use client";

import * as React from "react";
import { api, pollSpider } from "@/lib/api";
import type { Dispatch } from "../Console";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type {
  LeakKind,
  SpiderGraph,
  SpiderJob,
  SpiderNode,
  SpiderNodeType,
  SpiderScanSummary,
} from "@/lib/types";
import { Search, Copy, Spider as SpiderIcon } from "@/components/icons";

const mono = "var(--mono)";

const KINDS: { key: LeakKind; label: string }[] = [
  { key: "auto", label: "auto" },
  { key: "email", label: "email" },
  { key: "username", label: "username" },
  { key: "domain", label: "domain" },
  { key: "name", label: "name" },
];

// The modules the user can switch off for a scan. Keys match the backend
// registry; a module switched off server-side simply never runs even if ticked.
const MODULES: { key: string; label: string }[] = [
  { key: "leaks", label: "Leak sources" },
  { key: "gravatar", label: "Gravatar" },
  { key: "username_sites", label: "Username sites" },
  { key: "domain", label: "Domain intel" },
  { key: "darkweb_mentions", label: "Dark-web" },
];

// Per-type colour + heading. The seed kinds (email/username/domain/name) are
// the ones you can keep pivoting from, so they get the warmer accents.
const NODE_META: Record<SpiderNodeType, { label: string; color: string }> = {
  email: { label: "Emails", color: "#8ab4ff" },
  username: { label: "Usernames", color: "#b69cff" },
  domain: { label: "Domains", color: "#7fce9e" },
  name: { label: "Names", color: "#e0b57f" },
  account: { label: "Accounts", color: "#6fd3d0" },
  breach: { label: "Breaches", color: "#e8908f" },
  password: { label: "Passwords", color: "#e8739a" },
  hash: { label: "Hashes", color: "#c77ab0" },
  onion: { label: "Onion mentions", color: "#9a8cff" },
  wallet: { label: "Wallets", color: "#d6b24a" },
};

const TYPE_ORDER: SpiderNodeType[] = [
  "email", "username", "domain", "name", "account", "breach", "password", "hash", "onion", "wallet",
];

const PIVOTABLE = new Set<SpiderNodeType>(["email", "username", "domain", "name"]);

const keyOf = (type: string, value: string) => `${type}:${value}`;

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Build, from the edge list, a map of node-key → the node-keys it links to. */
function adjacency(graph: SpiderGraph): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, new Set());
    adj.get(a)!.add(b);
  };
  for (const e of graph.edges) {
    const s = keyOf(e.src[0], e.src[1]);
    const d = keyOf(e.dst[0], e.dst[1]);
    link(s, d);
    link(d, s);
  }
  return adj;
}

export function SpiderApp({
  initialQuery, initialKind, onConsumed, dispatch,
}: {
  initialQuery?: string;
  initialKind?: LeakKind;
  onConsumed: () => void;
  dispatch: Dispatch;
}) {
  const [seed, setSeed] = React.useState(initialQuery ?? "");
  const [kind, setKind] = React.useState<LeakKind>(initialKind ?? "auto");
  const [mods, setMods] = React.useState<Set<string>>(new Set(MODULES.map((m) => m.key)));
  const [running, setRunning] = React.useState(false);
  const [job, setJob] = React.useState<SpiderJob | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [paywall, setPaywall] = React.useState<string | null>(null);
  const [focus, setFocus] = React.useState<string | null>(null);

  // History, and the saved scan currently being viewed (null = live job).
  const [history, setHistory] = React.useState<SpiderScanSummary[] | null>(null);
  const [showHistory, setShowHistory] = React.useState(false);
  const [viewing, setViewing] = React.useState<{ summary: SpiderScanSummary; graph: SpiderGraph } | null>(null);

  const alive = React.useRef(true);
  React.useEffect(() => () => { alive.current = false; }, []);

  const loadHistory = React.useCallback(() => {
    api.spiderHistory().then((h) => { if (alive.current) setHistory(h); }).catch(() => { /* cosmetic */ });
  }, []);
  React.useEffect(() => { loadHistory(); }, [loadHistory]);

  const doScan = React.useCallback(async (q: string, k: LeakKind, chosen: Set<string>) => {
    if (!q.trim()) return;
    setRunning(true); setError(null); setPaywall(null); setJob(null); setViewing(null); setFocus(null);
    try {
      const picked = MODULES.filter((m) => chosen.has(m.key)).map((m) => m.key);
      const { job_id } = await api.startSpider(q.trim(), {
        kind: k === "auto" ? undefined : (k as Exclude<LeakKind, never>),
        modules: picked.length && picked.length < MODULES.length ? picked : undefined,
      });
      const final = await pollSpider(job_id, (j) => { if (alive.current) setJob(j); });
      if (!alive.current) return;
      setJob(final);
      if (final.status === "error") setError(final.error || "scan failed");
      else loadHistory();
    } catch (e) {
      if (!alive.current) return;
      if (isQuotaError(e)) { setPaywall(e.message); return; }
      setError(e instanceof Error ? e.message : "scan failed");
    } finally {
      if (alive.current) setRunning(false);
    }
  }, [loadHistory]);

  React.useEffect(() => {
    if (initialQuery) {
      const k = initialKind ?? "auto";
      setSeed(initialQuery); setKind(k);
      doScan(initialQuery, k, mods);
      onConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  const openSaved = React.useCallback(async (id: number) => {
    try {
      const d = await api.spiderScan(id);
      if (!alive.current) return;
      setViewing({ summary: d, graph: d.graph }); setJob(null); setFocus(null);
      setShowHistory(false);
    } catch {
      /* gone — refresh the list */ loadHistory();
    }
  }, [loadHistory]);

  const removeSaved = React.useCallback(async (id: number) => {
    try { await api.deleteSpiderScan(id); } catch { /* already gone */ }
    if (!alive.current) return;
    setHistory((h) => (h ? h.filter((s) => s.id !== id) : h));
    setViewing((v) => (v && v.summary.id === id ? null : v));
  }, []);

  const graph: SpiderGraph | null = viewing?.graph ?? job?.graph ?? null;
  const stats = job?.stats ?? null;
  const settled = job != null && (job.status === "done" || job.status === "error");
  const adj = React.useMemo(() => (graph ? adjacency(graph) : new Map<string, Set<string>>()), [graph]);

  const nodes = React.useMemo(() => graph?.nodes ?? [], [graph]);
  const correlated = nodes.filter((n) => n.seen_before.length > 0);
  const byType = React.useMemo(() => {
    const m = new Map<SpiderNodeType, SpiderNode[]>();
    for (const n of nodes) {
      if (!m.has(n.type)) m.set(n.type, []);
      m.get(n.type)!.push(n);
    }
    return m;
  }, [nodes]);

  const seedNode = nodes.find((n) => n.depth === 0) || null;
  const focusNeighbors = focus ? adj.get(focus) ?? new Set<string>() : null;

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: mono, fontSize: 11.5, color: "#75798c", marginBottom: 16, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span>~/spider$ scan {seed ? `${seed} --kind ${kind}` : "--help"}</span>
        <button
          onClick={() => { setShowHistory((v) => !v); loadHistory(); }}
          className="btn btn-ghost"
          style={{ marginLeft: "auto", height: 26, padding: "0 10px", fontSize: 11 }}
        >
          history{history ? ` · ${history.length}` : ""}
        </button>
      </div>

      {/* Scan form */}
      <form
        onSubmit={(e) => { e.preventDefault(); doScan(seed, kind, mods); }}
        style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}
      >
        <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
          <Search size={15} style={{ color: "var(--color-neutral-500)" }} />
          <input
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="email, username, domain, or a name to pivot from…"
            autoFocus
            aria-label="Seed identifier"
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: mono, fontSize: 13 }}
          />
        </div>
        <div className="seg" role="radiogroup" aria-label="Seed kind">
          {KINDS.map((kd) => (
            <label key={kd.key} className={`seg-opt ${kind === kd.key ? "active" : ""}`}>
              <input type="radio" name="spider-kind" checked={kind === kd.key} onChange={() => setKind(kd.key)} style={{ display: "none" }} />
              {kd.label}
            </label>
          ))}
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={running || !seed.trim()}>
          {running ? "Scanning…" : "Scan"}
        </button>
      </form>

      {/* Module toggles */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16, fontFamily: mono, fontSize: 11 }}>
        <span style={{ color: "var(--color-neutral-600)", alignSelf: "center" }}>modules:</span>
        {MODULES.map((m) => {
          const on = mods.has(m.key);
          return (
            <label
              key={m.key}
              className={`tag ${on ? "tag-ok" : "tag-neutral"}`}
              style={{ cursor: "pointer", userSelect: "none" }}
              title={on ? "click to exclude from the next scan" : "click to include"}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={(e) => setMods((prev) => {
                  const next = new Set(prev);
                  if (e.target.checked) next.add(m.key); else next.delete(m.key);
                  return next;
                })}
                style={{ display: "none" }}
              />
              {on ? "✓ " : "  "}{m.label}
            </label>
          );
        })}
      </div>

      {running && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ height: 3, background: "var(--color-neutral-900)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.round((job?.progress ?? 0.05) * 100)}%`, background: "var(--color-accent)", transition: "width .3s" }} />
          </div>
          <div style={{ fontFamily: mono, fontSize: 11, color: "var(--color-neutral-500)", marginTop: 6 }}>
            {job?.message || "starting…"}
          </div>
          {job && job.modules.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {job.modules.map((m) => (
                <span key={m.key} className="tag tag-outline" style={{ fontSize: 9 }}>{m.name}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {error && <div className="tag tag-bad" style={{ marginBottom: 12, whiteSpace: "normal" }}>{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {/* History drawer */}
      {showHistory && (
        <HistoryPanel
          history={history}
          onOpen={openSaved}
          onDelete={removeSaved}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* Viewing a saved scan */}
      {viewing && (
        <div className="card" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <SpiderIcon size={18} style={{ color: "var(--color-accent)" }} />
          <span style={{ fontSize: 13 }}>
            Saved scan · <strong>{viewing.summary.seed}</strong> · {fmtDate(viewing.summary.created_at)}
          </span>
          <button className="btn btn-ghost" style={{ marginLeft: "auto", height: 28, padding: "0 10px", fontSize: 12 }}
            onClick={() => { setViewing(null); }}>
            back to live
          </button>
          <button className="btn btn-ghost" style={{ height: 28, padding: "0 10px", fontSize: 12, color: "var(--color-neutral-400)" }}
            onClick={() => removeSaved(viewing.summary.id)}>
            delete
          </button>
        </div>
      )}

      {/* The "featured in a previous search" callout — the whole point of the tool. */}
      {graph && correlated.length > 0 && (
        <div
          className="card"
          style={{
            marginBottom: 16, borderColor: "var(--color-accent)",
            background: "linear-gradient(180deg, color-mix(in srgb, var(--color-accent) 10%, var(--color-surface)), var(--color-surface))",
          }}
        >
          <div className="card-kicker">Seen before</div>
          <div style={{ fontSize: 13, color: "var(--color-neutral-300)", marginBottom: 8 }}>
            {correlated.length} identifier{correlated.length === 1 ? "" : "s"} here also turned up in earlier scans of yours:
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {correlated.map((n) => (
              <div key={keyOf(n.type, n.value)} style={{ fontFamily: mono, fontSize: 12, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
                <span style={{ color: NODE_META[n.type]?.color }}>{n.label}</span>
                <span style={{ color: "var(--color-neutral-600)" }}>·</span>
                {n.seen_before.map((s) => (
                  <button
                    key={s.scan_id}
                    onClick={() => openSaved(s.scan_id)}
                    className="tag tag-outline"
                    style={{ fontSize: 10, cursor: "pointer" }}
                    title={`open the scan of "${s.seed}" from ${fmtDate(s.at)}`}
                  >
                    {s.seed} · {fmtDate(s.at)}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stats line */}
      {graph && (settled || viewing) && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 14, fontFamily: mono, fontSize: 11, color: "var(--color-neutral-500)" }}>
          <span>{nodes.length} node{nodes.length === 1 ? "" : "s"}</span>
          <span>· {graph.edges.length} link{graph.edges.length === 1 ? "" : "s"}</span>
          {stats && <span>· {stats.lookups} lookup{stats.lookups === 1 ? "" : "s"}</span>}
          {stats?.truncated && <span className="tag tag-warn" style={{ fontSize: 9 }}>capped — raise the limit for a wider scan</span>}
          {focus && <button className="btn btn-ghost" style={{ height: 22, padding: "0 8px", fontSize: 10 }} onClick={() => setFocus(null)}>clear selection</button>}
        </div>
      )}

      {/* Seed node */}
      {seedNode && (
        <div style={{ marginBottom: 14 }}>
          <NodeCard
            node={seedNode} isSeed adj={adj} focus={focus} focusNeighbors={focusNeighbors}
            onFocus={setFocus} onPivot={(n) => { setSeed(n.value); setKind(n.type as LeakKind); doScan(n.value, n.type as LeakKind, mods); }}
          />
        </div>
      )}

      {/* Nodes grouped by type */}
      {graph && nodes.length > (seedNode ? 1 : 0) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 14 }}>
          {TYPE_ORDER.filter((t) => byType.has(t)).map((t) => {
            const group = byType.get(t)!.filter((n) => n.depth > 0);
            if (group.length === 0) return null;
            const meta = NODE_META[t];
            return (
              <section key={t} style={{ border: "1px solid var(--color-divider)", borderRadius: 10, overflow: "hidden" }}>
                <header style={{ padding: "8px 12px", borderBottom: "1px solid var(--color-divider)", fontFamily: mono, fontSize: 11, color: meta.color, display: "flex", justifyContent: "space-between" }}>
                  <span>{meta.label}</span>
                  <span style={{ color: "var(--color-neutral-600)" }}>{group.length}</span>
                </header>
                <div style={{ display: "flex", flexDirection: "column" }}>
                  {group.map((n) => (
                    <NodeCard
                      key={keyOf(n.type, n.value)} node={n} adj={adj} focus={focus} focusNeighbors={focusNeighbors}
                      onFocus={setFocus} onPivot={(nn) => { setSeed(nn.value); setKind(nn.type as LeakKind); doScan(nn.value, nn.type as LeakKind, mods); }}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {graph && settled && nodes.length <= 1 && !error && (
        <div style={{ fontFamily: mono, fontSize: 12, color: "var(--color-neutral-500)", marginTop: 8 }}>
          Nothing linked out from that seed. Try a different identifier, or switch more modules on.
        </div>
      )}

      {!graph && !running && !paywall && !error && (
        <div style={{ fontFamily: mono, fontSize: 12.5, color: "var(--color-neutral-500)", lineHeight: 1.7, marginTop: 8, maxWidth: 620 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--color-neutral-400)", marginBottom: 10 }}>
            <SpiderIcon size={18} style={{ color: "var(--color-accent)" }} /> Pivot from one identifier.
          </div>
          Enter an email, username, domain or name. The spider expands it through
          the leak sources and the modules above, then expands what those turn up —
          linking everything into a graph. Click a node to highlight its links, or
          pivot from it into a fresh scan. Anything that also appeared in one of your
          earlier scans is flagged at the top.
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── node card ───────────────────────────

function NodeCard({
  node, adj, focus, focusNeighbors, onFocus, onPivot, isSeed,
}: {
  node: SpiderNode;
  adj: Map<string, Set<string>>;
  focus: string | null;
  focusNeighbors: Set<string> | null;
  onFocus: (key: string | null) => void;
  onPivot: (n: SpiderNode) => void;
  isSeed?: boolean;
}) {
  const k = keyOf(node.type, node.value);
  const meta = NODE_META[node.type];
  const selected = focus === k;
  const linked = focusNeighbors?.has(k) ?? false;
  const dim = focus != null && !selected && !linked;
  const degree = adj.get(k)?.size ?? 0;
  const seen = node.seen_before.length > 0;

  return (
    <div
      onClick={() => onFocus(selected ? null : k)}
      style={{
        padding: "10px 12px",
        borderBottom: isSeed ? "1px solid var(--color-divider)" : undefined,
        border: isSeed ? "1px solid var(--color-accent)" : undefined,
        borderRadius: isSeed ? 10 : undefined,
        background: isSeed
          ? "color-mix(in srgb, var(--color-accent) 7%, var(--color-surface))"
          : selected
          ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
          : "transparent",
        borderLeft: !isSeed ? `2px solid ${selected || linked ? meta.color : "transparent"}` : undefined,
        opacity: dim ? 0.4 : 1,
        cursor: "pointer",
        transition: "opacity .15s, background .15s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {isSeed && <span className="tag tag-outline" style={{ fontSize: 9 }}>seed</span>}
        <span style={{ fontFamily: mono, fontSize: 13, color: isSeed ? "#e4e7f5" : meta.color, wordBreak: "break-all" }}>
          {node.url ? (
            <a href={node.url} target="_blank" rel="noreferrer noopener" onClick={(e) => e.stopPropagation()} style={{ color: "inherit" }}>
              {node.label}
            </a>
          ) : node.label}
        </span>
        <button
          onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(node.value).catch(() => {}); }}
          className="btn btn-ghost" style={{ height: 20, width: 20, padding: 0, marginLeft: "auto" }} title="copy value"
        >
          <Copy size={12} />
        </button>
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
        {seen && (
          <span className="tag" style={{ fontSize: 9, background: "color-mix(in srgb, var(--color-accent) 25%, transparent)", color: "var(--color-accent-200, #c9b8ff)" }}
            title={node.seen_before.map((s) => `${s.seed} · ${fmtDate(s.at)}`).join("\n")}>
            ↩ seen in {node.seen_before.length} earlier scan{node.seen_before.length === 1 ? "" : "s"}
          </span>
        )}
        {node.sources.map((s) => (
          <span key={s} className="tag tag-neutral" style={{ fontSize: 9 }}>{s}</span>
        ))}
        {degree > 0 && <span style={{ fontFamily: mono, fontSize: 9, color: "var(--color-neutral-600)" }}>{degree} link{degree === 1 ? "" : "s"}</span>}
        {PIVOTABLE.has(node.type) && !isSeed && (
          <button
            onClick={(e) => { e.stopPropagation(); onPivot(node); }}
            className="btn btn-ghost" style={{ height: 20, padding: "0 8px", fontSize: 9, color: meta.color }}
            title="start a new scan from this node"
          >
            ↻ pivot
          </button>
        )}
      </div>

      {node.detail && (
        <div style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-600)", marginTop: 6, wordBreak: "break-word" }}>
          {node.detail}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── history panel ───────────────────────────

function HistoryPanel({
  history, onOpen, onDelete, onClose,
}: {
  history: SpiderScanSummary[] | null;
  onOpen: (id: number) => void;
  onDelete: (id: number) => void;
  onClose: () => void;
}) {
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
        <div className="card-kicker" style={{ margin: 0 }}>Your scan history</div>
        <button className="btn btn-ghost" style={{ marginLeft: "auto", height: 24, padding: "0 8px", fontSize: 11 }} onClick={onClose}>close</button>
      </div>
      {history == null ? (
        <div style={{ fontFamily: mono, fontSize: 11.5, color: "var(--color-neutral-500)" }}>loading…</div>
      ) : history.length === 0 ? (
        <div style={{ fontFamily: mono, fontSize: 11.5, color: "var(--color-neutral-500)" }}>
          No saved scans yet. Each scan you run is kept here until you delete it.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {history.map((s) => (
            <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0", borderBottom: "1px solid var(--color-divider)" }}>
              <button onClick={() => onOpen(s.id)} className="btn btn-ghost" style={{ flex: 1, justifyContent: "flex-start", height: "auto", padding: "2px 0", textAlign: "left" }}>
                <span style={{ fontFamily: mono, fontSize: 12.5, color: "var(--color-accent-300)", wordBreak: "break-all" }}>{s.seed}</span>
              </button>
              <span style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-600)" }}>
                {s.node_count} node{s.node_count === 1 ? "" : "s"}
              </span>
              <span style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-600)" }}>{fmtDate(s.created_at)}</span>
              <button onClick={() => onDelete(s.id)} className="btn btn-ghost" style={{ height: 24, padding: "0 8px", fontSize: 11, color: "var(--color-neutral-400)" }} title="delete this scan">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

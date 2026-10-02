"use client";

/**
 * The admin "Data" tab: every dataset the app holds, one sub-tab each, with the
 * audit log folded in, plus the leak datasets the admin has added to the search
 * engine.
 *
 * Datasets are described by the server (GET /admin/data) and all render through
 * the same view — KPIs, charts, tables — so a new dataset appears here by being
 * added to services/datahub.py, with no change to this file. The leak datasets
 * are the exception: they are files to manage rather than numbers to read, so
 * they have their own component.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, dataExportUrl } from "@/lib/api";
import type { DataCatalogItem, DataDataset, DataQuery } from "@/lib/types";
import { ChartCard } from "./DataCharts";
import { DataTableView, KpiTile } from "./DataTables";
import { LeakDatasets } from "./LeakDatasets";
import { btn, btnSmall, card, ink2, input, muted, when } from "./ui";

type Flash = (kind: "ok" | "err", text: string) => void;

const LEAKS = "leaks";
const GROUP_ORDER = ["Start", "Product", "Money", "Trust", "System", "Search engine"];
const DAY_CHOICES = [7, 14, 30, 90, 180, 365];
const ROW_CHOICES = [100, 200, 500, 1000];

export function DataHub({ flash }: { flash: Flash }) {
  const [catalog, setCatalog] = useState<DataCatalogItem[]>([]);
  const [active, setActive] = useState("overview");
  const [params, setParams] = useState<DataQuery>({ days: 30, q: "", bots: false, limit: 200 });
  const [qDraft, setQDraft] = useState("");
  const [data, setData] = useState<DataDataset | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [leakCount, setLeakCount] = useState<number | null>(null);

  // Deep link: /admin#data/audit lands on the audit log.
  useEffect(() => {
    const m = window.location.hash.match(/^#data\/([\w-]+)/);
    if (m) setActive(m[1]);
    api.adminDataCatalog().then((r) => setCatalog(r.datasets)).catch((e) =>
      setError(e instanceof ApiError ? e.message : "Couldn't load the dataset list."));
    api.adminLeakDatasets().then((r) => setLeakCount(r.datasets.length)).catch(() => {});
  }, []);

  const pick = (id: string) => {
    setActive(id);
    setQDraft(""); setParams((p) => ({ ...p, q: "" }));
    window.history.replaceState(null, "", `#data/${id}`);
  };

  const item = catalog.find((d) => d.id === active);
  const filters = item?.filters ?? [];

  const load = useCallback(() => {
    if (active === LEAKS) return;
    setLoading(true); setError("");
    api.adminDataset(active, params)
      .then((d) => setData(d))
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load that dataset."))
      .finally(() => setLoading(false));
  }, [active, params]);

  useEffect(load, [load]);

  const groups = useMemo(() => {
    const items: { id: string; title: string; group: string }[] = [
      ...catalog,
      { id: LEAKS, title: "Leak datasets", group: "Search engine" },
    ];
    return GROUP_ORDER
      .map((g) => ({ name: g, items: items.filter((d) => d.group === g) }))
      .filter((g) => g.items.length > 0);
  }, [catalog]);

  // Hold the previous render while a refetch is in flight: no skeleton, no jump.
  const shown = data && data.id === active ? data : null;
  const stale = loading && shown !== null;

  return (
    <div>
      <nav aria-label="Datasets" style={{
        display: "flex", gap: 22, overflowX: "auto", paddingBottom: 12, marginBottom: 18,
        borderBottom: "1px solid var(--color-divider)",
      }}>
        {groups.map((g) => (
          <div key={g.name} style={{ flexShrink: 0 }}>
            <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: muted, margin: "0 0 5px 2px" }}>
              {g.name}
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {g.items.map((d) => (
                <button key={d.id} onClick={() => pick(d.id)} aria-current={active === d.id ? "page" : undefined}
                  style={{
                    ...btn, padding: "5px 11px", whiteSpace: "nowrap",
                    borderColor: active === d.id ? "var(--color-accent)" : "transparent",
                    opacity: active === d.id ? 1 : 0.65,
                  }}>
                  {d.title}
                  {d.id === LEAKS && leakCount ? (
                    <span style={{ marginLeft: 7, fontSize: 11, fontFamily: "var(--mono)", color: muted }}>{leakCount}</span>
                  ) : null}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {active === LEAKS ? (
        <LeakDatasets flash={flash} onCount={setLeakCount} />
      ) : (
        <>
          <Toolbar
            filters={filters} params={params} qDraft={qDraft} setQDraft={setQDraft}
            onChange={(p) => setParams((cur) => ({ ...cur, ...p }))}
            onRefresh={load} loading={loading}
            title={item?.title ?? ""} blurb={item?.blurb ?? ""} updated={shown?.generated_at}
          />

          {error && (
            <div role="alert" style={{ ...card, padding: 14, color: "var(--color-bad)", marginBottom: 14 }}>
              {error} <button style={btnSmall} onClick={load}>Retry</button>
            </div>
          )}

          {!shown && !error && <p style={{ color: muted, fontSize: 13 }}>Loading…</p>}

          {shown && (
            <div aria-busy={stale} style={{ opacity: stale ? 0.55 : 1, transition: "opacity .15s", display: "flex", flexDirection: "column", gap: 18 }}>
              {shown.notes.map((n, i) => (
                <p key={i} style={{ ...card, margin: 0, padding: "10px 14px", fontSize: 13, color: ink2, lineHeight: 1.5 }}>{n}</p>
              ))}

              {shown.kpis.length > 0 && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
                  {shown.kpis.map((k) => <KpiTile key={k.label} kpi={k} />)}
                </div>
              )}

              {shown.charts.length > 0 && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 380px), 1fr))", gap: 14 }}>
                  {shown.charts.map((c, i) => (
                    <ChartCard key={`${c.title}-${i}`} chart={c} exportHref={dataExportUrl(shown.id, { chart: i }, params)} />
                  ))}
                </div>
              )}

              {shown.tables.map((t) => (
                <DataTableView key={t.key} table={t} exportHref={dataExportUrl(shown.id, { table: t.key }, params)} />
              ))}

              {active === "overview" && <Catalog catalog={catalog} leakCount={leakCount} onOpen={pick} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Toolbar({ filters, params, qDraft, setQDraft, onChange, onRefresh, loading, title, blurb, updated }: {
  filters: string[];
  params: DataQuery;
  qDraft: string;
  setQDraft: (s: string) => void;
  onChange: (p: DataQuery) => void;
  onRefresh: () => void;
  loading: boolean;
  title: string;
  blurb: string;
  updated?: string;
}) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{title}</h2>
          <p style={{ margin: "3px 0 0", fontSize: 13, color: ink2, maxWidth: 720 }}>{blurb}</p>
        </div>
        <span style={{ fontSize: 12, color: muted }}>{updated ? `Updated ${when(updated)} UTC` : ""}</span>
      </div>
      {/* one row, above everything it scopes: every chart, tile and table below obeys it */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 12 }}>
        {filters.includes("days") && (
          <select aria-label="Time range" value={params.days} onChange={(e) => onChange({ days: Number(e.target.value) })}
            style={{ ...input, width: "auto", minWidth: 130 }}>
            {DAY_CHOICES.map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
        )}
        {filters.includes("q") && (
          <>
            <input style={{ ...input, maxWidth: 280 }} placeholder="Search actor, action, target, IP…" value={qDraft}
              onChange={(e) => setQDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onChange({ q: qDraft })} />
            <button style={btn} onClick={() => onChange({ q: qDraft })}>Search</button>
            {params.q && <button style={btnSmall} onClick={() => { setQDraft(""); onChange({ q: "" }); }}>Clear</button>}
          </>
        )}
        {filters.includes("limit") && (
          <select aria-label="Rows" value={params.limit} onChange={(e) => onChange({ limit: Number(e.target.value) })}
            style={{ ...input, width: "auto" }}>
            {ROW_CHOICES.map((n) => <option key={n} value={n}>{n} rows</option>)}
          </select>
        )}
        {filters.includes("bots") && (
          <label style={{ fontSize: 13, color: ink2, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={!!params.bots} onChange={(e) => onChange({ bots: e.target.checked })} />
            Include bots
          </label>
        )}
        <div style={{ flex: 1 }} />
        <button style={btn} onClick={onRefresh} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
      </div>
    </div>
  );
}

/** The home page of the tab: every dataset as a card, with how much it holds. */
function Catalog({ catalog, leakCount, onOpen }: {
  catalog: DataCatalogItem[]; leakCount: number | null; onOpen: (id: string) => void;
}) {
  const cards = catalog.filter((d) => d.id !== "overview");
  return (
    <section>
      <h3 style={{ margin: "6px 0 10px", fontSize: 14, fontWeight: 600 }}>All datasets</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {cards.map((d) => (
          <button key={d.id} onClick={() => onOpen(d.id)} style={{
            ...card, textAlign: "left", cursor: "pointer", padding: "12px 14px", color: "var(--color-text)",
            font: "inherit", display: "flex", flexDirection: "column", gap: 4,
          }}>
            <span style={{ fontSize: 11, color: muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>{d.group}</span>
            <span style={{ fontWeight: 600 }}>{d.title}</span>
            <span style={{ fontSize: 12, color: ink2, lineHeight: 1.45 }}>{d.blurb}</span>
            <span style={{ fontSize: 11, color: muted, marginTop: 2 }}>
              {d.records !== undefined ? `${d.records.toLocaleString()} records` : "computed live"}
              {d.newest ? ` · latest ${when(d.newest)}` : ""}
            </span>
          </button>
        ))}
        <button onClick={() => onOpen(LEAKS)} style={{
          ...card, textAlign: "left", cursor: "pointer", padding: "12px 14px", color: "var(--color-text)",
          font: "inherit", display: "flex", flexDirection: "column", gap: 4,
        }}>
          <span style={{ fontSize: 11, color: muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>Search engine</span>
          <span style={{ fontWeight: 600 }}>Leak datasets</span>
          <span style={{ fontSize: 12, color: ink2, lineHeight: 1.45 }}>
            Files you add (.txt, .csv, .json) that the Leak search looks through alongside the public sources.
          </span>
          <span style={{ fontSize: 11, color: muted, marginTop: 2 }}>
            {leakCount === null ? "" : `${leakCount} dataset${leakCount === 1 ? "" : "s"}`}
          </span>
        </button>
      </div>
    </section>
  );
}

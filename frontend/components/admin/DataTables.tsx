"use client";

/** Stat tiles and sortable tables for the admin Data area. */

import { useMemo, useState } from "react";
import type { DataColumn, DataKpi, DataTable } from "@/lib/types";
import { Pill, badgeTone, btnSmall, card, fmtValue, ink2, muted, toneColor, when } from "./ui";

export function KpiTile({ kpi }: { kpi: DataKpi }) {
  const color = kpi.tone ? toneColor(kpi.tone) : "var(--color-text)";
  const text = fmtValue(kpi.value, kpi.fmt);
  return (
    <div style={{ ...card, padding: "14px 16px", minWidth: 0 }}>
      <div style={{ fontSize: 12, color: ink2 }}>{kpi.label}</div>
      <div style={{
        fontSize: kpi.fmt === "text" ? 20 : 26, fontWeight: 600, color, lineHeight: 1.25, marginTop: 2,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }} title={text}>{text}</div>
      {kpi.sub && <div style={{ fontSize: 11, color: muted, marginTop: 2 }}>{kpi.sub}</div>}
    </div>
  );
}

const NUMERIC = new Set(["int", "money", "pct", "hours", "days", "bytes"]);
const PAGE = 50;

function Cell({ col, value }: { col: DataColumn; value: string | number | null }) {
  if (value === null || value === undefined || value === "") return <span style={{ color: muted }}>—</span>;
  switch (col.kind) {
    case "int": case "money": case "pct": case "hours": case "days": case "bytes":
      return <>{fmtValue(Number(value), col.kind)}</>;
    case "ts": return <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: ink2, whiteSpace: "nowrap" }}>{when(String(value))}</span>;
    case "date": return <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: ink2 }}>{String(value)}</span>;
    case "mono": return <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{String(value)}</span>;
    case "badge": return <Pill text={String(value)} tone={badgeTone(String(value))} />;
    default: return <>{String(value)}</>;
  }
}

export function DataTableView({ table, exportHref }: { table: DataTable; exportHref?: string }) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const [all, setAll] = useState(false);

  const rows = useMemo(() => {
    if (!sort) return table.rows;
    const { key, dir } = sort;
    return [...table.rows].sort((a, b) => {
      const x = a[key], y = b[key];
      if (x === null || x === undefined) return 1;      // empty values always sink
      if (y === null || y === undefined) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
    });
  }, [table.rows, sort]);

  const shown = all ? rows : rows.slice(0, PAGE);
  const clickSort = (key: string) =>
    setSort((s) => (s?.key === key ? (s.dir === 1 ? { key, dir: -1 } : null) : { key, dir: 1 }));

  return (
    <section style={{ ...card, overflow: "hidden" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, padding: "12px 14px 8px" }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
            {table.title} <span style={{ fontWeight: 400, color: muted, fontSize: 12 }}>· {table.rows.length}</span>
          </h3>
          {table.note && <p style={{ margin: "3px 0 0", fontSize: 12, color: muted }}>{table.note}</p>}
        </div>
        {exportHref && table.rows.length > 0 && (
          <a style={{ ...btnSmall, opacity: 0.75, flexShrink: 0 }} href={exportHref} download>Export CSV</a>
        )}
      </header>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", fontSize: 12 }}>
              {table.columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th key={c.key} scope="col" aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}
                    style={{ padding: "8px 12px", fontWeight: 500, borderBottom: "1px solid var(--color-divider)",
                      textAlign: NUMERIC.has(c.kind) ? "right" : "left", whiteSpace: "nowrap" }}>
                    <button onClick={() => clickSort(c.key)} style={{
                      all: "unset", cursor: "pointer", color: active ? "var(--color-text)" : ink2,
                    }}>
                      {c.label}{active ? (sort!.dir === 1 ? " ↑" : " ↓") : ""}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--color-divider)" }}>
                {table.columns.map((c) => (
                  <td key={c.key} style={{
                    padding: "7px 12px", verticalAlign: "top", maxWidth: 360, overflowWrap: "anywhere",
                    textAlign: NUMERIC.has(c.kind) ? "right" : "left",
                    fontVariantNumeric: NUMERIC.has(c.kind) ? "tabular-nums" : undefined,
                  }}><Cell col={c} value={r[c.key]} /></td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={table.columns.length} style={{ padding: 22, textAlign: "center", color: muted }}>
                Nothing here yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      {rows.length > PAGE && (
        <div style={{ padding: "8px 14px", borderTop: "1px solid var(--color-divider)" }}>
          <button style={btnSmall} onClick={() => setAll((v) => !v)}>
            {all ? `Show first ${PAGE}` : `Show all ${rows.length}`}
          </button>
        </div>
      )}
    </section>
  );
}

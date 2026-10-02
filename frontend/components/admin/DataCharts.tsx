"use client";

/**
 * Charts for the admin Data area: a per-day bar or line chart and a ranked
 * horizontal bar list, drawn as plain SVG/HTML — there is no chart library in
 * this app and three simple forms do not justify one.
 *
 * Form follows the data's job. A value per day is a column or line chart; a
 * ranked set of categories is a bar list, one colour (the categories carry no
 * order of their own, so a ramp or a rainbow would only restate bar length).
 * Marks are thin, gridlines are solid hairlines, text wears text tokens and
 * never a series colour. Every chart can flip to a table of the exact values,
 * because a tooltip must never be the only way to read a number.
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DataBar, DataChart, DataFmt, DataPoint } from "@/lib/types";
import { btnSmall, card, fmtAxis, fmtValue, ink2, muted } from "./ui";

const SERIES = "var(--color-accent)";
const SERIES_HOVER = "var(--color-accent-400)";
const GRID = "var(--color-divider)";

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** Round tick spacing (1, 2, 5 × 10ⁿ), whole numbers for counts. */
function ticksFor(max: number, integer: boolean): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / 3;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / p;
  let step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  if (integer) step = Math.max(1, Math.round(step));
  const out: number[] = [];
  for (let t = 0; ; t += step) {
    out.push(t);
    if (t >= max) break;
  }
  return out;
}

const xLabel = (x: string) => (/^\d{4}-\d{2}-\d{2}$/.test(x) ? x.slice(5) : x);

// ── per-day chart ──

function TimeChart({ chart, points }: { chart: Extract<DataChart, { kind: "bars" | "line" }>; points: DataPoint[] }) {
  const [wrap, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const H = 190, mL = 46, mR = 10, mT = 10, mB = 24;
  const n = points.length;
  const vals = points.map((p) => p.y);
  const present = vals.filter((v): v is number => v !== null);
  const peak = present.length ? Math.max(...present) : 0;
  const integer = chart.fmt === "int";
  const ticks = useMemo(() => ticksFor(peak, integer), [peak, integer]);
  const top = ticks[ticks.length - 1] || 1;
  const innerW = Math.max(width - mL - mR, 10);
  const innerH = H - mT - mB;
  const band = n ? innerW / n : innerW;
  const px = (i: number) => mL + band * i + band / 2;
  const py = (v: number) => mT + innerH - (v / top) * innerH;
  const empty = present.length === 0 || peak === 0;

  const gap = band > 5 ? 2 : band > 2.5 ? 1 : 0;
  const barW = Math.max(1, Math.min(24, band - gap));

  const move = (clientX: number, rect: DOMRect) => {
    const i = Math.round((clientX - rect.left - mL - band / 2) / band);
    setHover(Math.min(Math.max(i, 0), n - 1));
  };

  const every = Math.max(1, Math.ceil(n / Math.max(Math.floor(innerW / 62), 1)));

  // A line breaks where the series has no value — a day with no snapshot is
  // not a day at zero.
  const segments: string[] = [];
  if (chart.kind === "line") {
    let cur = "";
    points.forEach((p, i) => {
      if (p.y === null) { if (cur) segments.push(cur); cur = ""; return; }
      cur += `${cur ? "L" : "M"}${px(i).toFixed(1)},${py(p.y).toFixed(1)}`;
    });
    if (cur) segments.push(cur);
  }
  const lastIdx = (() => { for (let i = n - 1; i >= 0; i--) if (vals[i] !== null) return i; return -1; })();

  const hp = hover !== null ? points[hover] : null;
  const tipLeft = hover !== null ? Math.min(Math.max(px(hover) - 62, 4), Math.max(width - 130, 4)) : 0;

  return (
    <div ref={wrap} style={{ position: "relative", width: "100%" }}>
      {width > 0 && (
        <svg
          width={width} height={H} role="img" tabIndex={0}
          aria-label={`${chart.title}. ${n} points, peak ${fmtValue(peak, chart.fmt)}. Use the table view for exact values.`}
          style={{ display: "block", outline: "none", touchAction: "pan-y" }}
          onPointerMove={(e) => move(e.clientX, e.currentTarget.getBoundingClientRect())}
          onPointerLeave={() => setHover(null)}
          onFocus={() => setHover(lastIdx >= 0 ? lastIdx : n - 1)}
          onBlur={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") { e.preventDefault(); setHover((h) => Math.max((h ?? n) - 1, 0)); }
            else if (e.key === "ArrowRight") { e.preventDefault(); setHover((h) => Math.min((h ?? -1) + 1, n - 1)); }
            else if (e.key === "Escape") setHover(null);
          }}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={mL} x2={width - mR} y1={py(t)} y2={py(t)} stroke={GRID} strokeWidth={1} />
              <text x={mL - 8} y={py(t) + 4} textAnchor="end" fontSize={11} fill={muted}
                style={{ fontVariantNumeric: "tabular-nums" }}>{fmtAxis(t, chart.fmt)}</text>
            </g>
          ))}
          {/* labels step back evenly from the latest point, so "today" is always named */}
          {points.map((p, i) => (n - 1 - i) % every === 0 && (
            <text key={i} x={px(i)} y={H - 6} textAnchor="middle" fontSize={11} fill={muted}>{xLabel(p.x)}</text>
          ))}

          {chart.kind === "bars" && points.map((p, i) => {
            if (!p.y) return null;
            const h = Math.max(innerH - (py(p.y) - mT), 1);
            const x = px(i) - barW / 2, y = mT + innerH - h, r = Math.min(4, barW / 2, h);
            return (
              <path key={i} fill={hover === i ? SERIES_HOVER : SERIES}
                d={`M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + barW - r}Q${x + barW},${y} ${x + barW},${y + r}V${y + h}Z`} />
            );
          })}

          {chart.kind === "line" && (
            <>
              {segments.map((d, i) => (
                <path key={i} d={d} fill="none" stroke={SERIES} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              ))}
              {lastIdx >= 0 && (
                <circle cx={px(lastIdx)} cy={py(vals[lastIdx] as number)} r={4} fill={SERIES}
                  stroke="var(--color-surface)" strokeWidth={2} />
              )}
            </>
          )}

          {hover !== null && (
            <>
              <line x1={px(hover)} x2={px(hover)} y1={mT} y2={mT + innerH} stroke={ink2} strokeWidth={1} opacity={0.5} />
              {chart.kind === "line" && points[hover].y !== null && hover !== lastIdx && (
                <circle cx={px(hover)} cy={py(points[hover].y as number)} r={4} fill={SERIES}
                  stroke="var(--color-surface)" strokeWidth={2} />
              )}
            </>
          )}
          {empty && (
            <text x={mL + innerW / 2} y={mT + innerH / 2} textAnchor="middle" fontSize={13} fill={muted}>
              {present.length === 0 ? "No data in this period" : "Nothing recorded in this period"}
            </text>
          )}
        </svg>
      )}
      {hp && (
        <div style={{
          position: "absolute", top: 0, left: tipLeft, pointerEvents: "none", zIndex: 5,
          background: "var(--color-surface-2)", border: "1px solid var(--color-divider)",
          borderRadius: "var(--radius-md)", padding: "6px 10px", boxShadow: "var(--shadow-md)", minWidth: 118,
        }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>{fmtValue(hp.y, chart.fmt)}</div>
          <div style={{ fontSize: 12, color: ink2 }}>{hp.x}</div>
        </div>
      )}
    </div>
  );
}

// ── ranked categories ──

function HBars({ chart, points }: { chart: Extract<DataChart, { kind: "hbars" }>; points: DataBar[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...points.map((p) => p.value), 1);
  if (points.length === 0) {
    return <p style={{ margin: "18px 0 8px", fontSize: 13, color: muted }}>No data in this period.</p>;
  }
  return (
    <div role="list" aria-label={chart.title} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {points.map((p, i) => (
        <div key={`${p.label}-${i}`} role="listitem" tabIndex={0}
          title={`${p.label}: ${fmtValue(p.value, chart.fmt)} (${p.share}%)`}
          onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
          onFocus={() => setHover(i)} onBlur={() => setHover(null)}
          style={{
            display: "grid", gridTemplateColumns: "minmax(80px, 36%) 1fr", alignItems: "center", gap: 10,
            padding: "3px 6px", margin: "0 -6px", borderRadius: "var(--radius-sm)", outline: "none",
            background: hover === i ? "var(--color-surface-2)" : "transparent",
          }}>
          <span style={{
            fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            color: p.muted ? muted : "var(--color-text)",
          }}>{p.label}</span>
          <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <span style={{
              height: 10, width: `${Math.max((p.value / max) * 100, 1.5)}%`, maxWidth: "calc(100% - 64px)",
              borderRadius: "0 4px 4px 0",
              background: p.muted ? "var(--color-neutral-600)" : hover === i ? SERIES_HOVER : SERIES,
            }} />
            <span style={{ fontSize: 12, color: ink2, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
              {fmtValue(p.value, chart.fmt)}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

// ── card with a table-view twin ──

export function ChartCard({ chart, exportHref }: { chart: DataChart; exportHref?: string }) {
  const [asTable, setAsTable] = useState(false);
  const isBars = chart.kind === "hbars";
  return (
    <div style={{ ...card, padding: "14px 16px 12px", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{chart.title}</h3>
        <span style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <button style={{ ...btnSmall, opacity: asTable ? 1 : 0.7 }} aria-pressed={asTable}
            onClick={() => setAsTable((v) => !v)}>{asTable ? "Chart" : "Table"}</button>
          {exportHref && <a style={{ ...btnSmall, opacity: 0.7 }} href={exportHref} download>CSV</a>}
        </span>
      </div>
      {chart.note && <p style={{ margin: "0 0 6px", fontSize: 12, color: muted }}>{chart.note}</p>}
      {asTable ? (
        <PointsTable chart={chart} />
      ) : isBars ? (
        <HBars chart={chart as Extract<DataChart, { kind: "hbars" }>} points={chart.points as DataBar[]} />
      ) : (
        <TimeChart chart={chart as Extract<DataChart, { kind: "bars" | "line" }>} points={chart.points as DataPoint[]} />
      )}
    </div>
  );
}

function PointsTable({ chart }: { chart: DataChart }) {
  const rows: [string, string, string?][] = chart.kind === "hbars"
    ? (chart.points as DataBar[]).map((p) => [p.label, fmtValue(p.value, chart.fmt), `${p.share}%`])
    : [...(chart.points as DataPoint[])].reverse().map((p) => [p.x, fmtValue(p.y, chart.fmt)]);
  return (
    <div style={{ maxHeight: 220, overflowY: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <tbody>
          {rows.map(([a, b, c], i) => (
            <tr key={i} style={{ borderBottom: `1px solid ${GRID}` }}>
              <td style={{ padding: "4px 0", color: ink2 }}>{a}</td>
              <td style={{ padding: "4px 0", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{b}</td>
              {c !== undefined && <td style={{ padding: "4px 0 4px 12px", textAlign: "right", color: muted, fontVariantNumeric: "tabular-nums" }}>{c}</td>}
            </tr>
          ))}
          {rows.length === 0 && <tr><td style={{ padding: 8, color: muted }}>No data in this period.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export type { DataFmt };

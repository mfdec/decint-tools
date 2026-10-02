"use client";

/**
 * Shared look and number formatting for the admin Data area. The older admin
 * components each keep their own copy of the style constants; new ones share
 * this file rather than adding a third.
 */

import type { DataFmt } from "@/lib/types";

export const card: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-divider)",
  borderRadius: "var(--radius-lg)",
};

export const btn: React.CSSProperties = {
  fontFamily: "var(--font-body)", fontSize: 14, cursor: "pointer",
  padding: "7px 12px", borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-divider)", background: "transparent",
  color: "var(--color-text)", textDecoration: "none", display: "inline-block",
};
export const btnPrimary: React.CSSProperties = {
  ...btn, background: "var(--color-accent)", borderColor: "var(--color-accent)",
  color: "#fff", fontWeight: 600,
};
export const btnDanger: React.CSSProperties = {
  ...btn, color: "var(--color-bad)", borderColor: "var(--color-bad)",
};
export const btnSmall: React.CSSProperties = { ...btn, fontSize: 12, padding: "4px 9px" };

export const input: React.CSSProperties = {
  fontFamily: "var(--font-body)", fontSize: 14, color: "var(--color-text)",
  background: "var(--color-bg)", border: "1px solid var(--color-divider)",
  borderRadius: "var(--radius-md)", padding: "7px 10px", width: "100%",
};

/** Secondary and muted ink — text never wears a series colour. */
export const ink2 = "color-mix(in srgb, var(--color-text) 72%, transparent)";
export const muted = "color-mix(in srgb, var(--color-text) 52%, transparent)";

export type Tone = "ok" | "warn" | "bad" | "muted";

export const toneColor = (t?: Tone | null) =>
  t === "ok" ? "var(--color-ok)" :
  t === "warn" ? "var(--color-warn)" :
  t === "bad" ? "var(--color-bad)" : "var(--color-text)";

export function Pill({ text, tone }: { text: string; tone?: Tone }) {
  const color = toneColor(tone);
  return (
    <span style={{
      fontSize: 12, fontFamily: "var(--mono)", color,
      border: `1px solid ${color}`, borderRadius: "var(--radius-sm)",
      padding: "1px 6px", opacity: tone === "muted" || !tone ? 0.75 : 1, whiteSpace: "nowrap",
    }}>{text}</span>
  );
}

/** Which badge words mean good, bad or "look at this" — a status never relies
 *  on colour alone (the word is always printed). */
export function badgeTone(v: string): Tone {
  const s = v.toLowerCase();
  if (["on", "ok", "yes", "active", "paid", "ready", "resolved", "installed", "admin"].includes(s)) return "ok";
  if (["off", "failed", "missing", "suspended", "blocked"].includes(s) || s.startsWith("no")) return "bad";
  if (["pending", "open", "expired", "processing", "uploading", "removing", "paused"].includes(s)) return "warn";
  return "muted";
}

// ── numbers ──

const nf = new Intl.NumberFormat("en-US");

export function compact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e9) return `${+(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${+(n / 1e3).toFixed(1)}k`;
  return nf.format(+n.toFixed(a < 10 ? 1 : 0));
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
}

export function fmtMoney(cents: number): string {
  return `${cents < 0 ? "-" : ""}$${(Math.abs(cents) / 100).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

export function fmtHours(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}

/** One value, printed the way its dataset said to. Null is an honest dash. */
export function fmtValue(v: number | string | null | undefined, fmt: DataFmt): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  switch (fmt) {
    case "int": return nf.format(Math.round(v));
    case "float": return nf.format(+v.toFixed(1));
    case "money": return fmtMoney(v);
    case "pct": return `${+v.toFixed(1)}%`;
    case "hours": return fmtHours(v);
    case "days": return `${nf.format(+v.toFixed(1))} d`;
    case "bytes": return fmtBytes(v);
    default: return String(v);
  }
}

/** Short form for axis ticks, where space is tight. */
export function fmtAxis(v: number, fmt: DataFmt): string {
  switch (fmt) {
    case "money": return `$${compact(v / 100)}`;
    case "pct": return `${compact(v)}%`;
    case "bytes": return fmtBytes(v);
    case "hours": return fmtHours(v);
    default: return compact(v);
  }
}

export const when = (iso: string | null | undefined) =>
  iso ? iso.slice(0, 16).replace("T", " ") : "—";

"use client";

import * as React from "react";
import { AppDef, AppKey } from "@/lib/apps";
import { Search } from "@/components/icons";

interface Props {
  apps: AppDef[];
  active: AppKey;
  onSelect: (k: AppKey) => void;
  onClose: () => void;
}

export function CommandPalette({ apps, active, onSelect, onClose }: Props) {
  const [q, setQ] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, []);

  const filtered = q.trim()
    ? apps.filter((a) => `${a.name} ${a.desc} ${a.key}`.toLowerCase().includes(q.trim().toLowerCase()))
    : apps;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 200, display: "grid",
        placeItems: "start center", paddingTop: "16vh",
        background: "color-mix(in srgb, black 55%, transparent)",
        backdropFilter: "blur(2px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="dc-fade"
        style={{
          width: "min(560px, 92vw)",
          background: "color-mix(in srgb, black 30%, var(--color-surface))",
          border: "1px solid var(--color-divider)", borderRadius: 12,
          boxShadow: "0 24px 60px rgba(0,0,0,.6)", overflow: "hidden",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderBottom: "1px solid var(--color-divider)" }}>
          <Search size={16} style={{ color: "var(--color-neutral-500)" }} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && filtered.length) onSelect(filtered[0].key);
            }}
            placeholder="Switch app…"
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontSize: 15, fontFamily: "var(--font-body)" }}
          />
        </div>
        <div style={{ padding: 6 }}>
          {filtered.map((app) => (
            <button key={app.key} type="button" onClick={() => onSelect(app.key)} className="pal-row">
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)", width: 34 }}>{app.tty}</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 14, color: "var(--color-neutral-100)", width: 74 }}>{app.name}</span>
              <span style={{ fontSize: 13, color: "var(--color-neutral-500)", flex: 1 }}>{app.desc}</span>
              {app.key === active && <span className="tag tag-outline">current</span>}
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-600)", width: 26, textAlign: "right" }}>{app.hot}</span>
            </button>
          ))}
          {filtered.length === 0 && (
            <div style={{ padding: "18px 12px", color: "var(--color-neutral-500)", fontSize: 13 }}>No matching app.</div>
          )}
        </div>
        <div style={{ display: "flex", gap: 16, padding: "9px 14px", borderTop: "1px solid var(--color-divider)", fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)" }}>
          <span>↵ open</span><span>esc close</span><span style={{ marginLeft: "auto" }}>DECINT-console</span>
        </div>
      </div>
      <style jsx>{`
        .pal-row { display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px;
          background: none; border: 0; border-radius: 8px; cursor: pointer;
          color: var(--color-text); font-family: var(--font-body); text-align: left; }
        .pal-row:hover { background: color-mix(in srgb, var(--color-text) 6%, transparent); }
      `}</style>
    </div>
  );
}

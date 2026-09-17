"use client";

import * as React from "react";
import type { Dispatch } from "../Console";
import { AppDef, AppKey } from "@/lib/apps";
import type { HealthResponse } from "@/lib/types";

type Line = { id: number; kind: "sys" | "cmd" | "out" | "ok" | "err"; text: string };

const COLORS: Record<Line["kind"], string> = {
  sys: "#7ba5e8", cmd: "#e4e7f5", out: "#9397ab", ok: "#7fce9e", err: "#e8908f",
};

let LID = 0;
const L = (kind: Line["kind"], text: string): Line => ({ id: ++LID, kind, text });

export function ReconApp({
  dispatch, health, apps,
}: {
  dispatch: Dispatch;
  health: HealthResponse | null;
  apps: AppDef[];
}) {
  const [history, setHistory] = React.useState<Line[]>(() => [
    L("sys", `DECINT-console ${health?.version ? "v" + health.version : "1.0.0"}   ·   tty1   ·   secure session`),
    L("sys", "type 'help' for commands   ·   'open <app>' to switch"),
    L("out", ""),
  ]);
  const [cmd, setCmd] = React.useState("");
  const termRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight;
  }, [history]);

  const appKeys = apps.map((a) => a.key);

  function run() {
    const raw = cmd.trim();
    if (!raw) return;
    const prompt = L("cmd", "operator@decint:~$ " + raw);
    const [c, ...rest] = raw.split(/\s+/);
    const arg = rest.join(" ");
    const push = (...ls: Line[]) => setHistory((h) => [...h, ...ls]);

    if (c === "clear") { setHistory([]); setCmd(""); return; }
    if (c === "help") {
      push(prompt,
        L("out", "apps:    recon   leaks   darkweb   discord" + (health?.sniffer_enabled ? "   packets" : "") + "   visitors"),
        L("out", "switch:  open <app>    ⌘K    ⌃1–⌃6"),
        L("out", "search:  leaks <email|user|domain>   darkweb <keyword>   discord <id|invite>"),
        L("out", "more:    tor · clear"));
      setCmd(""); return;
    }
    if (c === "tor") {
      push(prompt, health?.tor
        ? L("ok", "tor up · " + (health.tor_detail || "connected"))
        : L("err", "tor down · " + (health?.tor_detail || "unreachable")));
      setCmd(""); return;
    }
    if (c === "open") {
      if (appKeys.includes(arg as AppKey)) { push(prompt, L("ok", "→ " + arg)); dispatch.open(arg as AppKey); }
      else push(prompt, L("err", "no app: " + (arg || "?")));
      setCmd(""); return;
    }
    if (c === "leaks" || c === "darkweb" || c === "discord") {
      if (!arg) { push(prompt, L("err", `usage: ${c} <query>`)); setCmd(""); return; }
      push(prompt, L("ok", `→ ${c}  “${arg}”`));
      dispatch.open(c as AppKey, arg);
      setCmd(""); return;
    }
    if (c === "scan") {
      push(prompt,
        L("out", "resolving " + (arg || "target") + " …"),
        L("out", "tip: use 'leaks " + (arg || "domain.com") + "' or 'darkweb " + (arg || "keyword") + "'"));
      setCmd(""); return;
    }
    push(prompt, L("err", `command not found: ${c}   (try 'help')`));
    setCmd("");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: "18px 24px 14px" }}>
      <div ref={termRef} style={{ flex: 1, overflow: "auto", paddingRight: 8 }}>
        {history.map((line) => (
          <div key={line.id} style={{
            color: COLORS[line.kind], whiteSpace: "pre-wrap", wordBreak: "break-word",
            fontFamily: "var(--mono)", fontSize: 12.5, lineHeight: 1.75, minHeight: "1.05em",
          }}>{line.text}</div>
        ))}
      </div>
      <div style={{ display: "flex", alignItems: "center", paddingTop: 11, marginTop: 8, borderTop: "1px solid var(--color-divider)", fontFamily: "var(--mono)", fontSize: 12.5 }}>
        <span style={{ color: "#7ba5e8" }}>operator@decint</span>
        <span style={{ color: "#595d6c" }}>:</span>
        <span style={{ color: "#8fb2e6" }}>~</span>
        <span style={{ color: "#595d6c" }}>$&nbsp;</span>
        <input
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); run(); } }}
          placeholder="type a command…  (help)"
          autoFocus
          spellCheck={false}
          style={{ flex: 1, background: "none", border: 0, outline: "none", color: "#e4e7f5", fontFamily: "var(--mono)", fontSize: 12.5, caretColor: "var(--color-accent)" }}
        />
      </div>
    </div>
  );
}

"use client";

import * as React from "react";
import type { Dispatch, OpenOptions } from "../Console";
import { AppDef, AppKey } from "@/lib/apps";
import { classifyTarget, commandByName, helpFor, helpIndex, parseArgs, usage } from "@/lib/commands";
import { isSha1 } from "@/lib/password";
import type { DarkwebMode, DiscordMode, HealthResponse, LeakKind } from "@/lib/types";

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
    L("sys", "type 'help' for commands   ·   'help <command>' for its arguments   ·   'open <app>' to switch"),
    L("out", ""),
  ]);
  const [cmd, setCmd] = React.useState("");
  const termRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight;
  }, [history]);

  function run() {
    const raw = cmd.trim();
    if (!raw) return;
    const prompt = L("cmd", "operator@decint:~$ " + raw);
    const [word, ...rest] = raw.split(/\s+/);
    // "password" is the likely slip, and it must reach the guard below rather
    // than "command not found", which would leave its argument on screen.
    const c = word === "password" ? "passwords" : word;
    const push = (...ls: Line[]) => setHistory((h) => [...h, ...ls]);
    setCmd("");

    if (c === "clear") { setHistory([]); return; }

    // The shell keeps every line on screen, so `passwords` takes a hash and
    // nothing else. Anything else may be a password: it is refused, and the
    // echoed prompt shows dots in its place — before help or argument errors
    // get a chance to quote it back.
    if (c === "passwords") {
      const args = rest.filter((t) => t !== "--help" && t !== "-h");
      if (args.some((t) => !isSha1(t))) {
        push(L("cmd", "operator@decint:~$ passwords ••••••••"),
          L("err", "the shell only takes a SHA-1 hash here, never a password: it keeps what you type on screen"),
          L("out", "run 'passwords' on its own and type the password into the checker, which hashes it in this tab"));
        return;
      }
      if (args.length > 1) {
        push(prompt, L("err", "one hash at a time here"), L("out", "for a list, run 'passwords' and use batch mode"));
        return;
      }
    }

    const spec = commandByName(c);
    if (!spec) {
      push(prompt, L("err", `command not found: ${c}`), L("out", "type 'help' to see what's available"));
      return;
    }

    // `<command> --help` reads the same as `help <command>`.
    if (rest.some((t) => t === "--help" || t === "-h")) {
      push(prompt, ...helpFor(c, apps)!.map((t) => L("out", t)));
      return;
    }

    if (c === "help") {
      const topic = rest[0];
      if (!topic) { push(prompt, ...helpIndex(apps).map((t) => L("out", t))); return; }
      const lines = helpFor(topic, apps);
      if (lines) push(prompt, ...lines.map((t) => L("out", t)));
      else push(prompt, L("err", `no such command: ${topic}`), L("out", "type 'help' to see what's available"));
      return;
    }

    const parsed = parseArgs(spec, rest, apps);
    if (parsed.errors.length) {
      push(prompt,
        ...parsed.errors.map((e) => L("err", e)),
        L("out", `usage: ${usage(spec)}`),
        L("out", `try 'help ${c}' for details`));
      return;
    }
    const { query, flags } = parsed;

    if (c === "tor") {
      push(prompt, health?.tor
        ? L("ok", "tor up · " + (health.tor_detail || "connected"))
        : L("err", "tor down · " + (health?.tor_detail || "unreachable")));
      return;
    }
    if (c === "open") {
      push(prompt, L("ok", "→ " + query));
      dispatch.open(query as AppKey);
      return;
    }
    if (c === "leaks" || c === "spider" || c === "darkweb" || c === "discord") {
      const opts: OpenOptions = {
        kind: flags.kind as LeakKind | undefined,
        mode: flags.mode as DarkwebMode | undefined,
        experimental: flags.engines ? flags.engines === "all" : undefined,
        pages: flags.pages ? Number(flags.pages) : undefined,
        as: flags.as as DiscordMode | undefined,
      };
      const set = Object.entries(flags).map(([k, v]) => ` --${k} ${v}`).join("");
      push(prompt, L("ok", `→ ${c}  “${query}”${set}`));
      dispatch.open(c as AppKey, query, opts);
      return;
    }
    if (c === "passwords") {
      const hash = query.toLowerCase();
      push(prompt, L("ok", hash ? `→ passwords  ${hash}` : "→ passwords"));
      dispatch.open("passwords", hash || undefined);
      return;
    }
    if (c === "scan") {
      const hit = classifyTarget(query);
      push(prompt,
        L("out", `${query} looks like a ${hit.kind}`),
        L("out", `run:  ${hit.command}`));
      return;
    }
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
          placeholder="type a command…  (help, or help <command>)"
          autoFocus
          spellCheck={false}
          style={{ flex: 1, background: "none", border: 0, outline: "none", color: "#e4e7f5", fontFamily: "var(--mono)", fontSize: 12.5, caretColor: "var(--color-accent)" }}
        />
      </div>
    </div>
  );
}

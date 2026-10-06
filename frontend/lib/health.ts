import type { AdminHealthReport, HealthState, SourceHealth, SourceState } from "./types";

/**
 * The admin `health` command's printout, as shell lines. Pure, so the shell
 * only has to fetch the report and push what comes back.
 *
 * Each line carries the colour it should print in: "ok" green, "warn" amber,
 * "err" red, "out" grey, "sys" blue for headings.
 */

export type HealthLineKind = "sys" | "out" | "ok" | "warn" | "err";
export interface HealthLine { kind: HealthLineKind; text: string }

const TOOL_LABEL: Record<string, string> = {
  leaks: "leak search",
  passwords: "password checker",
  ip: "ip lookup",
  domain: "domain lookup",
  phone: "phone lookup",
};
const TOOL_ORDER = ["leaks", "passwords", "ip", "domain", "phone"];

const SOURCE_MARK: Record<SourceState, [string, HealthLineKind]> = {
  ok: ["●", "ok"],
  degraded: ["◐", "warn"],
  down: ["✕", "err"],
  idle: ["○", "out"],
};
const CHECK_MARK: Record<HealthState, [string, HealthLineKind]> = {
  ok: ["●", "ok"],
  warn: ["◐", "warn"],
  down: ["✕", "err"],
  off: ["○", "out"],
};

export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function uptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function sourceRow(s: SourceHealth, width: number, probed: boolean, now: number): HealthLine {
  const [mark, kind] = SOURCE_MARK[s.state];
  const head = `    ${mark} ${s.state.padEnd(8)} ${s.label.padEnd(width)}`;
  const tag = probed ? "  [probed]" : "";
  if (s.state === "idle") return { kind, text: `${head}  no calls since the restart${tag}` };
  const counts = `${String(s.ok).padStart(4)} ok ${String(s.failed).padStart(4)} failed`;
  const speed = s.latency_ms !== null ? `${String(s.latency_ms).padStart(5)} ms` : "      —  ";
  const tail = s.state === "ok"
    ? `last ok ${ago(s.last_ok, now)}`
    : `${s.last_error ?? "failed"} · ${ago(s.last_failure, now)}`;
  return { kind, text: `${head}  ${counts}  ${speed}   ${tail}${tag}` };
}

export function healthLines(rep: AdminHealthReport, now = Date.now()): HealthLine[] {
  const out: HealthLine[] = [];
  const probed = new Set(rep.probed);
  out.push({
    kind: "sys",
    text: `DECINT health · v${rep.version} · up ${uptime(rep.uptime_s)}` +
      (rep.probed_at ? ` · live probe ${ago(rep.probed_at, now)}` : " · no live probe yet"),
  });

  out.push({ kind: "out", text: "" }, { kind: "sys", text: "system" });
  const cw = Math.max(...rep.checks.map((c) => c.label.length));
  for (const c of rep.checks) {
    const [mark, kind] = CHECK_MARK[c.state];
    out.push({ kind, text: `  ${mark} ${c.state.padEnd(5)} ${c.label.padEnd(cw)}  ${c.detail}` });
  }

  out.push({ kind: "out", text: "" }, { kind: "sys", text: "outside sources (since the last restart)" });
  const sw = Math.max(...rep.sources.map((s) => s.label.length));
  const tools = [...new Set(rep.sources.map((s) => s.tool))].sort(
    (a, b) => (TOOL_ORDER.indexOf(a) + 1 || 99) - (TOOL_ORDER.indexOf(b) + 1 || 99)
  );
  for (const tool of tools) {
    out.push({ kind: "out", text: `  ${TOOL_LABEL[tool] ?? tool}` });
    for (const s of rep.sources.filter((x) => x.tool === tool)) {
      out.push(sourceRow(s, sw, probed.has(`${s.tool}.${s.key}`), now));
    }
  }

  if (rep.darkweb.length) {
    out.push({ kind: "out", text: "" }, { kind: "sys", text: "dark-web engines" });
    for (const d of rep.darkweb) {
      if (d.error) { out.push({ kind: "err", text: `  ✕ ${d.mode}: ${d.error}` }); continue; }
      const bad = (d.benched ?? 0) > 0;
      out.push({
        kind: bad ? "warn" : "ok",
        text: `  ${bad ? "◐" : "●"} ${d.mode.padEnd(8)} ${d.engines} engines · ${d.healthy} healthy · ` +
          `${d.benched} benched · ${d.untested} untried`,
      });
      if (d.benched_names?.length) out.push({ kind: "out", text: `      benched: ${d.benched_names.join(", ")}` });
    }
  }

  const n = (st: SourceState) => rep.sources.filter((s) => s.state === st).length;
  const nc = (st: HealthState) => rep.checks.filter((c) => c.state === st).length;
  const down = n("down") + nc("down");
  const warn = n("degraded") + nc("warn");
  out.push({ kind: "out", text: "" });
  out.push({
    kind: down ? "err" : warn ? "warn" : "ok",
    text: `summary: ${down} down · ${warn} degraded · ${n("ok") + nc("ok")} ok · ${n("idle")} not used yet`,
  });
  if (!rep.probed_at || n("idle")) {
    out.push({ kind: "out", text: "'health live' sends one canary query to every free source first" });
  }
  return out;
}

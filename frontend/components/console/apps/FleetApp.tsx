"use client";

import * as React from "react";
import { api, fleetStreamUrl } from "@/lib/api";
import type { FleetRun, FleetState, FleetTask } from "@/lib/types";

type Mode = "script" | "adhoc";

const DOT: Record<string, string> = {
  online: "var(--color-good, #3fb950)",
  unreachable: "var(--color-bad, #f85149)",
  error: "var(--color-bad, #f85149)",
  checking: "var(--color-accent-400, #58a6ff)",
  unknown: "var(--color-neutral-600, #5d6b7d)",
};

const STATUS_COLOR: Record<string, string> = {
  success: "var(--color-good, #3fb950)",
  failed: "var(--color-bad, #f85149)",
  error: "var(--color-bad, #f85149)",
  running: "var(--color-accent-400, #58a6ff)",
  partial: "var(--color-warn, #d29922)",
  cancelled: "var(--color-neutral-600, #5d6b7d)",
  pending: "var(--color-neutral-600, #5d6b7d)",
};

const mono = "var(--mono)";

export function FleetApp() {
  const [state, setState] = React.useState<FleetState | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const [mode, setMode] = React.useState<Mode>("script");
  const [script, setScript] = React.useState("");
  const [args, setArgs] = React.useState("");
  const [command, setCommand] = React.useState("");
  const [dryRun, setDryRun] = React.useState(false);

  const [picked, setPicked] = React.useState<Set<string>>(new Set());
  const [tags, setTags] = React.useState<Set<string>>(new Set());
  const [run, setRun] = React.useState<FleetRun | null>(null);
  const [openHosts, setOpenHosts] = React.useState<Set<string>>(new Set());

  const runRef = React.useRef<FleetRun | null>(null);
  runRef.current = run;

  const load = React.useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const s = await api.fleetState();
      setState(s);
      if (s.scripts.length && !script) setScript(s.scripts[0].name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to load");
    } finally {
      setBusy(false);
    }
  }, [script]);

  React.useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live output. The backend relays the hub's SSE stream unchanged, so tasks
  // update per host as they happen rather than only when the run finishes.
  React.useEffect(() => {
    if (!state?.configured) return;
    const es = new EventSource(fleetStreamUrl());

    const patchTask = (task: FleetTask) =>
      setRun((prev) =>
        prev ? { ...prev, tasks: { ...prev.tasks, [task.serverId]: { ...prev.tasks[task.serverId], ...task } } } : prev
      );

    es.addEventListener("task:start", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      if (runRef.current?.id === d.runId) patchTask(d.task);
    });

    es.addEventListener("task:done", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      if (runRef.current?.id !== d.runId) return;
      patchTask(d.task);
      if (d.task.status === "failed" || d.task.status === "error") {
        setOpenHosts((prev) => new Set(prev).add(d.task.serverId));
      }
    });

    es.addEventListener("task:data", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      if (runRef.current?.id !== d.runId) return;
      setRun((prev) => {
        if (!prev) return prev;
        const t = prev.tasks[d.serverId];
        if (!t) return prev;
        return { ...prev, tasks: { ...prev.tasks, [d.serverId]: { ...t, output: (t.output || "") + d.chunk } } };
      });
    });

    es.addEventListener("run:done", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      if (runRef.current?.id === d.run.id) {
        setRun((prev) => (prev ? { ...prev, status: d.run.status } : prev));
      }
    });

    es.addEventListener("health", () => {
      api.fleetState().then(setState).catch(() => {});
    });

    return () => es.close();
  }, [state?.configured]);

  const targets = React.useMemo(() => {
    const ids = new Set(picked);
    if (tags.size && state) {
      for (const s of state.servers) if (s.tags.some((t) => tags.has(t))) ids.add(s.id);
    }
    return [...ids];
  }, [picked, tags, state]);

  const selectedScript = state?.scripts.find((s) => s.name === script);

  async function start() {
    if (!targets.length) return;
    if (selectedScript?.danger && !dryRun) {
      const ok = window.confirm(
        `"${selectedScript.title}" is marked dangerous and will run on ${targets.length} server(s). Continue?`
      );
      if (!ok) return;
    }
    setBusy(true);
    setErr(null);
    try {
      const started = await api.fleetRun({
        kind: mode,
        script: mode === "script" ? script : null,
        command: mode === "adhoc" ? command : null,
        args,
        ids: targets,
        dryRun,
      });
      setRun(started);
      setOpenHosts(new Set(Object.keys(started.tasks).length <= 4 ? Object.keys(started.tasks) : []));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "run failed");
    } finally {
      setBusy(false);
    }
  }

  if (!state) {
    return (
      <Shell>
        <div style={{ color: "var(--color-neutral-500)", fontFamily: mono, fontSize: 12.5 }}>
          {err ? <span style={{ color: STATUS_COLOR.error }}>{err}</span> : "loading fleet…"}
        </div>
      </Shell>
    );
  }

  if (!state.configured) {
    return (
      <Shell>
        <div style={{ maxWidth: 620, fontSize: 13, lineHeight: 1.7, color: "var(--color-neutral-400)" }}>
          <p style={{ marginTop: 0, color: "var(--color-text)", fontWeight: 600 }}>The fleet hub is not connected.</p>
          <p>
            The hub runs beside this backend on loopback and holds SSH access to every enrolled server. To switch it on,
            copy the token from the hub&apos;s <code style={{ fontFamily: mono }}>.fleet-token</code> file into{" "}
            <code style={{ fontFamily: mono }}>backend/.env</code> as{" "}
            <code style={{ fontFamily: mono }}>FLEET_TOKEN</code>, then restart the backend.
          </p>
        </div>
      </Shell>
    );
  }

  const tasks = run ? Object.values(run.tasks) : [];

  return (
    <Shell>
      <div style={{ display: "flex", gap: 18, height: "100%", overflow: "hidden" }}>
        {/* ── targets ── */}
        <div style={{ width: 240, flex: "none", display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <Label>
            targets
            <button onClick={() => { setPicked(new Set(state.servers.map((s) => s.id))); }} style={linkBtn}>all</button>
            <button onClick={() => { setPicked(new Set()); setTags(new Set()); }} style={linkBtn}>none</button>
          </Label>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 9 }}>
            {state.tags.map(({ tag, count }) => (
              <button
                key={tag}
                onClick={() => {
                  const next = new Set(tags);
                  next.has(tag) ? next.delete(tag) : next.add(tag);
                  setTags(next);
                }}
                style={{
                  ...chip,
                  borderColor: tags.has(tag) ? "var(--color-accent-500, #4c8dff)" : "var(--color-neutral-800, #262d38)",
                  color: tags.has(tag) ? "var(--color-accent-200, #cfe0ff)" : "var(--color-neutral-500)",
                }}
              >
                {tag}<span style={{ opacity: 0.5, marginLeft: 4 }}>{count}</span>
              </button>
            ))}
          </div>

          <div style={{ flex: 1, overflowY: "auto" }}>
            {state.servers.map((s) => {
              const viaTag = s.tags.some((t) => tags.has(t));
              const on = picked.has(s.id) || viaTag;
              return (
                <div
                  key={s.id}
                  onClick={() => {
                    if (viaTag) return;
                    const next = new Set(picked);
                    next.has(s.id) ? next.delete(s.id) : next.add(s.id);
                    setPicked(next);
                  }}
                  style={{
                    display: "flex", alignItems: "center", gap: 8, padding: "5px 7px", borderRadius: 6,
                    cursor: viaTag ? "default" : "pointer", opacity: viaTag ? 0.75 : 1,
                    background: on ? "color-mix(in srgb, var(--color-accent-900, #29456f) 45%, transparent)" : "transparent",
                  }}
                >
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: DOT[s.health?.status] || DOT.unknown, flex: "none" }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 12.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.label}</div>
                    <div style={{ fontFamily: mono, fontSize: 10.5, color: "var(--color-neutral-600)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {s.host}
                    </div>
                  </div>
                </div>
              );
            })}
            {!state.servers.length && (
              <div style={{ fontSize: 12, color: "var(--color-neutral-600)", padding: "10px 4px" }}>
                No servers enrolled yet.
              </div>
            )}
          </div>
        </div>

        {/* ── composer + output ── */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <span style={{ fontFamily: mono, fontSize: 11.5, color: "var(--color-neutral-600)" }}>
              ~/fleet$ run --on {targets.length}
            </span>
            <div className="seg">
              {([["script", "script"], ["adhoc", "command"]] as [Mode, string][]).map(([k, l]) => (
                <label key={k} className={`seg-opt ${mode === k ? "active" : ""}`}>
                  <input type="radio" checked={mode === k} onChange={() => setMode(k)} style={{ display: "none" }} />{l}
                </label>
              ))}
            </div>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              dry run
            </label>
            <div style={{ flex: 1 }} />
            <button onClick={() => api.fleetRecheck().catch(() => {})} style={linkBtn}>re-check</button>
            {state.hubUrl && (
              <a href={state.hubUrl} target="_blank" rel="noreferrer" style={{ ...linkBtn, textDecoration: "none" }}>
                terminal + files ↗
              </a>
            )}
          </div>

          {mode === "script" ? (
            <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
              <select value={script} onChange={(e) => setScript(e.target.value)} style={{ ...input, flex: 1 }}>
                {state.scripts.map((s) => (
                  <option key={s.name} value={s.name}>{s.title}{s.danger ? "  ⚠" : ""}</option>
                ))}
              </select>
              <input
                value={args}
                onChange={(e) => setArgs(e.target.value)}
                placeholder="arguments"
                spellCheck={false}
                style={{ ...input, width: 190 }}
              />
              <button onClick={start} disabled={busy || !targets.length} style={runBtn(busy || !targets.length)}>
                run
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "flex-start" }}>
              <textarea
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="systemctl restart nginx && systemctl is-active nginx"
                spellCheck={false}
                rows={2}
                style={{ ...input, flex: 1, resize: "vertical", lineHeight: 1.55 }}
              />
              <button onClick={start} disabled={busy || !targets.length || !command.trim()} style={runBtn(busy || !targets.length || !command.trim())}>
                run
              </button>
            </div>
          )}

          {mode === "script" && selectedScript && (
            <div style={{ fontSize: 11.5, color: "var(--color-neutral-600)", marginBottom: 10 }}>
              {selectedScript.danger && <span style={{ color: STATUS_COLOR.partial, fontWeight: 600 }}>⚠ dangerous · </span>}
              {selectedScript.description}
              {selectedScript.args && ` · args: ${selectedScript.args}`}
            </div>
          )}

          {err && (
            <div style={{ fontSize: 12, color: STATUS_COLOR.error, marginBottom: 10, fontFamily: mono }}>{err}</div>
          )}

          {/* ── results ── */}
          <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            {!run && (
              <div style={{ fontSize: 12.5, color: "var(--color-neutral-600)", paddingTop: 6 }}>
                Pick targets, choose a script or type a command, then run. Output streams back per host.
              </div>
            )}

            {run && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 9, fontFamily: mono, fontSize: 11.5 }}>
                  <span style={{ color: STATUS_COLOR[run.status], fontWeight: 600 }}>{run.status}</span>
                  <span style={{ color: "var(--color-neutral-600)" }}>{run.title}</span>
                  {run.dryRun && <span style={{ color: STATUS_COLOR.partial }}>dry run</span>}
                  <div style={{ flex: 1 }} />
                  <span style={{ color: "var(--color-neutral-600)" }}>
                    {tasks.filter((t) => t.status === "success").length} ok ·{" "}
                    {tasks.filter((t) => t.status === "failed" || t.status === "error").length} failed · {tasks.length} total
                  </span>
                  {run.status === "running" && (
                    <button onClick={() => api.fleetCancel(run.id).catch(() => {})} style={linkBtn}>cancel</button>
                  )}
                </div>

                {tasks.map((t) => {
                  const open = openHosts.has(t.serverId);
                  return (
                    <div key={t.serverId} style={{ border: "1px solid var(--color-neutral-800, #262d38)", borderLeft: `2px solid ${STATUS_COLOR[t.status]}`, borderRadius: 7, marginBottom: 7, overflow: "hidden" }}>
                      <div
                        onClick={() => {
                          const next = new Set(openHosts);
                          next.has(t.serverId) ? next.delete(t.serverId) : next.add(t.serverId);
                          setOpenHosts(next);
                        }}
                        style={{ display: "flex", alignItems: "center", gap: 9, padding: "7px 11px", cursor: "pointer", fontSize: 12.5 }}
                      >
                        <span style={{ color: "var(--color-neutral-600)", fontSize: 9 }}>{open ? "▾" : "▸"}</span>
                        <span style={{ fontWeight: 600 }}>{t.label}</span>
                        <span style={{ fontFamily: mono, fontSize: 11, color: "var(--color-neutral-600)" }}>{t.host}</span>
                        <div style={{ flex: 1 }} />
                        <span style={{ fontFamily: mono, fontSize: 11, color: STATUS_COLOR[t.status] }}>
                          {t.status === "running"
                            ? "running…"
                            : t.error
                            ? t.error
                            : `exit ${t.exitCode}${t.durationMs != null ? ` · ${(t.durationMs / 1000).toFixed(1)}s` : ""}`}
                        </span>
                      </div>
                      {open && (
                        <pre style={{ margin: 0, padding: "9px 12px", background: "#0a0d12", borderTop: "1px solid var(--color-neutral-800, #262d38)", fontFamily: mono, fontSize: 11.8, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 320, overflowY: "auto", color: "#c6d0dc" }}>
                          {stripAnsi(t.output) || "waiting for output…"}
                        </pre>
                      )}
                    </div>
                  );
                })}
              </>
            )}
          </div>
        </div>
      </div>
    </Shell>
  );
}

/** The hub colours its output; the console renders it as plain text. */
function stripAnsi(s: string): string {
  // eslint-disable-next-line no-control-regex
  return (s || "").replace(/\x1b\[[0-9;]*m/g, "");
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div style={{ height: "100%", overflow: "hidden", padding: "20px 24px" }}>{children}</div>;
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--color-neutral-600)", fontWeight: 600, marginBottom: 8 }}>
      {children}
    </div>
  );
}

const linkBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--color-accent-400, #58a6ff)",
  cursor: "pointer",
  fontSize: 11.5,
  padding: "2px 4px",
};

const chip: React.CSSProperties = {
  fontFamily: mono,
  fontSize: 11,
  padding: "2px 7px",
  borderRadius: 20,
  background: "transparent",
  border: "1px solid",
  cursor: "pointer",
};

const input: React.CSSProperties = {
  background: "#0a0f18",
  border: "1px solid var(--color-neutral-800, #262d38)",
  borderRadius: 7,
  padding: "7px 9px",
  color: "var(--color-text)",
  fontFamily: mono,
  fontSize: 12.5,
  outline: "none",
};

function runBtn(disabled: boolean): React.CSSProperties {
  return {
    background: "var(--color-accent-600, #4c8dff)",
    border: "none",
    color: "#fff",
    fontWeight: 600,
    fontSize: 12.5,
    padding: "7px 18px",
    borderRadius: 7,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.45 : 1,
    alignSelf: "flex-start",
  };
}

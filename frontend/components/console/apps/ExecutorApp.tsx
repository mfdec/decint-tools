"use client";

import * as React from "react";
import { api } from "@/lib/api";
import type { ScriptInfo, ExecutionResult, ScriptArgument, ScriptVersionInfo } from "@/lib/types";

type Line = { id: number; kind: "sys" | "cmd" | "out" | "ok" | "err"; text: string };

const COLORS: Record<Line["kind"], string> = {
  sys: "#7ba5e8", cmd: "#e4e7f5", out: "#9397ab", ok: "#7fce9e", err: "#e8908f",
};

let LID = 0;
const L = (kind: Line["kind"], text: string): Line => ({ id: ++LID, kind, text });

export function ExecutorApp() {
  const [scripts, setScripts] = React.useState<ScriptInfo[]>([]);
  const [selectedScript, setSelectedScript] = React.useState<ScriptInfo | null>(null);
  const [selectedVersion, setSelectedVersion] = React.useState<string>("");
  const [scriptArgs, setScriptArgs] = React.useState<Record<string, string>>({});
  const [executing, setExecuting] = React.useState(false);
  const [result, setResult] = React.useState<ExecutionResult | null>(null);
  const [history, setHistory] = React.useState<Line[]>([
    L("sys", `DECINT-console — Code Executor   ·   tty9   ·   secure session`),
    L("sys", "Select a script from the dropdown, configure arguments, and execute."),
    L("out", ""),
  ]);
  const [loading, setLoading] = React.useState(false);
  const termRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight;
  }, [history]);

  React.useEffect(() => {
    loadScripts();
  }, []);

  async function loadScripts() {
    setLoading(true);
    try {
      const data = await api.executorScripts();
      setScripts(data);
      if (data.length > 0 && !selectedScript) {
        setSelectedScript(data[0]);
      }
    } catch (e) {
      setHistory(h => [...h, L("err", `Failed to load scripts: ${e}`)]);
    } finally {
      setLoading(false);
    }
  }

  async function handleExecute() {
    if (!selectedScript) return;

    setExecuting(true);
    setResult(null);
    setHistory(h => [...h, 
      L("cmd", `executor$ execute ${selectedScript.name}${selectedVersion ? ` v${selectedVersion}` : ""}`),
      L("sys", "Executing...")
    ]);

    try {
      // Convert arguments based on type
      const typedArgs: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(scriptArgs)) {
        const argDef = selectedScript.arguments.find(a => a.name === key);
        if (argDef) {
          if (argDef.type === "integer") {
            typedArgs[key] = parseInt(value, 10);
          } else if (argDef.type === "float") {
            typedArgs[key] = parseFloat(value);
          } else if (argDef.type === "boolean") {
            typedArgs[key] = value.toLowerCase() === "true" || value === "1";
          } else {
            typedArgs[key] = value;
          }
        } else {
          typedArgs[key] = value;
        }
      }

      const res = await api.executorExecute({
        script_id: selectedScript.id,
        version: selectedVersion || undefined,
        arguments: typedArgs,
        timeout: 30,
      });

      setResult(res);
      setHistory(h => [
        ...h,
        L("out", ""),
        L("sys", "─".repeat(60)),
        L("ok", `Execution completed in ${res.duration.toFixed(2)}s`),
        L("sys", `Status: ${res.status} | Exit code: ${res.exit_code}`),
        L("out", ""),
        L("sys", "STDOUT:"),
        ...res.stdout.split("\n").map(line => L("out", line)),
        ...(res.stderr ? [L("out", ""), L("sys", "STDERR:"), ...res.stderr.split("\n").map(line => L("err", line))] : []),
      ]);
    } catch (e) {
      setHistory(h => [...h, L("err", `Execution failed: ${e}`)]);
    } finally {
      setExecuting(false);
    }
  }

  function renderArgumentInput(arg: ScriptArgument) {
    const value = scriptArgs[arg.name] ?? arg.default ?? "";

    if (arg.type === "choice" && arg.choices) {
      return (
        <select
          value={value}
          onChange={(e) => setScriptArgs(a => ({ ...a, [arg.name]: e.target.value }))}
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#0f141f",
            border: "1px solid var(--color-divider)",
            color: "var(--color-text)",
            fontFamily: "var(--mono)",
            fontSize: 12.5,
            borderRadius: 4,
          }}
        >
          {arg.choices.map(choice => (
            <option key={choice} value={choice}>{choice}</option>
          ))}
        </select>
      );
    }

    if (arg.type === "boolean") {
      return (
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={value === "true" || value === "1"}
            onChange={(e) => setScriptArgs(a => ({ ...a, [arg.name]: e.target.checked ? "true" : "false" }))}
            style={{ accentColor: "var(--color-accent)" }}
          />
          <span style={{ color: "var(--color-text)", fontSize: 12.5 }}>{value === "true" || value === "1" ? "Yes" : "No"}</span>
        </label>
      );
    }

    return (
      <input
        type={arg.type === "integer" || arg.type === "float" ? "number" : "text"}
        value={value}
        onChange={(e) => setScriptArgs(a => ({ ...a, [arg.name]: e.target.value }))}
        placeholder={arg.description || arg.name}
        style={{
          width: "100%",
          padding: "8px 10px",
          background: "#0f141f",
          border: "1px solid var(--color-divider)",
          color: "var(--color-text)",
          fontFamily: "var(--mono)",
          fontSize: 12.5,
          borderRadius: 4,
        }}
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: "18px 24px 14px" }}>
      {/* Script Selection */}
      <div style={{ marginBottom: 16, display: "flex", gap: 12, alignItems: "center" }}>
        <div style={{ flex: 1 }}>
          <label style={{ display: "block", color: "#7ba5e8", fontSize: 11, marginBottom: 4, fontFamily: "var(--mono)" }}>
            SCRIPT
          </label>
          <select
            value={selectedScript?.id ?? ""}
            onChange={(e) => {
              const script = scripts.find(s => s.id === Number(e.target.value));
              setSelectedScript(script ?? null);
              setSelectedVersion("");
              setScriptArgs({});
            }}
            disabled={loading}
            style={{
              width: "100%",
              padding: "8px 10px",
              background: "#0f141f",
              border: "1px solid var(--color-divider)",
              color: "var(--color-text)",
              fontFamily: "var(--mono)",
              fontSize: 12.5,
              borderRadius: 4,
            }}
          >
            {scripts.map(script => (
              <option key={script.id} value={script.id}>
                {script.name} — {script.description || "No description"}
              </option>
            ))}
          </select>
        </div>

        {selectedScript && selectedScript.versions.length > 0 && (
          <div style={{ width: 150 }}>
            <label style={{ display: "block", color: "#7ba5e8", fontSize: 11, marginBottom: 4, fontFamily: "var(--mono)" }}>
              VERSION
            </label>
            <select
              value={selectedVersion}
              onChange={(e) => setSelectedVersion(e.target.value)}
              style={{
                width: "100%",
                padding: "8px 10px",
                background: "#0f141f",
                border: "1px solid var(--color-divider)",
                color: "var(--color-text)",
                fontFamily: "var(--mono)",
                fontSize: 12.5,
                borderRadius: 4,
              }}
            >
              <option value="">Latest ({selectedScript.latest_version})</option>
              {selectedScript.versions.map(v => (
                <option key={v.id} value={v.version}>v{v.version}</option>
              ))}
            </select>
          </div>
        )}

        <button
          onClick={handleExecute}
          disabled={!selectedScript || executing}
          style={{
            padding: "8px 20px",
            background: executing ? "#3a3f4f" : "var(--color-accent)",
            color: executing ? "#7a7f8f" : "#fff",
            border: "none",
            borderRadius: 4,
            fontFamily: "var(--mono)",
            fontSize: 12.5,
            cursor: executing ? "not-allowed" : "pointer",
            marginTop: 18,
          }}
        >
          {executing ? "Running..." : "Execute"}
        </button>
      </div>

      {/* Arguments Form */}
      {selectedScript && selectedScript.arguments.length > 0 && (
        <div style={{ 
          marginBottom: 16, 
          padding: 12, 
          background: "#0f141f", 
          borderRadius: 6,
          border: "1px solid var(--color-divider)",
        }}>
          <div style={{ color: "#7ba5e8", fontSize: 11, marginBottom: 10, fontFamily: "var(--mono)" }}>
            ARGUMENTS
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", gap: 12 }}>
            {selectedScript.arguments.map(arg => (
              <div key={arg.name}>
                <label style={{ display: "block", color: "var(--color-text)", fontSize: 11, marginBottom: 4, fontFamily: "var(--mono)" }}>
                  {arg.name} {arg.required && <span style={{ color: "#e8908f" }}>*</span>}
                </label>
                <div style={{ color: "#595d6c", fontSize: 10, marginBottom: 4 }}>{arg.description}</div>
                {renderArgumentInput(arg)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Output Terminal */}
      <div ref={termRef} style={{ 
        flex: 1, 
        overflow: "auto", 
        paddingRight: 8,
        background: "#0a0f18",
        borderRadius: 6,
        padding: 12,
        border: "1px solid var(--color-divider)",
      }}>
        {history.map((line) => (
          <div key={line.id} style={{
            color: COLORS[line.kind], whiteSpace: "pre-wrap", wordBreak: "break-word",
            fontFamily: "var(--mono)", fontSize: 12.5, lineHeight: 1.75, minHeight: "1.05em",
          }}>{line.text}</div>
        ))}
      </div>
    </div>
  );
}

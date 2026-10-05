"use client";

import * as React from "react";
import { api } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import { analyze, isSha1, maskEntry, sha1Hex, type Composition } from "@/lib/password";
import type { PasswordCheckResponse, PasswordResult } from "@/lib/types";
import { Copy, Key } from "@/components/icons";

type Mode = "single" | "batch";
/** What the input holds: passwords (hashed here first) or SHA-1 digests already. */
type Input = "password" | "hash";

/** One checked entry. `entry` is what was typed and never leaves this tab. */
interface Row {
  entry: string;
  hash: string;
  composition?: Composition;
  result?: PasswordResult;
}

const SOURCE_LABEL: Record<string, string> = {
  leakedpassword: "leakedpassword.com",
  hibp_range: "Pwned Passwords (fallback)",
};

const RATING_COLOR: Record<Composition["rating"], string> = {
  "very weak": "var(--color-bad)",
  weak: "var(--color-bad)",
  fair: "var(--color-warn)",
  strong: "var(--color-ok)",
  "very strong": "var(--color-ok)",
};

export function PasswordsApp({
  initialQuery, onConsumed,
}: {
  /** A SHA-1 handed over by the recon shell. The shell never passes passwords. */
  initialQuery?: string;
  onConsumed: () => void;
}) {
  const [mode, setMode] = React.useState<Mode>("single");
  const [input, setInput] = React.useState<Input>("password");
  const [value, setValue] = React.useState("");
  const [text, setText] = React.useState("");
  const [show, setShow] = React.useState(false);
  const [rows, setRows] = React.useState<Row[] | null>(null);
  const [summary, setSummary] = React.useState<PasswordCheckResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState<string | null>(null);

  const doCheck = React.useCallback(async (entries: string[], kind: Input) => {
    setError(null); setPaywall(null);
    // Hashes are normalised before de-duplicating, so "ABC…" and "abc…" are one.
    const cleaned: string[] = [];
    for (let i = 0; i < entries.length; i++) {
      const e = kind === "hash" ? entries[i].trim().toLowerCase() : entries[i];
      if (!e) continue;
      if (kind === "hash" && !isSha1(e)) {
        // Not echoed: if a password was pasted in hash mode, it stays off screen.
        setError(`${entries.length > 1 ? `Line ${i + 1}` : "That"} is not a SHA-1 hash (40 hex characters).`);
        return;
      }
      cleaned.push(e);
    }
    const unique = Array.from(new Set(cleaned));
    if (!unique.length) return;
    setLoading(true);
    try {
      const prepared: Row[] = [];
      for (const e of unique) {
        prepared.push(kind === "hash"
          ? { entry: e, hash: e }
          : { entry: e, hash: await sha1Hex(e), composition: analyze(e) });
      }
      const res = await api.checkPasswords(prepared.map((r) => r.hash));
      const byHash = new Map(res.results.map((r) => [r.hash, r]));
      setRows(prepared.map((r) => ({ ...r, result: byHash.get(r.hash) })));
      setSummary(res);
    } catch (e) {
      setRows(null); setSummary(null);
      if (isQuotaError(e)) { setPaywall(e.message); return; }
      setError(e instanceof Error ? e.message : "check failed");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (initialQuery) {
      setMode("single"); setInput("hash"); setValue(initialQuery);
      doCheck([initialQuery], "hash");
      onConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "single") doCheck([value], input);
    // Each line exactly as typed (spaces are legal in a password); only the
    // carriage return a pasted Windows list carries is dropped.
    else doCheck(text.split("\n").map((l) => l.replace(/\r$/, "")), input);
  }

  function clearAll() {
    setValue(""); setText(""); setRows(null); setSummary(null);
    setError(null); setPaywall(null); setShow(false);
  }

  function copy(label: string, s: string) {
    navigator.clipboard?.writeText(s).then(() => {
      setCopied(label);
      setTimeout(() => setCopied(null), 1400);
    }).catch(() => {});
  }

  const live = mode === "single" && input === "password" && value ? analyze(value) : null;
  const lines = text.split("\n").filter((l) => l.replace(/\r$/, "").length > 0).length;
  const single = mode === "single" && rows?.length === 1 ? rows[0] : null;

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/passwords$ check --{input} {mode === "batch" ? `--batch ${lines}` : ""}
        <span style={{ marginLeft: 10, color: "#7fce9e" }}>hashed in this tab · only the SHA-1 is sent</span>
      </div>

      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16, maxWidth: 860 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div className="seg">
            {(["single", "batch"] as Mode[]).map((m) => (
              <label key={m} className={`seg-opt ${mode === m ? "active" : ""}`}>
                <input type="radio" checked={mode === m} onChange={() => { setMode(m); setRows(null); setSummary(null); }} style={{ display: "none" }} />{m}
              </label>
            ))}
          </div>
          <div className="seg">
            {(["password", "hash"] as Input[]).map((k) => (
              <label key={k} className={`seg-opt ${input === k ? "active" : ""}`}>
                <input type="radio" checked={input === k} onChange={() => setInput(k)} style={{ display: "none" }} />
                {k === "hash" ? "sha-1 hash" : k}
              </label>
            ))}
          </div>
          {input === "password" && (
            <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
              show {mode === "batch" ? "entries" : "password"}
            </label>
          )}
        </div>

        {mode === "single" ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
              <Key size={15} style={{ color: "var(--color-neutral-500)" }} />
              <input
                type={input === "password" && !show ? "password" : "text"}
                value={value}
                // A verdict about the previous value must not sit beside a new one.
                onChange={(e) => { setValue(e.target.value); setRows(null); setSummary(null); }}
                placeholder={input === "password" ? "a password to check…" : "a 40-character SHA-1 hash…"}
                autoFocus
                autoComplete="new-password"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                data-1p-ignore
                data-lpignore="true"
                style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 13 }}
              />
            </div>
            <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={loading || !value}>
              {loading ? "Checking…" : "Check"}
            </button>
            <button type="button" className="btn btn-secondary" style={{ height: 40, padding: "0 14px" }} onClick={clearAll}>Clear</button>
          </div>
        ) : (
          <>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={input === "password" ? "one password per line…" : "one SHA-1 hash per line…"}
              rows={7}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              data-1p-ignore
              data-lpignore="true"
              style={{
                width: "100%", resize: "vertical", padding: "10px 12px", background: "var(--color-surface)",
                border: "1px solid var(--color-divider)", borderRadius: 8, outline: "none", color: "var(--color-text)",
                fontFamily: "var(--mono)", fontSize: 12.5, lineHeight: 1.6,
                // Typed passwords are blurred until "show entries" is ticked —
                // enough for a screen share, not a security boundary.
                ...(input === "password" && !show ? { WebkitTextSecurity: "disc", filter: "blur(3px)" } as React.CSSProperties : {}),
              }}
            />
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button type="submit" className="btn btn-primary" style={{ height: 38, padding: "0 18px" }} disabled={loading || !lines}>
                {loading ? "Checking…" : `Check ${lines || ""}`.trim()}
              </button>
              <button type="button" className="btn btn-secondary" style={{ height: 38, padding: "0 14px" }} onClick={clearAll}>Clear</button>
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)" }}>
                each line checked exactly as typed · duplicates collapse · one search per batch
              </span>
            </div>
          </>
        )}
      </form>

      {live && !single && <CompositionCard c={live} />}

      {error && <div className="tag tag-bad">{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {single && single.result && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-start" }}>
          <VerdictCard row={single} copied={copied} onCopy={copy} />
          {single.composition && <CompositionCard c={single.composition} />}
        </div>
      )}

      {mode === "batch" && rows && summary && (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", margin: "6px 0 14px" }}>
            <span className="tag tag-neutral">{summary.total} checked</span>
            <span className={`tag ${summary.leaked ? "tag-bad" : "tag-neutral"}`}>{summary.leaked} compromised</span>
            <span className="tag tag-ok">{summary.total - summary.leaked - summary.failed} not found</span>
            {summary.failed > 0 && <span className="tag tag-warn">{summary.failed} failed</span>}
            <button
              type="button"
              className="btn btn-ghost"
              style={{ marginLeft: "auto", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 6 }}
              title="sha1, leaked, seen, source — no passwords"
              onClick={() => copy("csv", toCsv(rows))}
            >
              <Copy size={13} /> {copied === "csv" ? "copied" : "copy CSV"}
            </button>
          </div>

          <div style={{ border: "1px solid var(--color-divider)", borderRadius: 10, overflowX: "auto", maxWidth: 980 }}>
            <table className="table">
              <thead>
                <tr><th>#</th><th>Entry</th><th>SHA-1</th><th>Seen</th><th>Verdict</th><th>Strength</th></tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.hash}>
                    <td style={{ color: "#75798c" }}>{i + 1}</td>
                    <td style={{ color: "#e4e7f5", fontFamily: "var(--mono)" }}>
                      {input === "hash" ? "—" : show ? r.entry : maskEntry(r.entry)}
                    </td>
                    <td style={{ fontFamily: "var(--mono)", color: "#b2b6ca" }} title={r.hash}>
                      <span style={{ color: "var(--color-accent-300)" }}>{r.hash.slice(0, 5)}</span>{r.hash.slice(5, 12)}…
                    </td>
                    <td style={{ fontFamily: "var(--mono)", color: r.result?.leaked ? "#e8908f" : "#75798c" }}>
                      {r.result?.ok ? r.result.seen.toLocaleString() : "—"}
                    </td>
                    <td><Verdict result={r.result} /></td>
                    {/* Greyed on a leaked row: "strong" must not read as reassurance there. */}
                    <td style={{ fontSize: 12, color: r.composition && !r.result?.leaked ? RATING_COLOR[r.composition.rating] : "#75798c" }}>
                      {r.composition ? `${r.composition.rating} · ~${r.composition.bits} bits` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {summary && <Attribution text={summary.attribution} />}

      {!rows && !loading && !error && !paywall && !live && (
        <div className="card" style={{ maxWidth: 560, marginTop: 8 }}>
          <div className="card-kicker">Password checker</div>
          <div className="card-title">Has this password been in a breach?</div>
          <p className="card-body">
            Checks a password, a list of them, or their SHA-1 hashes against Have I
            Been Pwned&apos;s Pwned Passwords corpus through the leakedpassword.com
            API, and says how many times each one has turned up in breach data.
          </p>
          <p className="card-body">
            The password is hashed with SHA-1 in this browser tab. Only the hash is
            sent to DECINT, which does not store it, and on to leakedpassword.com. If
            that API is down, Pwned Passwords is asked directly, and it sees only the
            first five characters of the hash.
          </p>
        </div>
      )}
    </div>
  );
}

function Verdict({ result }: { result?: PasswordResult }) {
  if (!result) return <span className="tag tag-warn">no answer</span>;
  if (!result.ok) return <span className="tag tag-warn" title={result.error ?? undefined}>check failed</span>;
  if (result.leaked) return <span className="tag tag-bad">compromised</span>;
  return <span className="tag tag-ok">not found</span>;
}

function VerdictCard({
  row, copied, onCopy,
}: {
  row: Row;
  copied: string | null;
  onCopy: (label: string, s: string) => void;
}) {
  const r = row.result!;
  const border = !r.ok ? "var(--color-warn)" : r.leaked ? "var(--color-bad)" : "var(--color-ok)";
  return (
    <div className="card" style={{ minWidth: 320, maxWidth: 520, flex: "1 1 320px", borderColor: border }}>
      <div className="card-kicker" style={{ color: border }}>
        {!r.ok ? "Check failed" : r.leaked ? "Compromised" : "Not found in breach data"}
      </div>
      {!r.ok ? (
        <>
          <div className="card-title">Neither source could answer.</div>
          <p className="card-body">{r.error} — this is not a clean result. Try again shortly.</p>
        </>
      ) : r.leaked ? (
        <>
          <div className="card-title">
            Seen {r.seen.toLocaleString()} time{r.seen === 1 ? "" : "s"} in breach data.
          </div>
          <p className="card-body">
            It is on the lists attackers try first, so treat it as public: don&apos;t use
            it anywhere, and change it wherever it is in use.
          </p>
        </>
      ) : (
        <>
          <div className="card-title">No breach corpus contains this password.</div>
          <p className="card-body">
            That only means it hasn&apos;t leaked yet, not that it is hard to guess —
            the strength estimate beside this tells you that part.
          </p>
        </>
      )}
      <div style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 12.5, padding: "3px 0" }}>
        <span style={{ width: 60, color: "var(--color-neutral-500)" }}>SHA-1</span>
        <span style={{ fontFamily: "var(--mono)", wordBreak: "break-all", flex: 1 }}>
          <span style={{ color: "var(--color-accent-300)" }}>{row.hash.slice(0, 5)}</span>{row.hash.slice(5)}
        </span>
        <button type="button" className="btn btn-ghost" style={{ fontSize: 11.5, display: "inline-flex", gap: 5, alignItems: "center" }} onClick={() => onCopy("hash", row.hash)}>
          <Copy size={12} /> {copied === "hash" ? "copied" : "copy"}
        </button>
      </div>
      {r.source && (
        <div style={{ display: "flex", gap: 10, fontSize: 12.5, padding: "3px 0" }}>
          <span style={{ width: 60, color: "var(--color-neutral-500)" }}>Source</span>
          <span>{SOURCE_LABEL[r.source] ?? r.source}</span>
        </div>
      )}
    </div>
  );
}

function CompositionCard({ c }: { c: Composition }) {
  const classes = [
    c.lower && "lowercase", c.upper && "uppercase", c.digit && "digits", c.symbol && "symbols", c.other && "other",
  ].filter(Boolean) as string[];
  return (
    <div className="card" style={{ minWidth: 280, maxWidth: 400, flex: "1 1 280px", marginBottom: 14 }}>
      <div className="card-kicker">Strength estimate · local</div>
      <div className="card-title" style={{ color: RATING_COLOR[c.rating] }}>
        {c.rating} <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--color-neutral-500)" }}>~{c.bits} bits</span>
      </div>
      <div style={{ display: "flex", gap: 10, fontSize: 12.5, padding: "3px 0" }}>
        <span style={{ width: 60, color: "var(--color-neutral-500)" }}>Length</span>
        <span style={{ fontFamily: "var(--mono)" }}>{c.length}</span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "4px 0" }}>
        {classes.map((k) => <span key={k} className="tag tag-accent" style={{ fontSize: 10 }}>{k}</span>)}
      </div>
      {c.notes.length > 0 && (
        <ul style={{ margin: "4px 0 0", paddingLeft: 16, fontSize: 12, color: "var(--color-neutral-400)" }}>
          {c.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      <p style={{ fontSize: 11, color: "var(--color-neutral-600)", margin: "8px 0 0", lineHeight: 1.5 }}>
        A character-pool estimate, worked out in this tab. A password found in
        breach data is weak whatever this says.
      </p>
    </div>
  );
}

function Attribution({ text }: { text: string }) {
  return (
    <div style={{ marginTop: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)" }}>
      data: {text}
    </div>
  );
}

function toCsv(rows: Row[]): string {
  const lines = ["sha1,leaked,seen,source"];
  for (const r of rows) {
    const res = r.result;
    lines.push([r.hash, res?.ok ? String(res.leaked) : "", res?.ok ? String(res.seen) : "", res?.source ?? "failed"].join(","));
  }
  return lines.join("\n");
}

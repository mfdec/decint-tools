"use client";

/**
 * Leak datasets — files the admin adds to the leak database search.
 *
 * Anything uploaded here is searched by the Leak tool alongside the public
 * sources, for every signed-in user, until it is paused or removed. The list is
 * kept on the server, so it is all still here whenever the admin comes back.
 *
 * Uploads go in 4 MB pieces: the reverse proxies cap one request at 10 MB and
 * these files are bigger. A failed or cancelled upload removes its own
 * half-sent session so nothing is left behind.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { LeakDataset, LeakDatasetLimits, LeakDatasetPreviewRow } from "@/lib/types";
import { Pill, badgeTone, btnDanger, btnPrimary, btnSmall, card, fmtBytes, ink2, input, muted, when } from "./ui";

type Flash = (kind: "ok" | "err", text: string) => void;

const BUSY = new Set(["uploading", "processing", "removing"]);
const ACCEPT = ".txt,.csv,.json";

export function LeakDatasets({ flash, onCount }: { flash: Flash; onCount?: (n: number) => void }) {
  const [items, setItems] = useState<LeakDataset[] | null>(null);
  const [limits, setLimits] = useState<LeakDatasetLimits | null>(null);
  const [loadErr, setLoadErr] = useState("");

  const load = useCallback(() => {
    api.adminLeakDatasets()
      .then((r) => { setItems(r.datasets); setLimits(r.limits); setLoadErr(""); onCount?.(r.datasets.length); })
      .catch((e) => setLoadErr(e instanceof ApiError ? e.message : "Couldn't load the datasets."));
  }, [onCount]);

  useEffect(load, [load]);

  // While anything is uploading, indexing or being erased, watch it progress.
  const busy = !!items?.some((d) => BUSY.has(d.status));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [busy, load]);

  const searchable = items?.filter((d) => d.status === "ready" && d.enabled) ?? [];
  const records = searchable.reduce((n, d) => n + d.records, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ ...card, padding: "14px 16px" }}>
        <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>Leak datasets</h2>
        <p style={{ margin: 0, fontSize: 13, color: ink2, lineHeight: 1.55, maxWidth: 780 }}>
          Files added here are searched by the Leak database tool next to the public sources, for every
          signed-in user. Passwords stay masked in results unless the searcher asks to reveal them.
          <b style={{ color: "var(--color-text)", fontWeight: 600 }}> Pause</b> keeps a dataset but stops searching it;
          <b style={{ color: "var(--color-text)", fontWeight: 600 }}> Remove</b> erases its records.
          Only add data you are entitled to hold and search.
        </p>
        {items && (
          <p style={{ margin: "8px 0 0", fontSize: 12, color: muted }}>
            {searchable.length} of {items.length} dataset{items.length === 1 ? "" : "s"} being searched · {records.toLocaleString()} records
          </p>
        )}
      </div>

      <Uploader limits={limits} flash={flash} onDone={load} />

      {loadErr && <div style={{ ...card, padding: 14, color: "var(--color-bad)" }}>{loadErr} <button style={btnSmall} onClick={load}>Retry</button></div>}

      {items === null && !loadErr && <p style={{ color: muted, fontSize: 13 }}>Loading…</p>}
      {items && items.length === 0 && (
        <div style={{ ...card, padding: 28, textAlign: "center", color: muted, fontSize: 14 }}>
          No datasets yet. Add a .txt, .csv or .json file above and it will be searchable within moments.
        </div>
      )}
      {items?.map((d) => <DatasetCard key={d.id} d={d} flash={flash} onChanged={load} />)}
    </div>
  );
}

// ── upload ──

function Uploader({ limits, flash, onDone }: { limits: LeakDatasetLimits | null; flash: Flash; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [problem, setProblem] = useState("");
  const [drag, setDrag] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  const choose = (f: File | null | undefined) => {
    setProblem("");
    if (!f) return;
    const ext = f.name.includes(".") ? f.name.split(".").pop()!.toLowerCase() : "";
    if (limits && !limits.formats.includes(ext)) { setProblem("Only .txt, .csv and .json files can be added."); return; }
    if (limits && f.size > limits.max_bytes) { setProblem(`That file is ${fmtBytes(f.size)}; the limit is ${fmtBytes(limits.max_bytes)}.`); return; }
    if (f.size === 0) { setProblem("That file is empty."); return; }
    setFile(f);
    if (!name) setName(f.name.replace(/\.[^.]+$/, ""));
  };

  const reset = () => { setFile(null); setName(""); setDesc(""); setProgress(null); if (picker.current) picker.current.value = ""; };

  async function upload() {
    if (!file || !limits) return;
    const ctl = new AbortController();
    abort.current = ctl;
    setProgress(0); setProblem("");
    let id: number | null = null;
    try {
      const ds = await api.adminLeakStart({ filename: file.name, size: file.size, name: name.trim(), description: desc.trim() });
      id = ds.id;
      let offset = 0;
      while (offset < file.size) {
        const end = Math.min(offset + limits.chunk_bytes, file.size);
        offset = await sendChunk(ds.id, offset, file.slice(offset, end), ctl.signal);
        setProgress(offset / file.size);
      }
      await api.adminLeakFinish(ds.id);
      flash("ok", "Uploaded — indexing now");
      reset(); onDone();
    } catch (e) {
      // Don't leave a half-sent upload sitting in the list.
      if (id !== null) await api.adminLeakRemove(id).catch(() => {});
      if (ctl.signal.aborted) flash("ok", "Upload cancelled");
      else setProblem(e instanceof ApiError ? e.message : "The upload failed. Check your connection and try again.");
      setProgress(null); onDone();
    } finally { abort.current = null; }
  }

  // One piece, retried on a dropped connection. If the server got the piece but
  // the reply was lost, ask it where it is rather than sending a duplicate.
  async function sendChunk(id: number, offset: number, piece: Blob, signal: AbortSignal): Promise<number> {
    for (let attempt = 0; ; attempt++) {
      try {
        const r = await api.adminLeakChunk(id, offset, piece, signal);
        return r.received;
      } catch (e) {
        if (signal.aborted) throw e;
        const transient = !(e instanceof ApiError) || e.status >= 500 || e.status === 409;
        if (!transient || attempt >= 3) throw e;
        await new Promise((r) => setTimeout(r, 600 * (attempt + 1)));
        const now = (await api.adminLeakDatasets().catch(() => null))?.datasets.find((d) => d.id === id);
        if (now && now.received_bytes >= offset + piece.size) return now.received_bytes;
        if (now && now.received_bytes !== offset) throw new ApiError("Upload got out of step — please try again.", 409);
      }
    }
  }

  const uploading = progress !== null;
  return (
    <div style={{ ...card, padding: 16 }}>
      <input ref={picker} type="file" accept={ACCEPT} hidden onChange={(e) => choose(e.target.files?.[0])} />
      {!file ? (
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); choose(e.dataTransfer.files?.[0]); }}
          style={{
            border: `1px ${drag ? "solid" : "dashed"} ${drag ? "var(--color-accent)" : "var(--color-neutral-700)"}`,
            borderRadius: "var(--radius-md)", padding: "22px 16px", textAlign: "center",
            background: drag ? "var(--color-surface-2)" : "transparent",
          }}>
          <button style={btnPrimary} onClick={() => picker.current?.click()}>Choose a file…</button>
          <p style={{ margin: "10px 0 0", fontSize: 12, color: muted }}>
            or drop it here · .txt, .csv or .json{limits ? ` · up to ${fmtBytes(limits.max_bytes)}` : ""}
          </p>
          <p style={{ margin: "6px auto 0", fontSize: 12, color: muted, maxWidth: 560, lineHeight: 1.5 }}>
            txt: one <code>email:password</code> or bare email/username/domain per line · csv: a header such as{" "}
            <code>email,password</code> · json: an array of records or one per line · add{" "}
            <code>first_name</code>/<code>last_name</code> (or <code>full_name</code>) columns to make people findable by name
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
            <span style={{ fontSize: 14 }}><b>{file.name}</b> <span style={{ color: muted }}>· {fmtBytes(file.size)}</span></span>
            {!uploading && <button style={btnSmall} onClick={reset}>Change file</button>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
            <label style={{ fontSize: 12, color: ink2 }}>Name
              <input style={{ ...input, marginTop: 4 }} value={name} maxLength={120} disabled={uploading}
                onChange={(e) => setName(e.target.value)} placeholder="Shown in search results" />
            </label>
            <label style={{ fontSize: 12, color: ink2 }}>Description <span style={{ color: muted }}>(optional)</span>
              <input style={{ ...input, marginTop: 4 }} value={desc} maxLength={500} disabled={uploading}
                onChange={(e) => setDesc(e.target.value)} placeholder="Where it came from, what it covers" />
            </label>
          </div>
          {uploading ? (
            <div>
              <Meter value={progress ?? 0} />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontSize: 12, color: ink2 }}>
                <span>Uploading… {Math.round((progress ?? 0) * 100)}%</span>
                <button style={btnSmall} onClick={() => abort.current?.abort()}>Cancel</button>
              </div>
            </div>
          ) : (
            <div><button style={btnPrimary} onClick={upload}>Upload and index</button></div>
          )}
        </div>
      )}
      {problem && <p role="alert" style={{ margin: "10px 0 0", fontSize: 13, color: "var(--color-bad)" }}>{problem}</p>}
    </div>
  );
}

function Meter({ value }: { value: number }) {
  return (
    <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}
      style={{ height: 6, borderRadius: 3, background: "var(--color-accent-900)", overflow: "hidden" }}>
      <div style={{ height: "100%", width: `${Math.max(value * 100, 1)}%`, background: "var(--color-accent)", transition: "width .2s" }} />
    </div>
  );
}

// ── one dataset ──

function DatasetCard({ d, flash, onChanged }: { d: LeakDataset; flash: Flash; onChanged: () => void }) {
  const [rows, setRows] = useState<LeakDatasetPreviewRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const err = (e: unknown) => flash("err", e instanceof ApiError ? e.message : "Something went wrong");

  async function toggle() {
    setBusy(true);
    try {
      await api.adminLeakUpdate(d.id, { enabled: !d.enabled });
      flash("ok", d.enabled ? "Paused — no longer searched" : "Resumed — searched again");
      onChanged();
    } catch (e) { err(e); } finally { setBusy(false); }
  }

  async function remove() {
    if (!confirm(`Remove “${d.name}”?\n\nIts ${d.records.toLocaleString()} records are erased and it stops appearing in searches. This cannot be undone.`)) return;
    setBusy(true);
    try { await api.adminLeakRemove(d.id); flash("ok", "Removed"); onChanged(); }
    catch (e) { err(e); } finally { setBusy(false); }
  }

  async function preview() {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (rows === null) api.adminLeakPreview(d.id).then((r) => setRows(r.rows)).catch((e) => { err(e); setOpen(false); });
  }

  const ready = d.status === "ready";
  const live = ready && d.enabled;
  const stateText = ready ? (d.enabled ? "searchable" : "paused") : d.status;
  const tone = ready ? (d.enabled ? "ok" : "warn") : badgeTone(d.status);
  const extra = Math.max(d.fields.length - 10, 0);

  return (
    <article style={{ ...card, padding: "14px 16px", opacity: d.status === "removing" ? 0.55 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 320px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, overflowWrap: "anywhere" }}>{d.name}</h3>
            <Pill text={stateText} tone={tone} />
            <Pill text={d.format} tone="muted" />
          </div>
          <div style={{ fontSize: 12, color: muted, marginTop: 4, overflowWrap: "anywhere" }}>
            {d.filename} · {fmtBytes(d.size_bytes)} · added {when(d.created_at)}{d.uploaded_by ? ` by ${d.uploaded_by}` : ""}
          </div>
          {d.description && <p style={{ margin: "8px 0 0", fontSize: 13, color: ink2 }}>{d.description}</p>}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {ready && <button style={btnSmall} onClick={preview} aria-expanded={open}>{open ? "Hide sample" : "Sample"}</button>}
          {ready && <button style={btnSmall} disabled={busy} onClick={toggle}>{d.enabled ? "Pause" : "Resume"}</button>}
          <button style={{ ...btnSmall, ...btnDanger, fontSize: 12, padding: "4px 9px" }}
            disabled={busy || d.status === "removing" || d.status === "processing"}
            title={d.status === "processing" ? "Wait for indexing to finish" : undefined}
            onClick={remove}>{d.status === "uploading" ? "Cancel" : "Remove"}</button>
        </div>
      </div>

      {d.status === "uploading" && (
        <div style={{ marginTop: 10 }}>
          <Meter value={d.size_bytes ? d.received_bytes / d.size_bytes : 0} />
          <p style={{ margin: "4px 0 0", fontSize: 12, color: muted }}>
            Receiving… {fmtBytes(d.received_bytes)} of {fmtBytes(d.size_bytes)}. If the browser that started this was closed, cancel it and upload again.
          </p>
        </div>
      )}
      {d.status === "processing" && (
        <p style={{ margin: "10px 0 0", fontSize: 13, color: ink2 }}>
          Indexing… {d.records.toLocaleString()} records so far. This page updates by itself.
        </p>
      )}
      {d.status === "failed" && d.error && (
        <p role="alert" style={{ margin: "10px 0 0", fontSize: 13, color: "var(--color-bad)" }}>{d.error}</p>
      )}

      {ready && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 22px", marginTop: 10, fontSize: 13 }}>
          <Stat k="Records" v={d.records.toLocaleString()} />
          {d.skipped > 0 && <Stat k="Skipped lines" v={d.skipped.toLocaleString()} />}
          <Stat k="In search" v={live ? "yes" : "no"} />
        </div>
      )}
      {ready && d.fields.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8, alignItems: "center" }}>
          <span style={{ fontSize: 12, color: muted }}>Fields</span>
          {d.fields.slice(0, 10).map((f) => (
            <span key={f} style={{ fontSize: 11, fontFamily: "var(--mono)", color: ink2, border: "1px solid var(--color-divider)", borderRadius: "var(--radius-sm)", padding: "0 6px" }}>{f}</span>
          ))}
          {extra > 0 && <span style={{ fontSize: 11, color: muted }}>+{extra} more</span>}
        </div>
      )}

      {open && (
        <div style={{ marginTop: 12, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", color: ink2 }}>
                {["Email", "Username", "Domain", "Secret (masked)"].map((h) => (
                  <th key={h} style={{ padding: "5px 10px", fontWeight: 500, borderBottom: "1px solid var(--color-divider)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(rows ?? []).map((r, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--color-divider)", fontFamily: "var(--mono)" }}>
                  <td style={{ padding: "5px 10px" }}>{r.email ?? "—"}</td>
                  <td style={{ padding: "5px 10px" }}>{r.username ?? "—"}</td>
                  <td style={{ padding: "5px 10px" }}>{r.domain ?? "—"}</td>
                  <td style={{ padding: "5px 10px" }}>{r.secret ? `${r.secret}${r.secret_kind === "hash" ? "  (hash)" : ""}` : "—"}</td>
                </tr>
              ))}
              {rows === null && <tr><td colSpan={4} style={{ padding: 10, color: muted }}>Loading…</td></tr>}
              {rows !== null && rows.length === 0 && <tr><td colSpan={4} style={{ padding: 10, color: muted }}>No rows.</td></tr>}
            </tbody>
          </table>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: muted }}>
            The first few records, so you can check the file was read the way you meant. Secrets stay masked here.
          </p>
        </div>
      )}
    </article>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return <span><span style={{ color: muted }}>{k} </span><b style={{ fontWeight: 600 }}>{v}</b></span>;
}

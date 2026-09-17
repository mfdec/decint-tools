"use client";

import * as React from "react";
import { api, packetsWsUrl } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

interface Pkt {
  n: number; time: string; src: string; dst: string; proto: string;
  len: number; flag: string; info?: string; error?: string;
}

const PROTO_COLOR: Record<string, string> = {
  TCP: "#9bbef1", UDP: "#b2b6ca", TLS: "#7fce9e", HTTP: "#8fb2e6",
  DNS: "#8fb2e6", ICMP: "#e0b57f", ARP: "#e0b57f",
};

export function PacketsApp({ health }: { health: HealthResponse | null }) {
  const [pkts, setPkts] = React.useState<Pkt[]>([]);
  const [running, setRunning] = React.useState(false);
  const [iface, setIface] = React.useState<string>("");
  const [ifaces, setIfaces] = React.useState<string[]>([]);
  const [status, setStatus] = React.useState<string>("idle");
  const [rate, setRate] = React.useState(0);
  const wsRef = React.useRef<WebSocket | null>(null);
  const countRef = React.useRef({ last: 0, at: Date.now() });

  React.useEffect(() => {
    if (!health?.sniffer_enabled) return;
    api.packetInterfaces().then((r) => { setIfaces(r.interfaces); setIface(r.default || r.interfaces[0] || ""); }).catch(() => {});
  }, [health]);

  React.useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      const total = pkts.length ? pkts[0].n : 0;
      const dt = (now - countRef.current.at) / 1000;
      if (dt >= 1) {
        setRate(Math.max(0, Math.round((total - countRef.current.last) / dt)));
        countRef.current = { last: total, at: now };
      }
    }, 1000);
    return () => clearInterval(t);
  }, [pkts]);

  function start() {
    if (wsRef.current) return;
    setPkts([]); setStatus("connecting…");
    const ws = new WebSocket(packetsWsUrl(iface || undefined));
    wsRef.current = ws;
    ws.onopen = () => { setRunning(true); setStatus("capturing"); };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.event === "started") { setStatus(`capturing · ${msg.iface}`); return; }
      if (msg.error) { setStatus("error: " + msg.error); return; }
      const p = msg as Pkt;
      setPkts((prev) => [p, ...prev].slice(0, 200));
    };
    ws.onclose = () => { setRunning(false); wsRef.current = null; setStatus((s) => s.startsWith("error") ? s : "stopped"); };
    ws.onerror = () => setStatus("error: websocket failed");
  }

  function stop() {
    wsRef.current?.close();
    wsRef.current = null;
    setRunning(false);
  }

  React.useEffect(() => () => { wsRef.current?.close(); }, []);

  if (!health?.sniffer_enabled) {
    return (
      <div style={{ padding: 24 }}>
        <div className="card" style={{ maxWidth: 520 }}>
          <div className="card-kicker">Packets</div>
          <div className="card-title">Capture is disabled on this host</div>
          <p className="card-body">
            The packet sniffer is an admin/local tool — it captures this machine&apos;s
            own interface and needs root. It&apos;s enabled only on the operator&apos;s box
            (SNIFFER_ENABLED), and hidden on the public server.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", padding: "20px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c" }}>~/packets$ capture --iface {iface || "auto"}</span>
        <select value={iface} onChange={(e) => setIface(e.target.value)} disabled={running} className="input" style={{ width: "auto", minHeight: 30, padding: "2px 8px", fontFamily: "var(--mono)", fontSize: 12 }}>
          {ifaces.length === 0 && <option value="">auto</option>}
          {ifaces.map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
        {!running
          ? <button className="btn btn-primary" style={{ height: 30 }} onClick={start}>Start</button>
          : <button className="btn btn-secondary" style={{ height: 30 }} onClick={stop}>Stop</button>}
        <span style={{ flex: 1 }} />
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 11 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: running ? "#e8908f" : "#595d6c", boxShadow: running ? "0 0 8px #e8908f" : "none", animation: running ? "dc-pulse 1.4s infinite" : "none" }} />
          <span style={{ color: "var(--color-neutral-400)" }}>{running ? "REC" : "idle"}</span>
          <span style={{ color: "var(--color-neutral-600)", marginLeft: 8 }}>{rate} pkt/s</span>
        </span>
      </div>

      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)", marginBottom: 8 }}>{status}</div>

      <div style={{ flex: 1, overflow: "auto", border: "1px solid var(--color-divider)", borderRadius: 10 }}>
        <table className="table">
          <thead style={{ position: "sticky", top: 0, background: "color-mix(in srgb, black 24%, var(--color-surface))" }}>
            <tr><th>Time</th><th>Source</th><th>Dest</th><th>Proto</th><th>Len</th><th>Flag</th><th>Info</th></tr>
          </thead>
          <tbody>
            {pkts.map((p) => (
              <tr key={p.n}>
                <td style={{ color: "#75798c" }}>{p.time}</td>
                <td style={{ color: "#e4e7f5" }}>{p.src}</td>
                <td style={{ color: "#e4e7f5" }}>{p.dst}</td>
                <td><span style={{ color: PROTO_COLOR[p.proto] || "#b2b6ca", border: `1px solid ${PROTO_COLOR[p.proto] || "#b2b6ca"}`, borderRadius: 4, padding: "1px 6px", fontSize: 10.5 }}>{p.proto}</span></td>
                <td style={{ color: "#9397ab" }}>{p.len}</td>
                <td style={{ color: "#b2b6ca" }}>{p.flag}</td>
                <td style={{ color: "#8fb2e6", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.info}</td>
              </tr>
            ))}
            {pkts.length === 0 && <tr><td colSpan={7} style={{ color: "var(--color-neutral-500)", fontFamily: "var(--font-body)" }}>{running ? "waiting for packets…" : "press Start to capture this host's traffic."}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

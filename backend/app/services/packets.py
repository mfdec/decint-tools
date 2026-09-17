"""Packet capture service — admin-only, local box.

Reuses scapy (the same engine decint_sniffer.py uses) to capture the HOST's
own interface and stream decoded packet summaries. This only makes sense on
the operator's own machine: it needs root / CAP_NET_RAW and it sees the
server's traffic, not a visitor's. It is gated by SNIFFER_ENABLED and the admin
session, and hard-disabled on the public server.

scapy is imported lazily so the rest of the backend runs without it present or
without root.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any


class CaptureError(RuntimeError):
    pass


def list_interfaces() -> list[str]:
    try:
        from scapy.all import get_if_list  # type: ignore
    except Exception as e:  # pragma: no cover
        raise CaptureError(f"scapy unavailable: {e}") from e
    return [i for i in get_if_list() if i != "lo"] or get_if_list()


_PROTO_FLAGS = {"F": "FIN", "S": "SYN", "R": "RST", "P": "PSH",
                "A": "ACK", "U": "URG", "E": "ECE", "C": "CWR"}


def _decode(pkt) -> dict[str, Any]:
    """Turn a scapy packet into a compact, JSON-safe summary."""
    from scapy.all import ARP, DNS, DNSQR, ICMP, IP, IPv6, TCP, UDP  # type: ignore

    now = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    out: dict[str, Any] = {
        "time": now, "src": "", "dst": "", "proto": "?",
        "len": len(pkt), "flag": "—", "info": "",
    }

    if pkt.haslayer(ARP):
        a = pkt[ARP]
        out.update(proto="ARP", src=a.psrc, dst=a.pdst,
                   info="who-has" if a.op == 1 else "is-at")
        return out

    if pkt.haslayer(IP):
        ip = pkt[IP]
        out["src"], out["dst"] = ip.src, ip.dst
    elif pkt.haslayer(IPv6):
        ip6 = pkt[IPv6]
        out["src"], out["dst"] = ip6.src, ip6.dst

    if pkt.haslayer(TCP):
        tcp = pkt[TCP]
        out["proto"] = "TCP"
        flags = "".join(_PROTO_FLAGS.get(f, "") for f in str(tcp.flags))
        out["flag"] = flags or str(tcp.flags) or "—"
        dport, sport = int(tcp.dport), int(tcp.sport)
        if 443 in (dport, sport):
            out["proto"] = "TLS"
        elif 80 in (dport, sport):
            out["proto"] = "HTTP"
        out["info"] = f":{sport} → :{dport}"
    elif pkt.haslayer(UDP):
        udp = pkt[UDP]
        out["proto"] = "UDP"
        out["info"] = f":{int(udp.sport)} → :{int(udp.dport)}"

    if pkt.haslayer(DNS):
        out["proto"] = "DNS"
        if pkt.haslayer(DNSQR):
            try:
                out["info"] = pkt[DNSQR].qname.decode("utf-8", "ignore").rstrip(".")
            except Exception:
                pass
    elif pkt.haslayer(ICMP):
        out["proto"] = "ICMP"

    return out


class CaptureSession:
    """One live capture, feeding an asyncio.Queue from a scapy sniff thread."""

    def __init__(self, iface: str | None, bpf: str, loop: asyncio.AbstractEventLoop):
        self.iface = iface or None
        self.bpf = bpf
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.count = 0

    def _on_packet(self, pkt) -> None:
        try:
            summary = _decode(pkt)
        except Exception:
            return
        self.count += 1
        summary["n"] = self.count
        # hand the item back to the event loop thread-safely
        self.loop.call_soon_threadsafe(self._offer, summary)

    def _offer(self, item: dict[str, Any]) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(item)

    def _run(self) -> None:
        from scapy.all import sniff  # type: ignore

        try:
            sniff(
                iface=self.iface,
                filter=self.bpf or None,
                prn=self._on_packet,
                store=False,
                stop_filter=lambda _p: self._stop.is_set(),
            )
        except PermissionError:
            self.loop.call_soon_threadsafe(
                self._offer,
                {"error": "permission denied — capture needs root / CAP_NET_RAW"},
            )
        except Exception as e:  # pragma: no cover
            self.loop.call_soon_threadsafe(
                self._offer, {"error": f"{type(e).__name__}: {e}"}
            )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

"""Dark-web search service.

Two engines, both vendored under backend/tools/:

  * ahmia — darknet_rerank.smart_search(): queries ahmia.fi over CLEARNET and
    BM25-reranks the results. Fast, no Tor, actually returns data. Default.
  * tor   — decint_darkweb_search.run_search(): concurrent multi-source query
    over Tor with health tracking + evidence manifest. Slower and dependent on
    live .onion mirrors (seed addresses rotate), but real dark-web coverage.

Both engines are blocking, so callers run them via asyncio.to_thread.
"""

from __future__ import annotations

import socket
import time
from typing import Any

from ..config import settings
from ..models import DarkwebResult

# The vendored tools import cleanly (no import-time side effects).
from tools import darknet_rerank as rr  # noqa: E402
from tools import decint_darkweb_search as dws  # noqa: E402


# Cache the Tor status so /health doesn't re-probe on every call.
_tor_cache: dict[str, Any] = {"at": 0.0, "ok": False, "detail": "not checked"}


def tor_status() -> tuple[bool, str]:
    """Fast, cached reachability check on the Tor SOCKS port.

    A full through-Tor verification (check.torproject.org) is slow (seconds)
    and races on the first connection, which made the status dot flicker. For a
    health indicator, confirming the SOCKS port is open is the right
    granularity; the actual dark-web search still routes real traffic through
    Tor and surfaces per-source failures itself.
    """
    now = time.time()
    if now - _tor_cache["at"] < 20:
        return _tor_cache["ok"], _tor_cache["detail"]
    try:
        with socket.create_connection((settings.tor_host, settings.tor_port), timeout=2.0):
            ok, detail = True, f"SOCKS {settings.tor_host}:{settings.tor_port} reachable"
    except OSError:
        ok, detail = False, (
            f"SOCKS {settings.tor_host}:{settings.tor_port} unreachable — "
            "start Tor: sudo systemctl start tor"
        )
    _tor_cache.update(at=now, ok=ok, detail=detail)
    return ok, detail


def tor_verify() -> tuple[bool, str]:
    """Full through-Tor confirmation (slow) for on-demand checks."""
    return dws.check_tor(settings.tor_host, settings.tor_port, timeout=15.0)


# ─────────────────────────── ahmia (clearnet) ───────────────────────────


def run_ahmia(query: str, limit: int = 25) -> tuple[list[DarkwebResult], dict[str, Any]]:
    started = time.time()
    hits = rr.smart_search(query, verify=False, limit=limit)
    results = [
        DarkwebResult(
            title=h.title or "(untitled)",
            url=h.url,
            snippet=h.best_snippet or h.snippet,
            score=round(float(h.score), 2),
            coverage=round(float(h.coverage), 2),
            live=h.live,
        )
        for h in hits
    ]
    manifest = {
        "engine": "ahmia (clearnet, BM25 rerank)",
        "keyword": query,
        "result_count": len(results),
        "elapsed_seconds": round(time.time() - started, 2),
        "note": "Ranked over ahmia.fi's index; no Tor required.",
    }
    return results, manifest


# ─────────────────────────── tor (multi-source) ───────────────────────────


def run_tor(
    query: str, verify: bool = False, limit: int = 25
) -> tuple[list[DarkwebResult], dict[str, Any]]:
    started = dws.utc_now()
    t0 = time.time()

    sources = dws.load_sources(None, create=True)
    health = dws.HealthStore()
    cfg = dws.RunConfig(
        keyword=query,
        tor_host=settings.tor_host,
        tor_port=settings.tor_port,
        onion_only=True,
    )
    raw, per_source = dws.run_search(sources, cfg, health)
    merged = dws.merge_results(raw)[:limit]
    manifest = dws.build_manifest(cfg, merged, per_source, started, time.time() - t0)
    manifest["engine"] = "tor (multi-source)"

    results = [
        DarkwebResult(
            title=r.get("title") or "(untitled)",
            url=r.get("url", ""),
            snippet=r.get("snippet", ""),
            score=round(float(r.get("score", 0.0)), 2),
            corroboration=r.get("corroboration"),
            sources=r.get("sources", []),
            entities=r.get("entities", {}) or {},
        )
        for r in merged
    ]
    return results, manifest

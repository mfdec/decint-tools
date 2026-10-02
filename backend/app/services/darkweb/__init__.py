"""Dark-web search service: a meta-search over onion search engines.

Based on the onionscope aggregator (github.com/mfdec/data-acquisition). A query fans out to
many engines in parallel, and the merged hits are cleaned, de-duplicated, scored and pruned
into one result list. Only search-engine result pages are ever fetched; the sites the results
point at are never visited.

Two modes, each backed by its own aggregator (transport, engine health and caches differ):

  * gateway — the engines that publish a clearnet gateway (Ahmia, OnionLand, VormWeb, ...).
    A few plain HTTPS requests, no Tor, seconds. Default, and what the free tiers get.
  * tor     — every default-tier onion engine, one isolated Tor circuit each, with entity
    extraction and an evidence hash on the results. Slower, and real dark-web coverage.

Routers talk to this module only: `search_events()` to run a search, `tor_status()` for the
health dot, `engine_roster()` for the engines list.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from ...config import settings as app_settings
from .aggregator import BLOCKED_QUERY_MESSAGE, NOT_SEARCHABLE_MESSAGE, Aggregator
from .config import Settings, Transport
from .extras import attach_entities, evidence_hash
from .models import PrunedItem, SearchResponse
from .pipeline.safety import is_blocked_query
from .query import QuerySpec, parse_query
from .tor import tor_check

# httpx logs every request URL at INFO, and the engine URLs carry the search text. What someone
# searches for is never persisted (see services/usage.py), and logs persist, so keep these quiet
# whatever level the host application later picks for the root logger.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

__all__ = [
    "BLOCKED_QUERY_MESSAGE", "NOT_SEARCHABLE_MESSAGE", "VERSION", "Mode", "QuerySpec",
    "aclose", "engine_roster", "parse_query", "preflight", "search_events", "search_slot",
    "slots_busy", "tor_status", "tor_verify", "upstream_failed",
]

VERSION = "3.0.0"
TOOL = "DECINT Dark Web Search"

Mode = Literal["gateway", "tor"]
_TRANSPORT: dict[str, Transport] = {"gateway": "direct", "tor": "tor"}

_aggregators: dict[str, Aggregator] = {}
_gate = asyncio.Semaphore(max(1, app_settings.darkweb_max_concurrent_searches))


# ─────────────────────────── aggregators ───────────────────────────


def settings_for(mode: Mode) -> Settings:
    return Settings.from_app(app_settings, _TRANSPORT[mode])


def aggregator_for(mode: Mode) -> Aggregator:
    """One long-lived aggregator per mode: it owns the connection pools and engine health."""
    agg = _aggregators.get(mode)
    if agg is None:
        agg = _aggregators[mode] = Aggregator(settings_for(mode))
    return agg


async def aclose() -> None:
    aggs = list(_aggregators.values())
    _aggregators.clear()
    await asyncio.gather(*(a.aclose() for a in aggs), return_exceptions=True)


# ─────────────────────────── concurrency ───────────────────────────


def slots_busy() -> bool:
    """True when a new search would have to wait for a free slot."""
    return _gate.locked()


@asynccontextmanager
async def search_slot() -> AsyncIterator[None]:
    """Cap simultaneous searches. A Tor search opens up to ~45 circuits; a burst of customers
    would otherwise starve each other and the Tor daemon, so the extras wait their turn."""
    async with _gate:
        yield


# ─────────────────────────── searching ───────────────────────────


def preflight(query: str) -> QuerySpec:
    """Validate a query before it is charged or queued. Raises ValueError with a user-facing
    message when it cannot or must not be searched."""
    spec = parse_query(query)
    if not spec.searchable:
        raise ValueError(NOT_SEARCHABLE_MESSAGE)
    if is_blocked_query(spec.raw) or is_blocked_query(spec.engine_query):
        raise ValueError(BLOCKED_QUERY_MESSAGE)
    return spec


_PRUNED_PER_REASON = 40


def _cap_pruned(pruned: list[PrunedItem]) -> list[PrunedItem]:
    """Keep the "why was this dropped" list readable: a phrase query over a few hundred raw hits
    can prune most of them. The full counts stay in `stats.pruned_by_reason`."""
    kept: list[PrunedItem] = []
    seen: Counter[str] = Counter()
    for item in pruned:
        seen[item.reason] += 1
        if seen[item.reason] <= _PRUNED_PER_REASON:
            kept.append(item)
    return kept


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upstream_failed(response: SearchResponse) -> bool:
    """Did the search fail for reasons that are ours, not the customer's? Then it is refunded.

    True when not one engine managed to answer (Tor down, every mirror dead, nothing
    selectable). An engine that answered with zero hits ("empty") is a real answer.
    """
    if response.blocked_query:
        return False
    return not any(r.status in ("ok", "empty") for r in response.engines)


async def search_events(
    query: str,
    mode: Mode = "gateway",
    *,
    pages: int = 1,
    experimental: bool = False,
    limit: int = 25,
) -> AsyncIterator[dict[str, Any]]:
    """Run a search, yielding `start`, one `engine` per engine as it finishes, then `results`.

    The final event carries the `SearchResponse` (trimmed to `limit`) and a `manifest` dict.
    Tor-mode results also get extracted entities and the evidence hash.
    """
    agg = aggregator_for(mode)
    s = agg.settings
    pages = max(1, min(pages, app_settings.darkweb_max_pages))
    # Experimental engines are unvetted and usually dead; they only make sense over Tor.
    experimental = bool(experimental and mode == "tor" and app_settings.darkweb_allow_experimental)
    started_at, t0 = _utc(), time.monotonic()

    async for event in agg.stream(query, pages=pages, include_experimental=experimental or None):
        if event["type"] != "results":
            yield event
            continue
        response: SearchResponse = event["response"]
        response.results = response.results[: max(1, min(limit, s.max_results))]
        response.pruned = _cap_pruned(response.pruned)
        manifest: dict[str, Any] = {
            "tool": TOOL,
            "version": VERSION,
            "mode": mode,
            "transport": response.transport,
            "keyword": response.query,
            "operators": response.operators,
            "started_utc": started_at,
            "completed_utc": _utc(),
            "elapsed_seconds": round(time.monotonic() - t0, 2),
            "result_count": len(response.results),
            "engines_queried": response.stats.engines_queried,
            "engines_responded": response.stats.engines_with_results,
            "parameters": {"pages": pages, "experimental": experimental, "limit": limit},
        }
        if mode == "tor" and not response.blocked_query:
            attach_entities(response.results)
            manifest["sha256"] = evidence_hash(response.results)
        yield {"type": "results", "response": response, "manifest": manifest}


# ─────────────────────────── status ───────────────────────────

# Cache the Tor status so /health doesn't re-probe on every call.
_tor_cache: dict[str, Any] = {"at": 0.0, "ok": False, "detail": "not checked"}


def tor_status() -> tuple[bool, str]:
    """Fast, cached reachability check on the Tor SOCKS port.

    A full through-Tor verification (check.torproject.org) is slow (seconds) and races on
    the first connection, which made the status dot flicker. For a health indicator,
    confirming the SOCKS port is open is the right granularity; the search itself still routes
    real traffic through Tor and reports per-engine failures.
    """
    now = time.time()
    if now - _tor_cache["at"] < 20:
        return _tor_cache["ok"], _tor_cache["detail"]
    host, port = app_settings.tor_host, app_settings.tor_port
    try:
        with socket.create_connection((host, port), timeout=2.0):
            ok, detail = True, f"SOCKS {host}:{port} reachable"
    except OSError:
        ok, detail = False, f"SOCKS {host}:{port} unreachable — start Tor: sudo systemctl start tor"
    _tor_cache.update(at=now, ok=ok, detail=detail)
    return ok, detail


async def tor_verify() -> dict[str, Any]:
    """Full through-Tor confirmation (slow): does traffic really exit via Tor?"""
    return await tor_check(settings_for("tor"))


def engine_roster(mode: Mode) -> list[dict[str, Any]]:
    """The engines a search in this mode would query, with their live health."""
    agg = aggregator_for(mode)
    health = agg.health.snapshot()
    now = time.time()
    out = []
    for e in agg.select():
        h = health.get(e.name)
        out.append(
            {
                "name": e.name,
                "label": e.spec.label,
                "tier": e.spec.tier,
                "notes": e.spec.notes,
                "benched": bool(h and h["benched_until"] > now),
                "success_rate": None if h is None else round(h["success_rate"], 2),
                "latency_s": None if h is None or h["latency_s"] is None else round(h["latency_s"], 1),
            }
        )
    return out

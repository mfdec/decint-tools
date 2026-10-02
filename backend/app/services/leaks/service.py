"""Leak-search aggregator.

Fans out the enabled providers concurrently over a shared HTTP client, merges
their normalized hits, masks secrets by default, and caches results briefly so
repeated lookups of the same query don't re-hammer the sources.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from ...config import settings
from ...models import LeakSearchResponse, LeakSource
from . import local
from .base import detect_kind, mask_line, mask_secret
from .providers import REGISTRY

# tiny in-process TTL cache: {(query, kind, masked): (expires, response)}
_cache: dict[tuple[str, str, bool], tuple[float, LeakSearchResponse]] = {}


def invalidate_cache() -> None:
    """Forget cached answers. Called when an uploaded dataset is added, paused
    or removed — otherwise a removed dataset would keep answering from cache."""
    _cache.clear()


local.on_change = invalidate_cache


def _enabled_providers():
    providers = [REGISTRY[k] for k in settings.leaks_provider_list
                 if k in REGISTRY and k != "local"]
    # Uploaded datasets are switched on by uploading one, not by an env var.
    if local.has_searchable():
        providers.append(REGISTRY["local"])
    return providers


async def search_leaks(
    query: str, kind: str = "auto", reveal: bool = False
) -> LeakSearchResponse:
    query = query.strip()
    if kind == "auto":
        kind = detect_kind(query)

    masked = not reveal
    cache_key = (query.lower(), kind, masked)
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    providers = _enabled_providers()
    timeout = httpx.Timeout(settings.leaks_timeout)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(p.search(client, query, kind) for p in providers),
            return_exceptions=True,
        )

    sources: list[LeakSource] = []
    hits = []
    for prov, res in zip(providers, results):
        if isinstance(res, Exception):
            sources.append(
                LeakSource(key=prov.key, label=prov.label, ok=False, count=0,
                           status=f"crashed: {type(res).__name__}")
            )
            continue
        sources.append(
            LeakSource(key=res.key, label=res.label, ok=res.ok,
                       count=res.count, status=res.status)
        )
        hits.extend(res.hits)

    if masked:
        for h in hits:
            h.password = mask_secret(h.password)
            h.line = mask_line(h.line)

    hits = _dedupe(hits)

    response = LeakSearchResponse(
        query=query,
        kind=kind,  # type: ignore[arg-type]
        total=len(hits),
        masked=masked,
        sources=sources,
        hits=hits,
    )
    _cache[cache_key] = (now + settings.leaks_cache_ttl, response)
    return response


def _dedupe(hits):
    seen = set()
    out = []
    for h in hits:
        sig = (h.source, h.breach, h.email, h.username, h.line)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(h)
    return out

"""Fan a query out to every selected engine in parallel and run the result pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from .config import Settings
from .engines import Engine, EngineOutcome, load_catalog
from .health import HealthStore
from .models import EngineReport, SearchResponse
from .pipeline import PipelineContext, run_pipeline
from .pipeline.filters import QueryFilters
from .pipeline.safety import is_blocked_query
from .query import parse_query
from .tor import Fetcher, FetchError, ReplayFetcher, make_fetcher, proxy_reachable

log = logging.getLogger(__name__)

BLOCKED_QUERY_MESSAGE = (
    "This query was blocked. DECINT refuses searches for child sexual abuse material."
)
NOT_SEARCHABLE_MESSAGE = (
    "Add at least one search word. A query made only of excluded terms has nothing to search for."
)
_STATUS_ORDER = {"ok": 0, "empty": 1, "blocked": 2, "timeout": 3, "error": 4, "benched": 5, "skipped": 6}
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_BANLIST_TTL = 24 * 3600


class Aggregator:
    def __init__(
        self,
        settings: Settings,
        *,
        engines: list[Engine] | None = None,
        fetcher: Fetcher | None = None,
        health: HealthStore | None = None,
    ):
        self.settings = settings
        self.engines = engines if engines is not None else load_catalog(settings.catalog_path)
        self._fetcher = fetcher
        if health is None:
            persist = settings.health_enabled and not settings.replay_dir
            health = HealthStore(
                settings.cache_dir / "health.json" if persist else None,
                failures_to_bench=settings.breaker_failures,
                cooldown=settings.breaker_cooldown,
            )
        self.health = health
        self._banlist: set[str] | None = None
        self._banlist_at = 0.0

    # -- setup -------------------------------------------------------------------------------

    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = make_fetcher(self.settings)
        return self._fetcher

    @property
    def all_engine_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for engine in self.engines:
            hosts |= engine.own_hosts
        return hosts

    def engine(self, name: str) -> Engine | None:
        return next((e for e in self.engines if e.name == name), None)

    def select(self, names: list[str] | None = None, include_experimental: bool | None = None) -> list[Engine]:
        allow = [n.lower() for n in (names or [])] or self.settings.engine_allowlist
        deny = self.settings.engine_denylist
        experimental = self.settings.include_experimental if include_experimental is None else include_experimental
        replay = self.fetcher if isinstance(self.fetcher, ReplayFetcher) else None
        chosen = []
        for engine in self.engines:
            if allow and engine.name not in allow:
                continue
            if not allow and engine.spec.tier == "experimental" and not experimental:
                continue
            if engine.name in deny or not engine.available(self.settings.transport):
                continue
            if replay is not None and not replay.has_engine(engine.name):
                continue
            chosen.append(engine)
        return chosen

    # -- search ------------------------------------------------------------------------------

    async def stream(
        self,
        query: str,
        *,
        pages: int | None = None,
        engines: list[str] | None = None,
        include_experimental: bool | None = None,
        deadline: float | None = None,
        ignore_health: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield progress events: one `start`, one `engine` per engine, then `results`."""
        started = time.monotonic()
        s = self.settings
        query = " ".join(query.split())[:300]
        spec = parse_query(query)
        engine_query = spec.engine_query  # plain words only: operators are enforced afterwards
        selected = self.select(engines, include_experimental)
        transport = "replay" if s.replay_dir else s.transport
        yield {
            "type": "start",
            "query": query,
            "transport": transport,
            "engines": [{"name": e.name, "label": e.spec.label, "tier": e.spec.tier} for e in selected],
        }

        def finish(**kwargs: Any) -> dict[str, Any]:
            kwargs.setdefault("took_ms", int((time.monotonic() - started) * 1000))
            kwargs.setdefault("operators", spec.operators)
            return {"type": "results", "response": SearchResponse(query=query, transport=transport, **kwargs)}

        if not query:
            yield finish(message="Empty query.")
            return
        if not spec.searchable:
            yield finish(message=NOT_SEARCHABLE_MESSAGE)
            return
        # Both forms: the text as typed, and the text with operators peeled off, so neither
        # `-"a" "b"` tricks nor separators can hide a blocked term from the check.
        if is_blocked_query(query) or is_blocked_query(engine_query):
            yield finish(blocked_query=True, message=BLOCKED_QUERY_MESSAGE)
            return
        if not await proxy_reachable(s):
            yield finish(
                message=(
                    f"Cannot reach the Tor SOCKS proxy at {s.tor_proxy}. "
                    "Start the tor service (sudo systemctl start tor), or use gateway mode, "
                    "which needs no Tor."
                ),
                engines=[EngineReport(engine=e.name, label=e.spec.label, status="error",
                                      error="Tor proxy unreachable") for e in selected],
            )
            return

        pages = pages or s.pages
        end_at = started + (deadline or s.deadline)
        banlist_task = asyncio.create_task(self._load_banlist()) if s.use_ahmia_banlist else None
        tasks: dict[asyncio.Task[EngineOutcome], Engine] = {}
        reports: list[EngineReport] = []
        raw = []
        try:
            for engine in selected:
                if not ignore_health and self.health.is_benched(engine.name):
                    rep = EngineReport(
                        engine=engine.name, label=engine.spec.label, status="benched",
                        error=f"benched after repeated failures ({self.health.get(engine.name).last_error})",
                    )
                    reports.append(rep)
                    yield {"type": "engine", "report": rep}
                    continue
                timeout = self.health.timeout_for(engine.name, s.read_timeout)
                task = asyncio.create_task(
                    engine.search(self.fetcher, engine_query, transport=s.transport, pages=pages, timeout=timeout)
                )
                tasks[task] = engine

            pending: set[asyncio.Task[EngineOutcome]] = set(tasks)
            while pending:
                remaining = end_at - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    engine = tasks[task]
                    try:
                        outcome = task.result()
                    except Exception as exc:  # a parser bug must not sink the whole search
                        log.exception("engine %s crashed", engine.name)
                        outcome = EngineOutcome([], EngineReport(
                            engine=engine.name, label=engine.spec.label, status="error",
                            error=f"internal error: {exc!r}"))
                    raw.extend(outcome.results)
                    reports.append(outcome.report)
                    self._record_health(outcome.report)
                    yield {"type": "engine", "report": outcome.report}

            for task in pending:
                task.cancel()
                engine = tasks[task]
                rep = EngineReport(
                    engine=engine.name, label=engine.spec.label, status="timeout",
                    latency_ms=int((time.monotonic() - started) * 1000), error="search deadline reached",
                )
                reports.append(rep)
                self._record_health(rep)
                yield {"type": "engine", "report": rep}
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            banned: set[str] = set()
            if banlist_task is not None:
                try:
                    banned = await asyncio.wait_for(banlist_task, timeout=max(0.5, min(5.0, end_at - time.monotonic())))
                except Exception as exc:  # the banlist is an extra filter; never fail a search over it
                    log.warning("Ahmia banlist unavailable: %s", exc)
                    banned = self._banlist or set()
        finally:
            # Also reached when the consumer stops early (e.g. the browser closed the SSE stream).
            for task in [*tasks, *([banlist_task] if banlist_task else [])]:
                if not task.done():
                    task.cancel()
                elif not task.cancelled():
                    task.exception()  # already failed and nobody awaited it: mark it retrieved

        ctx = PipelineContext(
            settings=s,
            engine_hosts=self.all_engine_hosts,
            weights={e.name: e.spec.weight for e in self.engines},
            groups={e.name: e.spec.group for e in self.engines},
            banned_md5=banned,
            filters=QueryFilters.from_spec(spec),
        )
        results, pruned, stats = run_pipeline(engine_query, raw, ctx)
        stats.engines_queried = len(selected)
        stats.engines_with_results = sum(1 for r in reports if r.status == "ok")
        self.health.save()
        reports.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 9), -r.results, r.engine))
        yield finish(results=results, pruned=pruned, engines=reports, stats=stats)

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        response = None
        async for event in self.stream(query, **kwargs):
            if event["type"] == "results":
                response = event["response"]
        assert response is not None
        return response

    async def probe(self, query: str = "bitcoin", *, engines: list[str] | None = None,
                    include_experimental: bool = True) -> SearchResponse:
        """Query every engine (ignoring the circuit breaker) to see which ones are alive."""
        return await self.search(query, engines=engines, include_experimental=include_experimental,
                                 ignore_health=True)

    async def aclose(self) -> None:
        if self._fetcher is not None:
            await self._fetcher.aclose()

    # -- helpers -----------------------------------------------------------------------------

    def _record_health(self, report: EngineReport) -> None:
        if report.status in ("ok", "empty"):
            self.health.record(report.engine, True, (report.latency_ms or 0) / 1000)
        elif report.status in ("timeout", "error", "blocked"):
            self.health.record(report.engine, False, error=report.error)

    async def _load_banlist(self) -> set[str]:
        """Ahmia publishes MD5 hashes of onion hosts it banned for abuse material."""
        if self._banlist is not None and time.time() - self._banlist_at < _BANLIST_TTL:
            return self._banlist
        cache = None if self.settings.replay_dir else self.settings.cache_dir / "ahmia_banned.txt"
        text = ""
        if cache and cache.is_file() and time.time() - cache.stat().st_mtime < _BANLIST_TTL:
            text = cache.read_text()
        else:
            ahmia = self.engine("ahmia")
            for base, _paths in ahmia.endpoints(self.settings.transport) if ahmia else []:
                for path in ("/banned/", "/blacklist/banned/"):
                    try:
                        resp = await self.fetcher.get(f"{base}{path}", engine="ahmia", kind="banlist", timeout=30)
                    except FetchError:
                        continue
                    if _MD5_RE.match(resp.text.strip().split("\n", 1)[0].strip()):
                        text = resp.text
                        break
                if text:
                    break
            if text and cache:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(text)
                except OSError:
                    pass
        banned = {line.strip() for line in text.splitlines() if _MD5_RE.match(line.strip())}
        if banned or self._banlist is None:
            self._banlist, self._banlist_at = banned, time.time()
        return self._banlist or set()

"""Engine runtime: URL building, mirror failover, pagination and parsing for one engine."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus, urlencode, urlsplit

from ..models import EngineReport, RawResult
from ..onion import host_of
from ..tor import Fetcher, FetchError
from ..urls import canonicalize
from .parsers import ParseContext, looks_blocked, parse_page
from .special import PREP_HOOKS, PrepResult

_PAGE_PLACEHOLDERS = ("{page}", "{page0}", "{offset}")
_PREP_TTL = 30 * 60  # Ahmia tokens stay valid for 60 minutes


@dataclass
class EngineSpec:
    name: str
    label: str
    tier: str
    parser: str
    mirrors: list[str]
    search_paths: list[str]
    clearnet: str | None = None
    clearnet_search_paths: list[str] | None = None
    per_page: int = 20
    max_pages: int = 1
    weight: float = 0.6
    group: str = ""
    prep: str | None = None
    page_delay: float = 0.0
    max_query_len: int | None = None
    notes: str = ""
    rejected_mirrors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.group = self.group or self.name


@dataclass
class EngineOutcome:
    results: list[RawResult]
    report: EngineReport


def fill_template(template: str, query: str, page: int, per_page: int) -> str:
    q_in_path = "{q}" in template.split("?", 1)[0]
    encoded = quote(query, safe="") if q_in_path else quote_plus(query)
    return (
        template.replace("{q}", encoded)
        .replace("{page0}", str(page - 1))
        .replace("{page}", str(page))
        .replace("{offset}", str((page - 1) * per_page))
    )


def _with_params(url: str, params: dict[str, str]) -> str:
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urlencode(params)


def _bounced_home(requested: str, final: str) -> bool:
    """A search that lands on the homepage was rejected (e.g. Ahmia's token gate)."""
    req_path, final_path = urlsplit(requested).path, urlsplit(final).path
    return req_path not in ("", "/") and final_path in ("", "/") and urlsplit(requested).query != ""


class Engine:
    def __init__(self, spec: EngineSpec):
        self.spec = spec
        self._prep_cache: dict[str, tuple[float, PrepResult]] = {}

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def own_hosts(self) -> set[str]:
        hosts = set(self.spec.mirrors)
        if self.spec.clearnet:
            hosts.add(host_of(self.spec.clearnet))
        return hosts

    def endpoints(self, transport: str) -> list[tuple[str, list[str]]]:
        eps: list[tuple[str, list[str]]] = []
        if transport == "tor":
            eps += [(f"http://{m}", self.spec.search_paths) for m in self.spec.mirrors]
        if self.spec.clearnet:
            eps.append((self.spec.clearnet.rstrip("/"), self.spec.clearnet_search_paths or self.spec.search_paths))
        return eps

    def available(self, transport: str) -> bool:
        return bool(self.endpoints(transport))

    def page_count(self, requested: int) -> int:
        paged = any(p in path for path in self.spec.search_paths for p in _PAGE_PLACEHOLDERS)
        return max(1, min(requested, self.spec.max_pages)) if paged else 1

    def _query_for_engine(self, query: str) -> str:
        limit = self.spec.max_query_len
        if limit and len(query) > limit:
            query = query[:limit].rsplit(" ", 1)[0]
        return query

    async def _prepare(self, fetcher: Fetcher, base: str, timeout: float) -> PrepResult:
        if not self.spec.prep:
            return PrepResult()
        cached = self._prep_cache.get(base)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        hook = PREP_HOOKS[self.spec.prep]
        result = await hook(fetcher, self.name, base, timeout)
        self._prep_cache[base] = (time.monotonic() + _PREP_TTL, result)
        return result

    async def search(
        self, fetcher: Fetcher, query: str, *, transport: str, pages: int = 1, timeout: float = 40.0
    ) -> EngineOutcome:
        start = time.monotonic()
        query = self._query_for_engine(query)
        errors: list[FetchError] = []
        blocked_detail = ""

        def report(status: str, results: list[RawResult], pages_done: int = 0, endpoint: str | None = None,
                   error: str | None = None) -> EngineOutcome:
            return EngineOutcome(
                results,
                EngineReport(
                    engine=self.name, label=self.spec.label, status=status, results=len(results),
                    pages=pages_done, latency_ms=int((time.monotonic() - start) * 1000),
                    endpoint=endpoint, error=error,
                ),
            )

        for base, paths in self.endpoints(transport):
            try:
                prep = await self._prepare(fetcher, base, timeout)
                templates = [prep.search_url] if prep.search_url else [base + p for p in paths]
                results, pages_done, blocked = await self._fetch_pages(
                    fetcher, templates, prep.extra_params, query, self.page_count(pages), timeout
                )
            except FetchError as exc:
                errors.append(exc)
                self._prep_cache.pop(base, None)
                continue
            if results:
                return report("ok", results, pages_done, host_of(base))
            if blocked:
                # Challenge page or token gate: another mirror/gateway may still work.
                blocked_detail = f"{host_of(base)}: {blocked}"
                self._prep_cache.pop(base, None)
                continue
            return report("empty", [], pages_done, host_of(base))

        if blocked_detail:
            return report("blocked", [], error=blocked_detail)
        if not errors:
            return report("skipped", [], error="no endpoint available for this transport")
        status = "timeout" if all(e.kind == "timeout" for e in errors) else "error"
        return report(status, [], error="; ".join(dict.fromkeys(str(e) for e in errors)))

    async def _fetch_pages(
        self, fetcher: Fetcher, templates: list[str], extra: dict[str, str], query: str,
        pages: int, timeout: float,
    ) -> tuple[list[RawResult], int, str]:
        results: list[RawResult] = []
        seen: set[tuple[str, bool]] = set()
        organic = 0
        pages_done = 0
        template: str | None = None
        own = self.own_hosts
        for page in range(1, pages + 1):
            if page > 1:
                if self.spec.page_delay:
                    await asyncio.sleep(self.spec.page_delay)
                try:
                    url = _with_params(fill_template(template or templates[0], query, page, self.spec.per_page), extra)
                    resp = await fetcher.get(url, engine=self.name, kind=f"page{page}", timeout=timeout)
                except FetchError:
                    break  # keep the pages we already have
            else:
                resp, url = None, ""
                for i, tpl in enumerate(templates):
                    url = _with_params(fill_template(tpl, query, page, self.spec.per_page), extra)
                    try:
                        resp = await fetcher.get(url, engine=self.name, kind="page1", timeout=timeout)
                    except FetchError as exc:
                        if exc.status in (404, 405) and i < len(templates) - 1:
                            continue  # try the next known search path
                        raise
                    template = tpl
                    break
                assert resp is not None
            pages_done += 1
            items, _parser = parse_page(self.spec.parser, resp.text, ParseContext(base_url=resp.url, own_hosts=own))
            if not items:
                if page == 1 and _bounced_home(url, resp.url):
                    return results, pages_done, "search redirected to homepage (token gate)"
                if page == 1 and looks_blocked(resp.text):
                    return results, pages_done, "challenge/captcha page"
                break
            new = 0
            for item in items:
                # An ad and an organic hit can share a URL; keep both so the organic one survives.
                key = (canonicalize(item.url), item.sponsored)
                if key in seen:
                    continue
                seen.add(key)
                new += 1
                if not item.sponsored:
                    organic += 1
                results.append(
                    RawResult(
                        engine=self.name, title=item.title or host_of(item.url), url=item.url,
                        snippet=item.snippet, rank=organic if not item.sponsored else 0, page=page,
                        sponsored=item.sponsored, badge=item.badge, last_seen=item.last_seen,
                    )
                )
            if new == 0:
                break
        return results, pages_done, ""

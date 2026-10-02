"""Post-processing: normalise -> safety -> filter -> merge -> cluster -> score -> prune -> diversify."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..config import Settings
from ..models import PrunedItem, RawResult, RelatedLink, Result, SearchStats
from ..onion import host_of, is_v2, is_valid_v3, onion_host
from ..text import clean_text, has_alnum, is_placeholder, strip_urls
from ..urls import canonicalize, display_url
from .dedupe import Merged, cluster, merge_exact
from .filters import QueryFilters
from .quality import assess
from .rank import RankContext, query_coverage, score_all, stems
from .safety import is_banned_host, is_blocked_text


@dataclass
class PipelineContext:
    settings: Settings
    engine_hosts: set[str] = field(default_factory=set)
    weights: dict[str, float] = field(default_factory=dict)
    groups: dict[str, str] = field(default_factory=dict)
    banned_md5: set[str] = field(default_factory=set)
    filters: QueryFilters = field(default_factory=QueryFilters)


class _Pruner:
    """Collects pruned items, merging repeats of the same URL+reason across engines."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], PrunedItem] = {}

    def add(self, url: str, title: str, engines: list[str], reason: str, detail: str = "",
            key: str | None = None) -> None:
        key = (key or canonicalize(url), reason)
        item = self._items.get(key)
        if item is None:
            self._items[key] = PrunedItem(url=url, title=title or url, engines=sorted(set(engines)),
                                          reason=reason, detail=detail)
        else:
            item.engines = sorted(set(item.engines) | set(engines))

    def items(self) -> list[PrunedItem]:
        return sorted(self._items.values(), key=lambda p: (p.reason, -len(p.engines), p.url))


def _link(m: Merged) -> RelatedLink:
    return RelatedLink(url=m.url, title=m.title, engines=m.engines)


def run_pipeline(query: str, raw: list[RawResult], ctx: PipelineContext
                 ) -> tuple[list[Result], list[PrunedItem], SearchStats]:
    s = ctx.settings
    stats = SearchStats(raw_results=len(raw))
    pruner = _Pruner()

    # 1-3. normalise, safety, hard filters
    kept: list[tuple[RawResult, str, str, str, str]] = []
    advertisers: set[str] = set()
    for r in raw:
        url = canonicalize(r.url)
        shown = display_url(r.url)
        title = clean_text(r.title, 200)
        snippet = strip_urls(clean_text(r.snippet, 400))
        if is_placeholder(snippet):
            snippet = ""
        if is_placeholder(title):
            title = ""
        if is_blocked_text(f"{title} {snippet} {url}"):
            stats.safety_blocked += 1
            continue
        host = onion_host(url)
        if host and is_banned_host(host, ctx.banned_md5):
            stats.safety_blocked += 1
            continue
        if not host:
            reason = "self_link" if host_of(url) in ctx.engine_hosts else "non_onion"
            pruner.add(shown, title, [r.engine], reason)
        elif is_v2(host):
            pruner.add(shown, title, [r.engine], "dead_v2", "v2 onion addresses stopped working in 2021")
        elif not is_valid_v3(host):
            pruner.add(shown, title, [r.engine], "invalid_address", "checksum mismatch - typo or phishing look-alike")
        elif host in ctx.engine_hosts:
            pruner.add(shown, title, [r.engine], "self_link")
        elif r.sponsored:
            advertisers.add(host)
            pruner.add(shown, title, [r.engine], "sponsored", "paid placement")
        elif not has_alnum(title) and not snippet:
            pruner.add(shown, title, [r.engine], "empty")
        else:
            kept.append((r, url, host, title, snippet))

    # 4. exact merge across engines
    merged = merge_exact(kept)
    stats.unique_urls = len(merged)
    stats.merged_duplicates = len(kept) - len(merged)

    # 5. near-duplicate pages and mirror/clone clusters
    merged, absorbed, stats.mirror_clusters = cluster(merged)
    for dup, into in absorbed:
        pruner.add(dup.url, dup.title, dup.engines, "near_duplicate", f"same content as {into.url}")

    # 5b. search-box operators: -excluded words and "required phrases"
    if ctx.filters.active:
        allowed = []
        for m in merged:
            verdict = ctx.filters.verdict(m)
            if verdict is None:
                allowed.append(m)
            else:
                pruner.add(m.url, m.title, m.engines, *verdict)
        merged = allowed

    # 6-7. quality and scoring
    for m in merged:
        m.quality, m.flags = assess(m, query, frozenset(advertisers))
    rank_ctx = RankContext(weights=ctx.weights, groups=ctx.groups, w_relevance=s.w_relevance,
                           w_fusion=s.w_fusion, w_consensus=s.w_consensus, w_quality=s.w_quality)
    scored = list(zip(merged, score_all(merged, query, rank_ctx), strict=True))

    # 8. prune useless results
    q_stems = set(stems(query))
    survivors = []
    for m, breakdown in scored:
        single_source = len({ctx.groups.get(e, e) for e in m.engine_ranks}) == 1
        if q_stems and single_source and query_coverage(m, q_stems) == 0:
            pruner.add(m.url, m.title, m.engines, "low_relevance", "no query terms and only one engine")
        elif m.quality < s.min_quality:
            pruner.add(m.url, m.title, m.engines, "spam", ", ".join(m.flags))
        else:
            survivors.append((m, breakdown))
    survivors.sort(key=lambda mb: mb[1].final, reverse=True)

    # 9. host crowding: at most N results per site, the rest collapse under its best result
    results: list[Result] = []
    lead_for_host: dict[str, Result] = {}
    shown_per_host: Counter[str] = Counter()
    for m, breakdown in survivors:
        if shown_per_host[m.host] >= max(1, s.max_per_host):
            lead_for_host[m.host].more_from_site.append(_link(m))
            stats.collapsed += 1
            continue
        result = Result(
            url=m.url, host=m.host, title=m.title, snippet=m.snippet, engines=m.engines,
            engine_ranks=m.engine_ranks, score=breakdown.final, breakdown=breakdown,
            quality=round(m.quality, 3), flags=m.flags, badge=m.badge, last_seen=m.last_seen,
            merged=m.raw_count, mirrors=[_link(x) for x in m.mirrors],
            corroboration=len({ctx.groups.get(e, e) for e in m.engine_ranks}),
        )
        shown_per_host[m.host] += 1
        lead_for_host.setdefault(m.host, result)
        results.append(result)
    results = results[: s.max_results]

    pruned = pruner.items()
    counts: dict[str, int] = {}
    for p in pruned:
        counts[p.reason] = counts.get(p.reason, 0) + 1
    stats.pruned_by_reason = counts
    stats.shown = len(results)
    return results, pruned, stats

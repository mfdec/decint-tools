"""Data models shared by engines, the pipeline, the CLI and the web API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EngineStatus = Literal["ok", "empty", "timeout", "error", "blocked", "benched", "skipped"]

PruneReason = Literal[
    "non_onion",       # clearnet result
    "dead_v2",         # v2 onion address (dead since 2021)
    "invalid_address", # bad v3 checksum: typo-squat or garbage
    "sponsored",       # paid placement injected by the engine
    "self_link",       # link back to a search engine (navigation, ads, mirrors)
    "empty",           # no usable title or text
    "near_duplicate",  # same site, near-identical page already shown
    "low_relevance",   # nothing in common with the query and only one engine found it
    "spam",            # quality score under the threshold (scam lexicon, emoji spam, stuffing)
    "excluded",        # contains a -word or -"phrase" the searcher ruled out
    "phrase_missing",  # a required "quoted phrase" is not in the title or description
]


class RawResult(BaseModel):
    """One hit as reported by one engine, before any merging."""

    engine: str
    title: str
    url: str
    snippet: str = ""
    rank: int = 1
    page: int = 1
    sponsored: bool = False
    badge: str | None = None  # engine-side verification label (VormWeb: Verified/Warning/Risky)
    last_seen: datetime | None = None


class ScoreBreakdown(BaseModel):
    relevance: float = 0.0
    fusion: float = 0.0
    consensus: float = 0.0
    quality: float = 0.0
    freshness: float = 0.0
    final: float = 0.0


class RelatedLink(BaseModel):
    """A collapsed entry under a result: a mirror/clone or another page of the same site."""

    url: str
    title: str
    engines: list[str] = Field(default_factory=list)


class Result(BaseModel):
    url: str
    host: str
    title: str
    snippet: str = ""
    engines: list[str]
    engine_ranks: dict[str, int] = Field(default_factory=dict)
    score: float = 0.0
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    quality: float = 1.0
    flags: list[str] = Field(default_factory=list)
    badge: str | None = None
    last_seen: datetime | None = None
    merged: int = 1  # how many raw hits were folded into this result
    corroboration: int = 1  # distinct backend indexes that returned it (front-ends of one index count once)
    mirrors: list[RelatedLink] = Field(default_factory=list)
    more_from_site: list[RelatedLink] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)  # Tor mode: onions, emails, wallets, PGP


class PrunedItem(BaseModel):
    url: str
    title: str
    engines: list[str]
    reason: PruneReason
    detail: str = ""


class EngineReport(BaseModel):
    engine: str
    label: str = ""
    status: EngineStatus
    results: int = 0
    pages: int = 0
    latency_ms: int | None = None
    endpoint: str | None = None
    error: str | None = None


class SearchStats(BaseModel):
    raw_results: int = 0
    unique_urls: int = 0
    merged_duplicates: int = 0
    mirror_clusters: int = 0
    shown: int = 0
    collapsed: int = 0
    safety_blocked: int = 0
    pruned_by_reason: dict[str, int] = Field(default_factory=dict)
    engines_queried: int = 0
    engines_with_results: int = 0


class SearchResponse(BaseModel):
    query: str
    transport: str = "tor"
    results: list[Result] = Field(default_factory=list)
    pruned: list[PrunedItem] = Field(default_factory=list)
    engines: list[EngineReport] = Field(default_factory=list)
    stats: SearchStats = Field(default_factory=SearchStats)
    took_ms: int = 0
    blocked_query: bool = False
    message: str | None = None
    operators: dict[str, list[str]] = Field(default_factory=dict)  # "phrases" / -excluded in force

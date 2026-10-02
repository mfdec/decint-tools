"""Merge identical URLs across engines, then cluster near-duplicates and mirrors/clones."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from ..models import RawResult
from ..text import is_truncated, tokenize
from ..urls import display_url
from .simhash import near_pairs, shingles, simhash

_GENERIC_TITLES = {"no title", "untitled", "home", "index", "welcome", "login", "loading"}


@dataclass
class Merged:
    key: str  # canonical URL (dedupe key)
    host: str
    url: str = ""  # URL to show/link: as reported by the first engine, minus tracking params
    titles: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)
    engine_ranks: dict[str, int] = field(default_factory=dict)
    badge: str | None = None
    last_seen: datetime | None = None
    raw_count: int = 0
    title: str = ""
    snippet: str = ""
    quality: float = 1.0
    flags: list[str] = field(default_factory=list)
    mirrors: list[Merged] = field(default_factory=list)
    absorbed: list[Merged] = field(default_factory=list)

    @property
    def engines(self) -> list[str]:
        return sorted(self.engine_ranks, key=lambda e: self.engine_ranks[e])

    def add(self, raw: RawResult, title: str, snippet: str) -> None:
        self.raw_count += 1
        if not self.url:
            self.url = display_url(raw.url)
        if title:
            self.titles.append(title)
        if snippet:
            self.snippets.append(snippet)
        prev = self.engine_ranks.get(raw.engine)
        self.engine_ranks[raw.engine] = raw.rank if prev is None else min(prev, raw.rank)
        if raw.badge and not self.badge:
            self.badge = raw.badge
        if raw.last_seen and (self.last_seen is None or raw.last_seen > self.last_seen):
            self.last_seen = raw.last_seen

    def absorb(self, other: Merged) -> None:
        """Fold a near-duplicate page of the same site into this one (its engines count too)."""
        self.absorbed.append(other)
        for engine, rank in other.engine_ranks.items():
            self.engine_ranks[engine] = min(rank, self.engine_ranks.get(engine, rank))
        self.raw_count += other.raw_count


def _looks_like_address(title: str, host: str) -> bool:
    t = title.lower().strip("/ ")
    return t.startswith(("http://", "https://")) or t == host or host.split(".")[0] in t.replace(" ", "")


def choose_title(titles: list[str], host: str) -> str:
    usable = [t for t in titles if t and not _looks_like_address(t, host) and t.lower() not in _GENERIC_TITLES]
    if not usable:
        return titles[0] if titles else host
    votes = Counter(t.lower().rstrip(".… ") for t in usable)

    def key(t: str) -> tuple:
        return (votes[t.lower().rstrip(".… ")], not is_truncated(t), min(len(t), 120))

    return max(usable, key=key)


def choose_snippet(snippets: list[str]) -> str:
    if not snippets:
        return ""
    return max(snippets, key=lambda s: (not is_truncated(s), min(len(s), 300)))


def merge_exact(items: list[tuple[RawResult, str, str, str, str]]) -> list[Merged]:
    """items: (raw, canonical_url, host, clean_title, clean_snippet). Keeps first-seen order."""
    merged: dict[str, Merged] = {}
    for raw, key, host, title, snippet in items:
        entry = merged.get(key)
        if entry is None:
            entry = merged[key] = Merged(key=key, host=host)
        entry.add(raw, title, snippet)
    for entry in merged.values():
        entry.title = choose_title(entry.titles, entry.host)
        entry.snippet = choose_snippet(entry.snippets)
    return list(merged.values())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _rep_key(m: Merged) -> tuple:
    verified = (m.badge or "").lower() == "verified"
    return (len(m.engine_ranks), verified, -min(m.engine_ranks.values(), default=99), len(m.snippet), len(m.title))


def cluster(merged: list[Merged], max_distance: int = 5, min_title_jaccard: float = 0.5
            ) -> tuple[list[Merged], list[tuple[Merged, Merged]], int]:
    """Group near-identical results.

    Two results are linked when their title+snippet SimHashes are within `max_distance` bits AND
    their titles share most words (so boilerplate snippets alone don't merge different pages).
    Same host -> near-duplicate page (absorbed). Different hosts -> mirror/clone (collapsed under
    the best-supported member). Returns (kept, [(absorbed, into)], mirror_cluster_count).
    """
    idx_map: list[int] = []
    hashes: list[int] = []
    title_sets: dict[int, set[str]] = {}
    for i, m in enumerate(merged):
        tokens = tokenize(f"{m.title} {m.snippet}")
        if len(tokens) < 6:
            continue  # too little text to judge similarity safely
        idx_map.append(i)
        hashes.append(simhash(shingles(tokens)))
        title_sets[i] = set(tokenize(m.title))

    parent = list(range(len(merged)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in near_pairs(hashes, max_distance):
        i, j = idx_map[a], idx_map[b]
        if _jaccard(title_sets[i], title_sets[j]) >= min_title_jaccard:
            parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(len(merged)):
        groups.setdefault(find(i), []).append(i)

    kept: list[Merged] = []
    absorbed: list[tuple[Merged, Merged]] = []
    mirror_clusters = 0
    for members in sorted(groups.values(), key=min):
        if len(members) == 1:
            kept.append(merged[members[0]])
            continue
        items = [merged[i] for i in members]
        rep = max(items, key=_rep_key)
        has_mirror = False
        for other in items:
            if other is rep:
                continue
            if other.host == rep.host:
                rep.absorb(other)
                absorbed.append((other, rep))
            else:
                rep.mirrors.append(other)
                has_mirror = True
        mirror_clusters += has_mirror
        kept.append(rep)
    return kept, absorbed, mirror_clusters

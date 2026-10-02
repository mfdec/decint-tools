"""Scoring: BM25 relevance + weighted reciprocal rank fusion + cross-engine consensus + quality."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..models import ScoreBreakdown
from ..text import tokenize
from .dedupe import Merged

RRF_K = 60


def stem(token: str) -> str:
    """Tiny suffix stripper so "markets"/"market" and "hacking"/"hack" match."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3 and not token.endswith("ss"):
            return token[: -len(suffix)]
    return token


def stems(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text)]


def url_terms(url: str) -> list[str]:
    path = urlsplit(url).path
    return [stem(t) for t in tokenize(re.sub(r"[/_\-.]+", " ", path))]


def bm25(docs: list[list[str]], query: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    n = len(docs)
    if not n or not query:
        return [0.0] * n
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df = Counter(term for d in docs for term in set(d))
    scores = []
    for doc in docs:
        tf = Counter(doc)
        dl = len(doc) or 1
        s = 0.0
        for term in set(query):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


@dataclass
class RankContext:
    weights: dict[str, float]  # engine -> trust prior
    groups: dict[str, str]  # engine -> backend index group
    w_relevance: float = 0.40
    w_fusion: float = 0.25
    w_consensus: float = 0.20
    w_quality: float = 0.15


def distinct_groups(m: Merged, ctx: RankContext) -> dict[str, tuple[int, float]]:
    """Best (rank, weight) per backend index, so two front-ends of one index count once."""
    out: dict[str, tuple[int, float]] = {}
    for engine, rank in m.engine_ranks.items():
        group = ctx.groups.get(engine, engine)
        weight = ctx.weights.get(engine, 0.6)
        prev = out.get(group)
        if prev is None or rank < prev[0] or (rank == prev[0] and weight > prev[1]):
            out[group] = (rank, weight)
    return out


def query_coverage(m: Merged, q_stems: set[str]) -> float:
    if not q_stems:
        return 1.0
    doc = set(stems(f"{m.title} {m.snippet}")) | set(url_terms(m.key))
    return len(q_stems & doc) / len(q_stems)


def score_all(merged: list[Merged], query: str, ctx: RankContext) -> list[ScoreBreakdown]:
    q_stems = stems(query)
    q_set = set(q_stems)
    phrase = " ".join(tokenize(query, keep_stopwords=True))
    docs = [stems(m.title) * 2 + stems(m.snippet) + url_terms(m.key) for m in merged]
    raw_bm25 = bm25(docs, q_stems)
    max_bm25 = max(raw_bm25, default=0.0) or 1.0

    groups = [distinct_groups(m, ctx) for m in merged]
    rrf = [sum(w / (RRF_K + r) for r, w in g.values()) for g in groups]
    max_rrf = max(rrf, default=0.0) or 1.0
    now = datetime.now(UTC)

    out = []
    for m, bm, fu, g in zip(merged, raw_bm25, rrf, groups, strict=True):
        coverage = query_coverage(m, q_set)
        text = " ".join(tokenize(f"{m.title} {m.snippet}", keep_stopwords=True))
        relevance = 0.7 * (bm / max_bm25) + 0.3 * coverage
        if len(q_stems) > 1 and phrase and phrase in text:
            relevance += 0.1
        relevance = min(1.0, relevance)
        consensus = min(1.0, math.log1p(len(g)) / math.log1p(4))
        freshness = 0.0
        if m.last_seen:
            age_days = (now - m.last_seen).total_seconds() / 86400
            freshness = 0.05 if age_days <= 7 else 0.02 if age_days <= 30 else 0.0
        fusion = fu / max_rrf
        blended = (
            ctx.w_relevance * relevance
            + ctx.w_fusion * fusion
            + ctx.w_consensus * consensus
            + ctx.w_quality * m.quality
        )
        # Quality also gates the whole score, so spammy pages can't ride on keyword matches;
        # recently crawled pages (Ahmia last-seen) get up to 5% on top. Scores stay within 0..1.
        final = blended * (0.6 + 0.4 * m.quality) * (0.95 + freshness)
        out.append(
            ScoreBreakdown(
                relevance=round(relevance, 4), fusion=round(fusion, 4), consensus=round(consensus, 4),
                quality=round(m.quality, 4), freshness=freshness, final=round(final, 4),
            )
        )
    return out

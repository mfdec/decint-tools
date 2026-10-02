"""64-bit SimHash with banded lookup for near-duplicate detection (pure Python)."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from itertools import combinations

BITS = 64
BANDS = 8  # 8 bands of 8 bits: any pair within Hamming distance 7 shares at least one band
_BAND_BITS = BITS // BANDS
_BAND_MASK = (1 << _BAND_BITS) - 1


def _hash64(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")


def shingles(tokens: list[str], k: int = 3) -> list[str]:
    if len(tokens) < k:
        return list(tokens)
    return [" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]


def simhash(features: list[str]) -> int:
    weights = [0] * BITS
    for feature, count in Counter(features).items():
        h = _hash64(feature)
        for bit in range(BITS):
            weights[bit] += count if (h >> bit) & 1 else -count
    return sum(1 << bit for bit, w in enumerate(weights) if w > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def near_pairs(hashes: list[int], max_distance: int = 5) -> set[tuple[int, int]]:
    """Index pairs whose hashes are within `max_distance` bits."""
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, h in enumerate(hashes):
        for band in range(BANDS):
            buckets[(band, (h >> (band * _BAND_BITS)) & _BAND_MASK)].append(idx)
    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > 500:  # huge buckets = degenerate text
            continue
        for i, j in combinations(members, 2):
            if (i, j) not in pairs and hamming(hashes[i], hashes[j]) <= max_distance:
                pairs.add((i, j))
    return pairs

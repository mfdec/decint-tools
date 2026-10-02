"""Pro-tier extras for Tor-mode searches: entity extraction and a tamper-evident evidence hash.

Both carry over from the previous dark-web engine (decint_darkweb_search v2), so a report
produced before and after the overhaul reads the same way.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import Result
from .onion import is_valid_v3

ONION_V3_RE = re.compile(r"\b([a-z2-7]{56})\.onion\b", re.I)
ONION_V2_RE = re.compile(r"\b([a-z2-7]{16})\.onion\b", re.I)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")
BTC_RE = re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
XMR_RE = re.compile(r"\b[48][0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b")
PGP_RE = re.compile(r"-----BEGIN PGP (?:PUBLIC KEY BLOCK|MESSAGE|SIGNATURE)-----")

_MAX_PER_KIND = 10  # a snippet stuffed with wallets must not bloat the payload


def extract_entities(title: str, snippet: str, url: str) -> dict[str, Any]:
    """Indicators mentioned in a result's title, description and URL. Empty kinds are omitted."""
    blob = f"{title} {snippet} {url}"
    found: dict[str, Any] = {
        "onion_v3": sorted(
            {f"{m.group(1).lower()}.onion" for m in ONION_V3_RE.finditer(blob) if is_valid_v3(m.group(1))}
        ),
        "onion_v2_deprecated": sorted({f"{m.group(1).lower()}.onion" for m in ONION_V2_RE.finditer(blob)}),
        "emails": sorted(set(EMAIL_RE.findall(blob))),
        "btc": sorted(set(BTC_RE.findall(blob))),
        "eth": sorted(set(ETH_RE.findall(blob))),
        "xmr": sorted(set(XMR_RE.findall(blob))),
        "pgp": bool(PGP_RE.search(blob)),
    }
    return {k: (v[:_MAX_PER_KIND] if isinstance(v, list) else v) for k, v in found.items() if v}


def evidence_hash(results: list[Result]) -> str:
    """SHA-256 over the canonical result set, so a report can be shown to be unaltered.

    To verify an export, rebuild the list of `{"url", "title", "score"}` objects in order and
    hash `json.dumps(list, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
    """
    canonical = json.dumps(
        [{"url": r.url, "title": r.title, "score": r.score} for r in results],
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def attach_entities(results: list[Result]) -> None:
    for r in results:
        r.entities = extract_entities(r.title, r.snippet, r.url)

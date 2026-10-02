"""Content quality heuristics: scam/spam signals that separate real sites from junk."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import parse_qsl, urlsplit

from ..text import symbol_count, tokenize
from .dedupe import Merged, _looks_like_address

# Phrases overwhelmingly used by scam listings on onion link lists.
SCAM_PHRASES = [
    "hacker for hire", "hire a hacker", "hire hacker", "hackers for hire", "cvv", "cc dumps",
    "dumps with pin", "cloned card", "clone card", "cloned cards", "prepaid card", "prepaid cards",
    "paypal transfer", "paypal transfers", "paypal accounts",
    "bank transfer", "cash out", "cashout", "bitcoin doubler", "double your bitcoin",
    "btc doubler", "bitcoin multiplier", "money multiplier", "100% legit", "legit vendor",
    "guaranteed profit", "fake money", "counterfeit money", "counterfeit bills", "free bitcoin",
    "bitcoin generator", "private key generator", "wallet generator", "escrow guaranteed",
    "instant delivery", "gift cards", "carding", "hacked accounts", "hitman", "hit man",
    "get rich", "make money fast", "western union",
]
LINK_LIST_PHRASES = [
    "hidden wiki", "onion links", "dark web links", "darknet links", "deep web links",
    "link list", "links directory", "tor links", ".onion links",
]
_SCAM_RE = {p: re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.I) for p in SCAM_PHRASES}
_LINK_RE = {p: re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.I) for p in LINK_LIST_PHRASES}
_PUNCT_SPAM_RE = re.compile(r"([!$*?✅⭐🔥💰])\1{2,}")
# "earn 5-9% per week", "200% daily profit": high-yield investment (Ponzi) scams
_HYIP_RE = re.compile(
    r"\b\d+(?:\s*[-–]\s*\d+)?\s*%\s*(?:per|a|each|every)?\s*(?:day|daily|week|weekly|month|monthly|hour|hourly)\b"
    r"|\b(?:daily|weekly|hourly)\s+(?:profit|return|interest)s?\b",
    re.I,
)

BADGE_ADJUST = {"verified": 0.15, "warning": -0.2, "risky": -0.35}
_SEARCH_PATH_RE = re.compile(r"/(?:search|find|results?|query)(?:\.php|\.html?)?/?$", re.I)
_SEARCH_PARAMS = {"q", "query", "search", "s", "term", "keyword", "keywords", "k"}


def is_search_page(url: str) -> bool:
    """A link to some other site's search-results page (a dead end, not a destination)."""
    parts = urlsplit(url)
    params = {k.lower() for k, _ in parse_qsl(parts.query)}
    return bool(_SEARCH_PATH_RE.search(parts.path) and params & _SEARCH_PARAMS)


def assess(m: Merged, query: str, advertisers: frozenset[str] = frozenset()) -> tuple[float, list[str]]:
    """Return (quality 0..1, flags). Phrases that appear in the query itself are not penalised,
    so a researcher searching for e.g. "cvv shop" still gets ranked results. `advertisers` are
    hosts some engine showed as a paid placement in this search."""
    q = query.lower()
    text = f"{m.title} {m.snippet}"
    score = 1.0
    flags: list[str] = []

    scam_hits = [p for p, rx in _SCAM_RE.items() if p not in q and rx.search(text)]
    if _HYIP_RE.search(text):
        scam_hits.append("hyip")
    if scam_hits:
        score -= min(0.8, 0.2 * len(scam_hits))
        flags.append("scam-signals")

    if any(rx.search(text) for p, rx in _LINK_RE.items() if p not in q):
        score -= 0.1
        flags.append("link-directory")

    emoji = symbol_count(m.title)
    if emoji >= 6:
        score -= 0.25
        flags.append("emoji-spam")
    elif emoji >= 3:
        score -= 0.15
        flags.append("emoji-spam")

    letters = [c for c in m.title if c.isalpha()]
    if len(letters) >= 10 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        score -= 0.1
        flags.append("shouting")

    if _PUNCT_SPAM_RE.search(text):
        score -= 0.05

    tokens = tokenize(text)
    if len(tokens) >= 8:
        _top, top_count = Counter(tokens).most_common(1)[0]
        if top_count / len(tokens) > 0.25:
            score -= 0.15
            flags.append("keyword-stuffing")

    if not m.title or _looks_like_address(m.title, m.host):
        score -= 0.2
        flags.append("no-title")
    if not m.snippet:
        score -= 0.1
        flags.append("no-description")

    if is_search_page(m.key):
        score -= 0.2
        flags.append("search-page")

    if m.host in advertisers:
        score -= 0.15
        flags.append("advertiser")

    if "index of /" in text.lower():
        flags.append("directory-listing")

    if m.badge:
        adjust = BADGE_ADJUST.get(m.badge.lower(), 0.0)
        score += adjust
        flags.append(m.badge.lower())

    if m.mirrors:
        flags.append("has-mirrors")

    return max(0.0, min(1.0, score)), flags

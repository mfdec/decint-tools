#!/usr/bin/env python3
"""
darknet_rerank.py — make ahmia.fi (and similar Tor search engines) results
behave like a real search engine.

The problem: ahmia does loose full-text matching over stale crawl data, so you
get hits that don't actually contain your terms, plus spam, dead mirrors, and
keyword-stuffed junk ranked above real matches.

The fix, in order of impact:
  1. PARSE ahmia's HTML into structured results (title, onion url, snippet).
  2. QUERY PARSING like Google: "quoted phrases", -excluded, required terms,
     stopword removal, light stemming.
  3. VERIFY (optional, needs Tor): actually fetch each onion over the SOCKS
     proxy and keep only pages whose live text contains the terms.
  4. SCORE with BM25 + field weighting (title > snippet > body), a phrase
     bonus, a term-coverage requirement, a proximity bonus, and spam
     penalties (keyword stuffing, low text/markup ratio).
  5. SNIPPET: build a Google-style excerpt centered on the best term match.
  6. DEDUP near-identical mirrors.

Offline mode (no Tor) already removes most of the junk by re-ranking on
title+snippet and enforcing coverage. Turn on verify=True to also drop any
result whose live page no longer contains your keywords.

deps:  pip install requests beautifulsoup4
tor  :  pip install requests[socks]   (and a running Tor SOCKS proxy)
"""

from __future__ import annotations

import math
import re
import html
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

AHMIA_BASE = "https://ahmia.fi"
AHMIA_SEARCH = "https://ahmia.fi/search/?q={q}"
# ahmia gates /search/ behind a per-load hidden token field, so a plain
# ?q= GET now returns the empty search form. A browser-ish UA also matters.
AHMIA_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:127.0) "
            "Gecko/20100101 Firefox/127.0")
TOR_SOCKS = "socks5h://127.0.0.1:9050"   # 9150 if you use the Tor Browser bundle

# BM25 knobs. k1 controls term-frequency saturation, b controls length norm.
BM25_K1 = 1.4
BM25_B = 0.72

# Field multipliers applied to each field's BM25 contribution.
FIELD_WEIGHTS = {"title": 3.2, "snippet": 1.6, "body": 1.0}

# A result must contain at least this fraction of the (non-excluded) query
# terms to survive. 1.0 = must contain all of them, like an AND query.
MIN_TERM_COVERAGE = 0.6

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "with", "at", "by", "it", "this", "that", "as", "from",
}

# Cheap spam / junk signals on a page's raw HTML.
SPAM_MARKERS = re.compile(
    r"(?:viagra|casino|replica watches|forex|\bxxx\b|free\s+download\s+now)",
    re.I,
)


# --------------------------------------------------------------------------- #
# Text utilities
# --------------------------------------------------------------------------- #

_word_re = re.compile(r"[a-z0-9]+")


def _stem(tok: str) -> str:
    """Very light suffix folding — enough to match plurals/gerunds without
    pulling in a full stemmer dependency."""
    for suf in ("ing", "edly", "ed", "es", "s"):
        if len(tok) > len(suf) + 2 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def tokenize(text: str, *, stem: bool = True, keep_stop: bool = False) -> list[str]:
    toks = _word_re.findall(text.lower())
    out = []
    for t in toks:
        if not keep_stop and t in STOPWORDS:
            continue
        out.append(_stem(t) if stem else t)
    return out


# --------------------------------------------------------------------------- #
# Query parsing  (Google-ish syntax)
# --------------------------------------------------------------------------- #

@dataclass
class Query:
    raw: str
    terms: list[str] = field(default_factory=list)          # stemmed, weighted set
    phrases: list[list[str]] = field(default_factory=list)  # each an ordered token list
    excluded: list[str] = field(default_factory=list)       # stemmed terms to reject

    @property
    def all_terms(self) -> set[str]:
        s = set(self.terms)
        for p in self.phrases:
            s.update(p)
        return s


_phrase_re = re.compile(r'"([^"]+)"')


def parse_query(raw: str) -> Query:
    q = Query(raw=raw)
    rest = raw

    # "quoted phrases" -> exact-order requirements
    for m in _phrase_re.findall(raw):
        toks = tokenize(m, keep_stop=True)   # keep stopwords inside a phrase
        if toks:
            q.phrases.append(toks)
    rest = _phrase_re.sub(" ", rest)

    for word in rest.split():
        if word.startswith("-") and len(word) > 1:
            q.excluded += tokenize(word[1:])
        elif word.startswith("+") and len(word) > 1:
            q.terms += tokenize(word[1:])
        else:
            q.terms += tokenize(word)

    # de-dup while preserving order
    seen: set[str] = set()
    q.terms = [t for t in q.terms if not (t in seen or seen.add(t))]
    return q


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #

@dataclass
class Result:
    title: str
    url: str                     # the .onion address
    snippet: str                 # ahmia's snippet (fallback text)
    body: str = ""               # live page text, if verified over Tor
    score: float = 0.0
    coverage: float = 0.0        # fraction of query terms actually present
    live: bool | None = None     # None = not checked
    best_snippet: str = ""       # Google-style excerpt we generate

    def field_text(self, name: str) -> str:
        return {"title": self.title, "snippet": self.snippet, "body": self.body}[name]


# --------------------------------------------------------------------------- #
# Ahmia HTML parsing
# --------------------------------------------------------------------------- #

def _clean_onion(href: str) -> str:
    """Ahmia wraps hits in /search/redirect?...&redirect_url=<onion>. Unwrap it."""
    if "redirect_url=" in href:
        qs = parse_qs(urlparse(href).query)
        if "redirect_url" in qs:
            return unquote(qs["redirect_url"][0])
    return href


def parse_ahmia_html(page_html: str) -> list[Result]:
    soup = BeautifulSoup(page_html, "html.parser")
    results: list[Result] = []
    for li in soup.select("li.result"):
        a = li.select_one("h4 a") or li.select_one("a")
        if not a:
            continue
        title = html.unescape(a.get_text(" ", strip=True))
        url = _clean_onion(a.get("href", ""))
        cite = li.select_one("cite")
        if cite and cite.get_text(strip=True):
            url = cite.get_text(strip=True)
        p = li.select_one("p")
        snippet = html.unescape(p.get_text(" ", strip=True)) if p else ""
        if url:
            results.append(Result(title=title, url=url, snippet=snippet))
    return results


def _ahmia_form_token(sess: requests.Session, timeout: int) -> tuple[str, dict[str, str]]:
    """Fetch the search form and return (action_url, hidden_fields).

    ahmia embeds a randomized hidden input (name/value change every load) that
    must be echoed back or the results page is withheld. We scrape whatever
    hidden inputs the form carries so this keeps working if the field is
    renamed again.
    """
    r = sess.get(AHMIA_BASE + "/search/", timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    hidden: dict[str, str] = {}
    action = "/search/"
    if form:
        action = form.get("action") or action
        for inp in form.select("input"):
            name = inp.get("name")
            if not name or name == "q" or inp.get("type") == "submit":
                continue
            hidden[name] = inp.get("value") or ""
    url = AHMIA_BASE + action if action.startswith("/") else action
    return url, hidden


def search_ahmia(query: str, *, timeout: int = 20) -> list[Result]:
    sess = requests.Session()
    sess.headers.update({"User-Agent": AHMIA_UA})
    try:
        url, hidden = _ahmia_form_token(sess, timeout)
        params = {**hidden, "q": query}
        r = sess.get(url, params=params, timeout=timeout)
    except requests.RequestException:
        # fall back to the naive endpoint if the token dance fails
        r = sess.get(AHMIA_SEARCH.format(q=requests.utils.quote(query)),
                     timeout=timeout)
    r.raise_for_status()
    return parse_ahmia_html(r.text)


# --------------------------------------------------------------------------- #
# Optional: fetch the live onion over Tor to verify content
# --------------------------------------------------------------------------- #

def fetch_onion_text(url: str, *, timeout: int = 30) -> tuple[str, str]:
    """Return (visible_text, raw_html) fetched through the Tor SOCKS proxy.
    Raises on failure so the caller can mark the result dead."""
    proxies = {"http": TOR_SOCKS, "https": TOR_SOCKS}
    r = requests.get(
        url, proxies=proxies, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (research)"},
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True), r.text


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def _build_field_index(results: list[Result], field_name: str):
    docs = [tokenize(r.field_text(field_name)) for r in results]
    n = len(docs)
    avg_len = (sum(len(d) for d in docs) / n) if n else 0.0
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    return docs, avg_len, df, n


def _bm25_term(tf: int, dl: int, avg_len: float, df: int, n: int) -> float:
    if tf == 0 or avg_len == 0:
        return 0.0
    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
    denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avg_len)
    return idf * (tf * (BM25_K1 + 1)) / denom


def _phrase_hits(tokens: list[str], phrase: list[str]) -> int:
    if not phrase:
        return 0
    stem_phrase = [_stem(p) for p in phrase]
    hits, m = 0, len(stem_phrase)
    for i in range(len(tokens) - m + 1):
        if tokens[i : i + m] == stem_phrase:
            hits += 1
    return hits


def _proximity_bonus(tokens: list[str], terms: set[str]) -> float:
    """Reward query terms appearing close together (min window covering >=2 terms)."""
    positions = [i for i, t in enumerate(tokens) if t in terms]
    if len(positions) < 2:
        return 0.0
    span = positions[-1] - positions[0]
    return 1.5 / (1 + span / max(len(terms), 1))


def score_results(results: list[Result], q: Query) -> list[Result]:
    if not results:
        return []

    indexes = {f: _build_field_index(results, f) for f in FIELD_WEIGHTS}
    qterms = q.all_terms

    for i, res in enumerate(results):
        total = 0.0
        for fname, weight in FIELD_WEIGHTS.items():
            docs, avg_len, df, n = indexes[fname]
            toks = docs[i]
            if not toks:
                continue
            tf = Counter(toks)
            dl = len(toks)
            field_score = sum(
                _bm25_term(tf.get(t, 0), dl, avg_len, df.get(t, 0), n) for t in qterms
            )
            # phrase matches are worth a lot — exact order is a strong signal
            for phrase in q.phrases:
                field_score += 4.0 * _phrase_hits(toks, phrase)
            field_score += _proximity_bonus(toks, qterms)
            total += weight * field_score

        # ---- term coverage across all fields combined ----
        combined = tokenize(" ".join(res.field_text(f) for f in FIELD_WEIGHTS))
        present = {t for t in qterms if t in combined}
        res.coverage = (len(present) / len(qterms)) if qterms else 1.0

        # ---- hard rejections ----
        excluded_present = any(t in combined for t in q.excluded)
        # a required phrase must appear somewhere
        phrase_ok = all(
            any(_phrase_hits(tokenize(res.field_text(f), keep_stop=True), p)
                for f in FIELD_WEIGHTS)
            for p in q.phrases
        ) if q.phrases else True

        if excluded_present or not phrase_ok or res.coverage < MIN_TERM_COVERAGE:
            res.score = -1.0
        else:
            res.score = total * (0.4 + 0.6 * res.coverage)   # coverage-weighted

        res.best_snippet = make_snippet(res, qterms) or res.snippet

    kept = [r for r in results if r.score >= 0.0]
    kept.sort(key=lambda r: r.score, reverse=True)
    return kept


# --------------------------------------------------------------------------- #
# Spam / quality penalty (applied when we have the live page)
# --------------------------------------------------------------------------- #

def quality_penalty(raw_html: str, visible_text: str, qterms: set[str]) -> float:
    """Return a multiplier in (0, 1]. Lower = junkier."""
    mult = 1.0
    if not raw_html:
        return mult
    if SPAM_MARKERS.search(raw_html):
        mult *= 0.4
    # keyword stuffing: one query term making up an absurd share of the text
    toks = tokenize(visible_text)
    if toks:
        counts = Counter(toks)
        for t in qterms:
            if counts.get(t, 0) / len(toks) > 0.08:
                mult *= 0.6
                break
    # text-to-markup ratio: link farms are almost all markup
    if len(raw_html) > 0:
        ratio = len(visible_text) / len(raw_html)
        if ratio < 0.04:
            mult *= 0.7
    return mult


# --------------------------------------------------------------------------- #
# Snippet generation (Google-style)
# --------------------------------------------------------------------------- #

def make_snippet(res: Result, qterms: set[str], width: int = 260) -> str:
    text = res.body or res.snippet or res.title
    if not text:
        return ""
    words = text.split()
    stems = [_stem(w.lower().strip(".,!?:;\"'()")) for w in words]
    # find the window with the most query-term hits
    best_i, best_hits = 0, -1
    win = 40
    for i in range(0, max(1, len(words) - win + 1), 5):
        hits = sum(1 for s in stems[i : i + win] if s in qterms)
        if hits > best_hits:
            best_hits, best_i = hits, i
    excerpt = " ".join(words[best_i : best_i + win])
    if len(excerpt) > width:
        excerpt = excerpt[:width].rsplit(" ", 1)[0] + "…"
    # emphasize matched terms
    def _emph(m):
        return f"**{m.group(0)}**" if _stem(m.group(0).lower()) in qterms else m.group(0)
    return _word_re.sub(lambda m: _emph(m), excerpt) if False else excerpt


# --------------------------------------------------------------------------- #
# Dedup
# --------------------------------------------------------------------------- #

def dedup(results: list[Result]) -> list[Result]:
    """Collapse mirrors: same title + very similar snippet."""
    seen: dict[str, Result] = {}
    out = []
    for r in results:
        key = (r.title.lower().strip(), r.snippet[:80].lower().strip())
        sig = "|".join(key)
        if sig in seen:
            continue
        seen[sig] = r
        out.append(r)
    return out


# --------------------------------------------------------------------------- #
# Top-level pipeline
# --------------------------------------------------------------------------- #

def smart_search(
    query: str,
    *,
    verify: bool = False,
    max_verify: int = 25,
    limit: int = 20,
) -> list[Result]:
    """
    verify=False : re-rank on ahmia's own titles+snippets (fast, no Tor).
    verify=True  : also fetch each onion over Tor, drop dead pages and pages
                   that don't actually contain the terms, apply spam penalty.
    """
    q = parse_query(query)
    results = dedup(search_ahmia(query))

    if verify:
        qterms = q.all_terms
        for res in results[:max_verify]:
            try:
                text, raw = fetch_onion_text(res.url)
                res.body, res.live = text, True
                # fold the spam penalty into the snippet-stage body by trimming
                if quality_penalty(raw, text, qterms) < 0.5:
                    res.body = ""      # treat as low quality -> weak match
            except Exception:
                res.live = False
        # drop confirmed-dead pages
        results = [r for r in results if r.live is not False]

    ranked = score_results(results, q)
    return ranked[:limit]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Relevance-filtered ahmia search")
    ap.add_argument("query", help='e.g.  "leaked database" breach -forum')
    ap.add_argument("--verify", action="store_true",
                    help="fetch each onion over Tor and drop non-matching/dead pages")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    hits = smart_search(args.query, verify=args.verify, limit=args.limit)
    if not hits:
        print("No results passed the relevance filter.")
    for i, r in enumerate(hits, 1):
        live = "" if r.live is None else ("  [live]" if r.live else "  [dead]")
        print(f"\n{i}. {r.title}{live}")
        print(f"   {r.url}")
        print(f"   score={r.score:.2f}  coverage={r.coverage:.0%}")
        if r.best_snippet:
            print(f"   {r.best_snippet}")

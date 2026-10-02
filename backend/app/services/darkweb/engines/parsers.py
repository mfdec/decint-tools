"""HTML/JSON result parsers, one per engine family, plus a generic fallback.

A parser returns a list of ParsedItem when it recognises the page layout (possibly empty: the
engine genuinely found nothing), or None when the expected structure is missing entirely. None
makes the engine fall back to the generic parser, which keeps working when a site redesigns.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup, NavigableString, Tag

from ..onion import find_onion_urls, host_of, onion_host
from ..text import clean_text, has_alnum
from ..urls import canonicalize, unwrap


@dataclass
class ParsedItem:
    title: str
    url: str
    snippet: str = ""
    sponsored: bool = False
    badge: str | None = None
    last_seen: datetime | None = None


@dataclass
class ParseContext:
    base_url: str
    own_hosts: set[str] = field(default_factory=set)


Parser = Callable[[str, ParseContext], "list[ParsedItem] | None"]

_NOISE_ANCHORS = re.compile(
    r"^(?:cached|report|verify|next|prev(?:ious)?|more|copy|open…?|details|«|»|\d+)$", re.I
)
_BLOCK_TAGS = ("li", "article", "tr", "dd", "div", "p", "section", "td", "table")


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _text(node: Tag | None, max_len: int | None = 400) -> str:
    return clean_text(node.get_text(" ", strip=True), max_len) if node else ""


def _resolve(href: str, ctx: ParseContext) -> str:
    return unwrap(href, ctx.base_url, ctx.own_hosts)


def _accept(url: str, ctx: ParseContext) -> bool:
    host = host_of(url)
    return bool(host) and host not in ctx.own_hosts


def _block_text(anchor: Tag, title: str) -> str:
    """Text of the nearest container that looks like one result (not the whole page)."""
    node: Tag | None = anchor
    best = ""
    for _ in range(4):
        node = node.parent if node is not None else None
        if node is None or node.name in ("body", "html", "[document]"):
            break
        if node.name not in _BLOCK_TAGS:
            continue
        text = clean_text(node.get_text(" ", strip=True), None)
        if len(text) > 1500:
            break
        best = text
        if len(text) > len(title) + 40:
            break
    snippet = best.replace(title, "", 1).strip(" -|·:") if title else best
    return clean_text(snippet, 400)


# ------------------------------------------------------------------------------------------
# Generic fallback
# ------------------------------------------------------------------------------------------


def parse_generic(html: str, ctx: ParseContext) -> list[ParsedItem]:
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "head", "nav", "footer", "form", "header"]):
        tag.decompose()
    items: list[ParsedItem] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        target = _resolve(href, ctx)
        host = onion_host(target)
        if not host or host in ctx.own_hosts:
            continue
        key = canonicalize(target)
        title = _text(anchor, 200) or clean_text(anchor.get("title"), 200)
        if key in seen:
            continue
        if _NOISE_ANCHORS.match(title or ""):
            title = ""
        if len(title) < 4 or not has_alnum(title):
            heading = anchor.find_parent(_BLOCK_TAGS)
            heading = heading.find(["h1", "h2", "h3", "h4", "h5", "b", "strong"]) if heading else None
            title = _text(heading, 200) if heading else ""
        if len(title) < 4 or not has_alnum(title):
            title = host
        seen.add(key)
        items.append(ParsedItem(title=title, url=target, snippet=_block_text(anchor, title)))
    if not items:
        for url in find_onion_urls(html):
            host = onion_host(url)
            if host and host not in ctx.own_hosts and canonicalize(url) not in seen:
                seen.add(canonicalize(url))
                items.append(ParsedItem(title=host, url=url))
    return items


# ------------------------------------------------------------------------------------------
# Engine-specific parsers
# ------------------------------------------------------------------------------------------

_TIMESINCE_RE = re.compile(r"(\d+)\s*(year|month|week|day|hour|minute)", re.I)
_UNIT_DAYS = {"year": 365, "month": 30, "week": 7, "day": 1, "hour": 1 / 24, "minute": 1 / 1440}


def _parse_last_seen(span: Tag | None) -> datetime | None:
    if span is None:
        return None
    raw = (span.get("data-timestamp") or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    # Django renders "{{ updated_on|timesince }}" inside the span, e.g. "2 days, 3 hours".
    match = _TIMESINCE_RE.search(span.get_text(" ", strip=True))
    if match:
        days = int(match.group(1)) * _UNIT_DAYS[match.group(2).lower()]
        return datetime.now(UTC) - timedelta(days=days)
    return None


def parse_ahmia(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    page = soup.select_one("#ahmiaResultsPage")
    if page is None:
        return None
    items = []
    for li in page.select("ol.searchResults li.result"):
        anchor = li.select_one("h4 a")
        if anchor is None or not anchor.get("href"):
            continue
        url = _resolve(anchor["href"], ctx)
        desc = _text(li.find("p"))
        if desc.lower() == "no description provided":
            desc = ""
        items.append(
            ParsedItem(
                title=_text(anchor, 200),
                url=url,
                snippet=desc,
                last_seen=_parse_last_seen(li.select_one("span.lastSeen")),
            )
        )
    return items


def parse_tor66(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    rule = soup.find("hr")
    if rule is None:
        return None
    items = []
    for bold in rule.find_all_next("b"):
        anchor = bold.find("a", href=True)
        if anchor is None:
            continue
        url = _resolve(anchor["href"], ctx)
        if not _accept(url, ctx):
            continue
        parts = []
        for sib in bold.next_siblings:
            if isinstance(sib, Tag) and (sib.name == "b" or sib.find("b")):
                break
            parts.append(str(sib) if isinstance(sib, NavigableString) else sib.get_text(" "))
        items.append(ParsedItem(title=_text(anchor, 200), url=url, snippet=clean_text(" ".join(parts))))
    return items


def parse_onionland(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    blocks = soup.select(".result-block")
    if not blocks:
        return None if not soup.select_one(".search-status, .search-results") else []
    items = []
    for block in blocks:
        anchor = block.select_one(".title a")
        link_box = block.select_one(".link")
        if anchor is None or link_box is None:
            continue
        sponsored = (
            anchor.get("data-category") == "sponsored-text"
            or "/ads/" in (anchor.get("href") or "")
            or link_box.select_one(".label-ad") is not None
        )
        for label in link_box.select(".label-ad"):
            label.decompose()
        url = clean_text(link_box.get_text(" ", strip=True), None)
        if not url.lower().startswith("http"):
            url = "http://" + url
        items.append(
            ParsedItem(title=_text(anchor, 200), url=url, snippet=_text(block.select_one(".desc")), sponsored=sponsored)
        )
    return items


def parse_tordex(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    headings = soup.select(".container h5")
    if not headings:
        return None
    items = []
    for h5 in headings:
        anchor = h5.find("a", href=True)
        if anchor is None:
            continue
        url = _resolve(anchor["href"], ctx)
        if not _accept(url, ctx):
            continue
        items.append(ParsedItem(title=_text(anchor, 200), url=url, snippet=_text(h5.find_next_sibling("p"))))
    return items


def parse_torch(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    blocks = soup.select("div.result")
    items = []
    if blocks:
        for block in blocks:
            anchor = block.select_one("h5 a[href]") or block.find("a", href=True)
            if anchor is None:
                continue
            url = _resolve(anchor["href"], ctx)
            if not _accept(url, ctx):
                continue
            title = _text(block.find("h5"), 200) or _text(anchor, 200)
            items.append(ParsedItem(title=title, url=url, snippet=_text(block.find("p"))))
        return items
    # Xapian Omega layout (legacy Torch): one result per table row.
    rows = soup.select("table tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        anchor = cells[1].find("a", href=True)
        if anchor is None:
            continue
        url = _resolve(anchor["href"], ctx)
        if not _accept(url, ctx):
            continue
        title = _text(cells[1].find("b"), 200) or _text(anchor, 200)
        items.append(ParsedItem(title=title, url=url, snippet=_text(cells[1].find("small"))))
    if items:
        return items
    if re.search(r"<b>\s*0\s*</b>\s*results", html, re.I):
        return []
    return None


def parse_haystak(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    blocks = soup.select("div.result")
    if not blocks:
        return None
    items = []
    for block in blocks:
        anchor = block.select_one("b a[href]") or block.find("a", href=True)
        if anchor is None:
            continue
        url = _resolve(anchor["href"], ctx)
        if not onion_host(url):
            shown = _text(block.find("i"), None)
            url = shown if shown.startswith("http") else (f"http://{shown}" if shown else url)
        if not _accept(url, ctx):
            continue
        title = _text(anchor, 200)
        snippet = _text(block, 600).replace(title, "", 1)
        for italic in block.find_all("i"):
            snippet = snippet.replace(_text(italic, None), "")
        items.append(ParsedItem(title=title, url=url, snippet=clean_text(snippet)))
    return items


def parse_vormweb(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    boxes = soup.select("div.query-box")
    if not boxes:
        return None
    items = []
    for box in boxes:
        # Each box also has #verify and #report links; the report link embeds the target URL.
        anchor = box.find("a", id="urllink") or box.find("a", href=True)
        if anchor is None or not anchor.get("href"):
            continue
        url = _resolve(anchor["href"], ctx)
        badge_node = box.select_one("p > b")
        badge = _text(badge_node, 40) or None
        desc = box.select_one("li i") or box.find("li")
        items.append(ParsedItem(title=_text(anchor, 200), url=url, snippet=_text(desc), badge=badge))
    return items


def parse_onionsearchengine(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    container = soup.select_one("#results-container, ul.results-list")
    if container is None:
        return [] if soup.select_one(".no-results") else None
    items = []
    for li in container.select("li.result-item"):
        if "ad-result-item" in (li.get("class") or []):
            continue
        anchor = li.select_one("h3.result-title a[href]")
        if anchor is None:
            continue
        url = _resolve(anchor["href"], ctx)
        items.append(
            ParsedItem(title=_text(anchor, 200), url=url, snippet=_text(li.select_one("p.result-snippet")))
        )
    return items


def parse_onionengine(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    articles = soup.select("article.res")
    if not articles:
        return [] if soup.select_one("main .meta") else None
    items = []
    for art in articles:
        anchor = art.select_one("h3 a[href]")
        if anchor is None:
            continue
        copy = art.select_one("button.copy[data-url]")
        url = copy["data-url"] if copy else _resolve(anchor["href"], ctx)
        items.append(ParsedItem(title=_text(anchor, 200), url=url, snippet=_text(art.find("p"))))
    return items


def parse_oss(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    anchors = soup.select(".osscmnrdr.ossfieldrdr1 a[href]")
    if not anchors:
        return None if not soup.select_one(".ossnumfound") else []
    items = []
    for anchor in anchors:
        url = _resolve(anchor["href"], ctx)
        if not _accept(url, ctx):
            continue
        container = anchor.find_parent(class_="oss-one-result") or anchor.parent
        snippet = _text(container.select_one(".ossfieldrdr2")) if container else ""
        items.append(ParsedItem(title=_text(anchor, 200), url=url, snippet=snippet))
    return items


def parse_submarine(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    lists = soup.select("ul#page")
    if not lists:
        return None
    items = []
    for ul in lists:
        title, url = "", ""
        for anchor in ul.find_all("a", href=True):
            text = _text(anchor, 200)
            if text.lower().startswith("http"):
                url = _resolve(anchor["href"], ctx)
            elif text:
                title = text
        if url and _accept(url, ctx):
            items.append(ParsedItem(title=title or host_of(url), url=url, snippet=_block_text(ul, title)))
    return items


def parse_phobos(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    anchors = soup.select(".serp a.titles[href], .serp .titles a[href]")
    if not anchors:
        return None if not soup.select_one(".serp") else []
    items = []
    for anchor in anchors:
        url = _resolve(anchor["href"], ctx)
        if _accept(url, ctx):
            title = _text(anchor, 200)
            items.append(ParsedItem(title=title, url=url, snippet=_block_text(anchor, title)))
    return items


def parse_evo(html: str, ctx: ParseContext) -> list[ParsedItem] | None:
    soup = soup_of(html)
    anchors = soup.select("#results .title a[href]")
    if not anchors:
        return None if not soup.select_one("#results") else []
    items = []
    for anchor in anchors:
        url = _resolve(anchor["href"], ctx)
        if _accept(url, ctx):
            title = _text(anchor, 200)
            items.append(ParsedItem(title=title, url=url, snippet=_block_text(anchor, title)))
    return items


_JSON_URL_KEYS = ("url", "address", "onion", "link", "href")
_JSON_TITLE_KEYS = ("title", "name", "label")
_JSON_DESC_KEYS = ("description", "desc", "snippet", "summary", "meta")


def parse_json(text: str, ctx: ParseContext) -> list[ParsedItem] | None:
    """Tolerant walker for JSON APIs: any object with an onion-looking URL field is a result."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    items: list[ParsedItem] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            url = next((str(node[k]) for k in _JSON_URL_KEYS if node.get(k)), "")
            if url and ".onion" in url:
                url = url if url.startswith("http") else f"http://{url}"
                title = next((str(node[k]) for k in _JSON_TITLE_KEYS if node.get(k)), "")
                desc = next((str(node[k]) for k in _JSON_DESC_KEYS if node.get(k)), "")
                items.append(
                    ParsedItem(
                        title=clean_text(title or desc, 200) or host_of(url),
                        url=url,
                        snippet=clean_text(desc) if title else "",
                    )
                )
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return items


PARSERS: dict[str, Parser] = {
    "generic": parse_generic,
    "ahmia": parse_ahmia,
    "tor66": parse_tor66,
    "onionland": parse_onionland,
    "tordex": parse_tordex,
    "torch": parse_torch,
    "haystak": parse_haystak,
    "vormweb": parse_vormweb,
    "onionsearchengine": parse_onionsearchengine,
    "onionengine": parse_onionengine,
    "oss": parse_oss,
    "submarine": parse_submarine,
    "phobos": parse_phobos,
    "evo": parse_evo,
    "json": parse_json,
}


def parse_page(parser_name: str, html: str, ctx: ParseContext) -> tuple[list[ParsedItem], str]:
    """Run the engine's parser, falling back to the generic one if the layout is unrecognised.

    Returns the items and the name of the parser that produced them.
    """
    parser = PARSERS.get(parser_name, parse_generic)
    items = parser(html, ctx)
    if items is None:
        if parser is parse_json:
            return [], parser_name
        return parse_generic(html, ctx), "generic"
    return items, parser_name


_CHALLENGE_MARKERS = (
    "cf-chl", "challenge-platform/h/", "just a moment...", "captcha", "are you human",
    "ddos protection", "checking your browser",
)


def looks_blocked(html: str) -> bool:
    """Heuristic: a challenge/captcha interstitial instead of a results page."""
    lowered = html[:20000].lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


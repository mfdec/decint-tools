"""URL unwrapping (engine redirect wrappers) and canonicalisation for de-duplication."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from .onion import ONION_URL_RE, onion_host

# Query keys that search engines use to carry the real destination of a result link.
REDIRECT_KEYS = (
    "redirect_url", "url", "u", "l", "link", "redirect", "target", "goto", "dest",
    "destination", "to", "site", "address", "q",
)
TRACKING_KEYS = frozenset(
    {
        "ref", "ref_src", "referrer", "sid", "phpsessid", "jsessionid", "sessionid", "fbclid",
        "gclid", "yclid", "mc_cid", "mc_eid", "_ga", "aff", "aff_id", "affiliate",
    }
)
_AHMIA_TAIL_RE = re.compile(r"(?:^|&)redirect_url=(.*)$", re.IGNORECASE | re.DOTALL)
_BARE_ONION_RE = re.compile(r"^(?:[a-z0-9-]+\.)*[a-z2-7]{56}\.onion(?:[:/?#].*)?$", re.IGNORECASE)
_INDEX_RE = re.compile(r"/(?:index|default)\.(?:html?|php|aspx?)$", re.IGNORECASE)
_PATH_SAFE = "/:@!$&'()*+,;=-._~%"


def _looks_like_url(value: str) -> str | None:
    value = value.strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    if _BARE_ONION_RE.match(value):
        return "http://" + value
    return None


def extract_embedded_url(url: str) -> str | None:
    """Return the destination URL hidden in a redirect wrapper's query string, if any."""
    try:
        query = urlsplit(url).query
    except ValueError:
        return None
    if not query:
        return None
    # Ahmia puts redirect_url last and does not encode it, so the target may contain '&'.
    tail = _AHMIA_TAIL_RE.search(query)
    if tail and (found := _looks_like_url(unquote(tail.group(1)))):
        return found
    pairs = parse_qsl(query, keep_blank_values=False)
    for key, value in pairs:
        if key.lower() in REDIRECT_KEYS:
            decoded = unquote(value) if "%" in value else value
            if found := _looks_like_url(decoded):
                return found
    for _key, value in pairs:
        decoded = unquote(value) if "%" in value else value
        if ONION_URL_RE.fullmatch(decoded.strip()) or _BARE_ONION_RE.match(decoded.strip()):
            return _looks_like_url(decoded)
    return None


def unwrap(href: str, base: str | None = None, own_hosts: Iterable[str] = ()) -> str:
    """Resolve `href` against `base` and peel off engine redirect wrappers.

    Direct links to onion services other than the engine itself are returned unchanged, so a
    legitimate site URL that happens to carry another URL in its query is not rewritten.
    """
    own = {h.lower() for h in own_hosts}
    if base and (base_host := urlsplit(base).hostname):
        own.add(base_host.lower())  # links back to the page's own host are engine-internal
    url = urljoin(base, href.strip()) if base else href.strip()
    for _ in range(3):
        host = onion_host(url)
        if host and host not in own:
            return url
        embedded = extract_embedded_url(url)
        if not embedded or embedded == url:
            return url
        url = embedded
    return url


def canonicalize(url: str) -> str:
    """Normalise a URL so the same page reported by different engines compares equal."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")) and (bare := _looks_like_url(url)):
        url = bare
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    scheme = (parts.scheme or "http").lower()
    if host.endswith(".onion"):
        scheme = "http"  # onion services are reached the same way over http and https
    netloc = host
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    params = _strip_tracking(parts.query)
    if not params:
        path = _INDEX_RE.sub("/", path)  # /index.php == / only when there is no query string
    path = quote(unquote(path), safe=_PATH_SAFE).rstrip("/")
    query = urlencode(sorted(params))
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def _strip_tracking(query: str) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS
    ]


def display_url(url: str) -> str:
    """The URL as the engine reported it, minus fragment and tracking parameters."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    params = _strip_tracking(parts.query)
    query = urlencode(params) if params else ""
    if parts.query and len(params) == len(parse_qsl(parts.query, keep_blank_values=True)):
        query = parts.query  # nothing removed: keep the original encoding/order
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))

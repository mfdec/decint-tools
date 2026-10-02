"""HTTP transport: Tor SOCKS5h with per-engine circuit isolation, size caps and redirect guards.

Also contains ReplayFetcher, which serves saved engine pages from disk for demos and tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

try:  # installed with httpx[socks]; without it Tor mode cannot work, but nothing else should break
    from socksio.exceptions import SOCKSError
except ImportError:  # pragma: no cover

    class SOCKSError(Exception):  # type: ignore[no-redef]
        """Stand-in so the handler below is valid when socksio is missing."""


from .config import Settings
from .onion import host_of, onion_host


@dataclass
class FetchResponse:
    status: int
    url: str
    text: str
    elapsed: float


class FetchError(Exception):
    """A failed request. `kind` is one of timeout|connect|proxy|http|redirect|error."""

    def __init__(self, kind: str, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable


class Fetcher(Protocol):
    async def get(
        self, url: str, *, engine: str, kind: str = "page1", timeout: float | None = None
    ) -> FetchResponse: ...

    async def aclose(self) -> None: ...


BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
}


def redirect_allowed(current: str, target: str) -> bool:
    """Where an engine may send us.

    From an onion engine: only to another onion address, never out to the clearnet.
    From a clearnet gateway (gateway mode, requests leave this server directly): only
    within the same site (`www.` twin included). A gateway that is hijacked or compromised
    must not be able to bounce the server onto an internal address or another host.
    """
    if urlsplit(target).scheme not in ("http", "https"):
        return False
    if onion_host(current):
        return bool(onion_host(target))
    strip = lambda h: h.removeprefix("www.")  # noqa: E731
    return bool(host_of(target)) and strip(host_of(target)) == strip(host_of(current))


class HttpFetcher:
    """httpx-based fetcher. In `tor` mode every engine gets its own SOCKS credentials, which Tor's
    default IsolateSOCKSAuth turns into a separate circuit, so engine operators cannot link the
    queries you send to the other engines."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._nonce = secrets.token_hex(6)
        self._sem = asyncio.Semaphore(max(1, settings.max_concurrency))

    def proxy_url(self, engine: str) -> str | None:
        if self.settings.transport != "tor":
            return None
        proxy = self.settings.tor_proxy
        if not self.settings.isolate_circuits:
            return proxy
        parts = urlsplit(proxy)
        user = quote(f"{engine}-{self._nonce}", safe="")
        netloc = f"{user}:x@{parts.hostname}:{parts.port or 9050}"
        return urlunsplit((parts.scheme, netloc, "", "", ""))

    def _client(self, engine: str) -> httpx.AsyncClient:
        key = engine if self.settings.isolate_circuits else "_shared"
        client = self._clients.get(key)
        if client is None:
            kwargs: dict = {
                "headers": {"User-Agent": self.settings.user_agent, **BROWSER_HEADERS},
                "follow_redirects": False,
                "timeout": httpx.Timeout(self.settings.read_timeout, connect=self.settings.connect_timeout),
                # Never let HTTP(S)_PROXY env vars route Tor traffic around Tor.
                "trust_env": self.settings.transport == "direct",
                "limits": httpx.Limits(max_connections=8, max_keepalive_connections=4),
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            elif proxy := self.proxy_url(engine):
                kwargs["proxy"] = proxy
            client = self._clients[key] = httpx.AsyncClient(**kwargs)
        return client

    async def get(
        self, url: str, *, engine: str, kind: str = "page1", timeout: float | None = None
    ) -> FetchResponse:
        client = self._client(engine)
        limit = httpx.Timeout(timeout or self.settings.read_timeout, connect=self.settings.connect_timeout)
        attempt = 0
        while True:
            try:
                async with self._sem:
                    return await self._get_once(client, url, limit)
            except FetchError as exc:
                if exc.retryable and attempt < self.settings.retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise

    async def _get_once(self, client: httpx.AsyncClient, url: str, limit: httpx.Timeout) -> FetchResponse:
        start = time.monotonic()
        current = url
        try:
            for _ in range(5):
                async with client.stream("GET", current, timeout=limit) as resp:
                    if resp.is_redirect and (location := resp.headers.get("location")):
                        nxt = urljoin(current, location)
                        if not redirect_allowed(current, nxt):
                            raise FetchError("redirect", f"refused redirect to {host_of(nxt) or nxt[:40]}")
                        current = nxt
                        continue
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= self.settings.max_body_bytes:
                            break  # results sit at the top of the page; ignore the rest
                    text = body.decode(resp.encoding or "utf-8", errors="replace")
                    if resp.status_code >= 400:
                        raise FetchError(
                            "http",
                            f"HTTP {resp.status_code}",
                            status=resp.status_code,
                            retryable=resp.status_code in (500, 502, 503, 504),
                        )
                    return FetchResponse(resp.status_code, str(resp.url), text, time.monotonic() - start)
            raise FetchError("redirect", "too many redirects")
        except httpx.ConnectTimeout as exc:
            raise FetchError("timeout", "connect timeout") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise FetchError("timeout", "read timeout", retryable=True) from exc
        except httpx.ProxyError as exc:
            raise FetchError("proxy", f"proxy error: {exc}") from exc
        except SOCKSError as exc:
            # Tor answers a dead or unreachable onion service with its own SOCKS error codes
            # (0xF0-0xF7: descriptor not found, introduction failed, ...). socksio cannot parse
            # them and raises ProtocolError("Malformed reply") instead of httpx.ProxyError, which
            # would escape as a crash and skip this engine's remaining mirrors.
            raise FetchError("proxy", f"onion service unreachable ({exc})") from exc
        except httpx.ConnectError as exc:
            raise FetchError("connect", f"connect error: {exc}") from exc
        except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
            raise FetchError("connect", f"connection dropped: {exc}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise FetchError("error", f"{type(exc).__name__}: {exc}") from exc

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        await asyncio.gather(*(c.aclose() for c in clients), return_exceptions=True)


class ReplayFetcher:
    """Serves `<dir>/<engine>.<kind>.(html|json|txt)` instead of the network (`kind` = page1,
    page2, prep, banlist). `<engine>.html` also works for page1. Missing files act like a dead
    engine. Used for offline demos (ONIONSCOPE_REPLAY_DIR) and end-to-end tests."""

    EXTENSIONS = (".html", ".json", ".txt")

    def __init__(self, directory: Path, delay: float = 0.0):
        self.directory = Path(directory)
        self.delay = delay

    def _find(self, engine: str, kind: str) -> Path | None:
        stems = [f"{engine}.{kind}"] + ([engine] if kind == "page1" else [])
        for stem in stems:
            for ext in self.EXTENSIONS:
                path = self.directory / f"{stem}{ext}"
                if path.is_file():
                    return path
        return None

    def has_engine(self, engine: str) -> bool:
        return self._find(engine, "page1") is not None

    async def get(
        self, url: str, *, engine: str, kind: str = "page1", timeout: float | None = None
    ) -> FetchResponse:
        if self.delay:
            await asyncio.sleep(self.delay * (1 + (zlib.crc32(engine.encode()) % 7) / 3))
        path = self._find(engine, kind)
        if path is None:
            raise FetchError("http", f"no replay fixture for {engine}/{kind}", status=404)
        return FetchResponse(200, url, path.read_text(encoding="utf-8", errors="replace"), self.delay)

    async def aclose(self) -> None:
        return None


def make_fetcher(settings: Settings) -> Fetcher:
    if settings.replay_dir:
        return ReplayFetcher(settings.replay_dir, settings.replay_delay)
    return HttpFetcher(settings)


async def proxy_reachable(settings: Settings, timeout: float = 3.0) -> bool:
    """Is anything listening on the Tor SOCKS port? (Cheap check before fanning out.)"""
    if settings.transport != "tor" or settings.replay_dir:
        return True
    parts = urlsplit(settings.tor_proxy)
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parts.hostname or "127.0.0.1", parts.port or 9050), timeout
        )
    except (TimeoutError, OSError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def tor_check(settings: Settings) -> dict:
    """Ask check.torproject.org whether our traffic exits through Tor."""
    if not await proxy_reachable(settings):
        return {"ok": False, "is_tor": False, "error": f"nothing listening at {settings.tor_proxy}"}
    fetcher = HttpFetcher(settings)
    try:
        resp = await fetcher.get("https://check.torproject.org/api/ip", engine="torcheck", timeout=30)
        data = json.loads(resp.text)
        return {"ok": True, "is_tor": bool(data.get("IsTor")), "ip": data.get("IP"), "transport": settings.transport}
    except (FetchError, ValueError) as exc:
        return {"ok": False, "is_tor": False, "error": str(exc), "transport": settings.transport}
    finally:
        await fetcher.aclose()

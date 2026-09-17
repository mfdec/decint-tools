#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║              DECINT // DARK WEB KEYWORD SEARCH v2.0.0               ║
║           OSINT across .onion indexes & paste repositories           ║
╚══════════════════════════════════════════════════════════════════════╝

WHAT CHANGED IN v2.0.0
    * Endpoints moved out of code into an editable JSON config
    * Per-endpoint health tracking + circuit breaker (dead mirrors self-demote)
    * Concurrent source queries with per-source Tor stream isolation
    * Adaptive HTML parsing (heuristic, survives layout changes)
    * Relevance scoring + cross-source corroboration boost
    * Entity extraction (onion v3/v2, email, BTC/ETH/XMR, PGP markers)
    * Evidence manifest: SHA-256 over canonical result set
    * --self-test runs offline; no Tor required to validate an install

REQUIREMENTS
    pip install "requests[socks]>=2.31,<3" "beautifulsoup4>=4.12,<5"

TOR
    sudo apt install tor && sudo systemctl start tor
    Default SOCKS5 proxy: 127.0.0.1:9050
    Stream isolation requires IsolateSOCKSAuth (Tor default: on)

QUICK START
    python3 decint_darkweb_search.py --self-test
    python3 decint_darkweb_search.py --probe
    python3 decint_darkweb_search.py "keyword"
    python3 decint_darkweb_search.py "keyword" --json --out results.json

EXIT CODES
    0  success (results found)
    1  runtime error
    2  completed but zero results
    3  Tor unavailable
    4  configuration error
    130 interrupted (partial results still written)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import signal
import string
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

__version__ = "2.0.0"
__tool__ = "DECINT Dark Web Keyword Search"


# ══════════════════════════════════════════════════════════════════════════════
#  DEPENDENCY GUARD
#  Fail with an actionable message rather than a traceback. Clients running a
#  packaged install should never see an ImportError stack.
# ══════════════════════════════════════════════════════════════════════════════

_MISSING: list[str] = []
try:
    import requests
    from requests.adapters import HTTPAdapter
except ImportError:
    _MISSING.append('"requests[socks]>=2.31,<3"')

try:
    from bs4 import BeautifulSoup
except ImportError:
    _MISSING.append('"beautifulsoup4>=4.12,<5"')

if _MISSING:
    sys.stderr.write(
        "\n[!] Missing dependencies: {}\n"
        "    Install with:\n"
        "        pip install {}\n\n".format(", ".join(_MISSING), " ".join(_MISSING))
    )
    sys.exit(4)

try:
    import socks  # noqa: F401  (PySocks, pulled in by requests[socks])
    _SOCKS_OK = True
except ImportError:
    _SOCKS_OK = False


# ══════════════════════════════════════════════════════════════════════════════
#  TERMINAL STYLING
# ══════════════════════════════════════════════════════════════════════════════

class Style:
    """ANSI codes, auto-disabled for non-TTY output, NO_COLOR, or --no-color."""

    enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def wrap(cls, code: str, text: str) -> str:
        return f"{code}{text}{cls.RESET}" if cls.enabled else text

    @classmethod
    def c(cls, t): return cls.wrap(cls.CYAN, t)
    @classmethod
    def g(cls, t): return cls.wrap(cls.GREEN, t)
    @classmethod
    def y(cls, t): return cls.wrap(cls.YELLOW, t)
    @classmethod
    def r(cls, t): return cls.wrap(cls.RED, t)
    @classmethod
    def d(cls, t): return cls.wrap(cls.DIM, t)
    @classmethod
    def b(cls, t): return cls.wrap(cls.BOLD, t)


class Log:
    """Minimal leveled logger. Everything diagnostic goes to stderr so that
    --json on stdout stays machine-parseable."""

    quiet = False
    verbose = False

    @staticmethod
    def _emit(prefix: str, msg: str) -> None:
        sys.stderr.write(f"{prefix} {msg}\n")
        sys.stderr.flush()

    @classmethod
    def info(cls, msg: str) -> None:
        if not cls.quiet:
            cls._emit(Style.c("[*]"), msg)

    @classmethod
    def ok(cls, msg: str) -> None:
        if not cls.quiet:
            cls._emit(Style.g("[+]"), msg)

    @classmethod
    def warn(cls, msg: str) -> None:
        if not cls.quiet:
            cls._emit(Style.y("[!]"), msg)

    @classmethod
    def err(cls, msg: str) -> None:
        cls._emit(Style.r("[-]"), msg)

    @classmethod
    def dbg(cls, msg: str) -> None:
        if cls.verbose and not cls.quiet:
            cls._emit(Style.d("[~]"), Style.d(msg))


# ══════════════════════════════════════════════════════════════════════════════
#  PATHS
# ══════════════════════════════════════════════════════════════════════════════

def config_dir() -> Path:
    base = os.environ.get("DECINT_HOME")
    if base:
        return Path(base).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "decint"


SOURCES_FILE = "darkweb_sources.json"
HEALTH_FILE = "endpoint_health.json"


# ══════════════════════════════════════════════════════════════════════════════
#  ENDPOINT REGISTRY
#
#  IMPORTANT OPERATIONAL NOTE
#  .onion addresses for search indexes rotate frequently — some monthly. The
#  addresses below are seed values only. They are NOT guaranteed live. This is
#  precisely why the registry is a config file and why health tracking exists:
#  run --probe periodically, and edit the JSON when a mirror moves.
#
#  parser types:
#    "html_generic" — heuristic extraction (default, most resilient)
#    "json_api"     — JSON response, mapped via json_map
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_SOURCES: dict = {
    "_comment": (
        "DECINT dark web source registry. Onion addresses rotate; run --probe "
        "to validate and edit `mirrors` when an index moves. Set enabled=false "
        "to skip a source without deleting it."
    ),
    "schema_version": 2,
    "sources": [
        {
            "key": "ahmia",
            "name": "Ahmia",
            "enabled": True,
            "trust": 1.00,
            "parser": "html_generic",
            "notes": "Curated index, filters abuse material. Most reliable source.",
            "mirrors": [
                "https://ahmia.fi/search/?q={q}",
                "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q={q}",
            ],
            "page_param": None,
        },
        {
            "key": "torch",
            "name": "Torch",
            "enabled": True,
            "trust": 0.80,
            "parser": "html_generic",
            "notes": "Oldest onion index. Address changes often; expect churn.",
            "mirrors": [
                "http://torchdeedp3i2jigzjdmfpn5ttjhthh5wbmda2rr3jvqjg5p77c54dqd.onion/search?query={q}",
                "http://torchqsxkqf6sb77gw2xbdgmnnuohiyb6bzzmml3uxqpbqiokjowxid.onion/search?query={q}",
            ],
            "page_param": "page",
        },
        {
            "key": "haystak",
            "name": "Haystak",
            "enabled": True,
            "trust": 0.70,
            "parser": "html_generic",
            "notes": "Large index; free tier truncates results.",
            "mirrors": [
                "http://haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion/?q={q}",
            ],
            "page_param": "page",
        },
        {
            "key": "tordex",
            "name": "Tordex",
            "enabled": True,
            "trust": 0.65,
            "parser": "html_generic",
            "notes": "General onion index.",
            "mirrors": [
                "http://tordexu73joywapk2txdr54jed4imqledpcvcuf75qsas2gwdgksvnyd.onion/search?query={q}",
            ],
            "page_param": "page",
        },
        {
            "key": "onionland",
            "name": "OnionLand",
            "enabled": True,
            "trust": 0.60,
            "parser": "html_generic",
            "notes": "Broad crawler, higher noise ratio.",
            "mirrors": [
                "http://3bbad7fauom4d6sgppalyqddsqbf5u5p56b5k5uk2zxsy3d6ey2jobad.onion/search?q={q}",
            ],
            "page_param": "page",
        },
        {
            "key": "excavator",
            "name": "Excavator",
            "enabled": True,
            "trust": 0.55,
            "parser": "html_generic",
            "notes": "Forum/market oriented index.",
            "mirrors": [
                "http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/search/?search={q}",
            ],
            "page_param": None,
        },
        {
            "key": "darksearch",
            "name": "DarkSearch",
            "enabled": False,
            "trust": 0.50,
            "parser": "json_api",
            "notes": (
                "DISABLED — darksearch.io ceased public API operation. Retained "
                "for reference only. Do not re-enable without verifying the "
                "service has returned."
            ),
            "mirrors": ["https://darksearch.io/api/search?query={q}&page=1"],
            "json_map": {
                "root": "data",
                "title": "title",
                "url": "link",
                "snippet": "description",
            },
            "page_param": None,
        },
    ],
}


@dataclass
class Source:
    key: str
    name: str
    enabled: bool = True
    trust: float = 0.5
    parser: str = "html_generic"
    notes: str = ""
    mirrors: list = field(default_factory=list)
    page_param: str | None = None
    json_map: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        return cls(
            key=str(d["key"]),
            name=str(d.get("name", d["key"])),
            enabled=bool(d.get("enabled", True)),
            trust=float(d.get("trust", 0.5)),
            parser=str(d.get("parser", "html_generic")),
            notes=str(d.get("notes", "")),
            mirrors=list(d.get("mirrors", [])),
            page_param=d.get("page_param"),
            json_map=dict(d.get("json_map", {})),
        )


def load_sources(path: Path | None, create: bool = True) -> list[Source]:
    """Load the endpoint registry, seeding defaults on first run."""
    path = path or (config_dir() / SOURCES_FILE)

    if not path.exists():
        if create:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(DEFAULT_SOURCES, indent=2), encoding="utf-8")
                Log.info(f"Seeded source registry → {path}")
            except OSError as e:
                Log.warn(f"Could not write registry ({e}); using built-in defaults")
                return _parse_sources(DEFAULT_SOURCES)
        else:
            return _parse_sources(DEFAULT_SOURCES)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        Log.err(f"Registry unreadable ({path}): {e}")
        Log.warn("Falling back to built-in defaults. Fix or delete the file to reseed.")
        return _parse_sources(DEFAULT_SOURCES)

    return _parse_sources(raw)


def _parse_sources(raw: dict) -> list[Source]:
    out: list[Source] = []
    for entry in raw.get("sources", []):
        try:
            out.append(Source.from_dict(entry))
        except (KeyError, TypeError, ValueError) as e:
            Log.warn(f"Skipping malformed source entry: {e}")
    if not out:
        Log.err("Registry contains no usable sources.")
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH STORE / CIRCUIT BREAKER
#
#  Every mirror accumulates a success/failure record. Three consecutive
#  failures trips a cooldown with exponential backoff, so a dead onion stops
#  costing 45 seconds on every run. Mirrors are tried in health order.
# ══════════════════════════════════════════════════════════════════════════════

BREAKER_THRESHOLD = 3
BREAKER_BASE_COOLDOWN = 300      # 5 minutes
BREAKER_MAX_COOLDOWN = 21600     # 6 hours


@dataclass
class MirrorHealth:
    url: str
    ok: int = 0
    fail: int = 0
    consecutive_fail: int = 0
    last_ok: str | None = None
    last_fail: str | None = None
    last_status: int | None = None
    last_error: str | None = None
    avg_latency: float = 0.0
    cooldown_until: float = 0.0

    @property
    def total(self) -> int:
        return self.ok + self.fail

    @property
    def success_rate(self) -> float:
        return self.ok / self.total if self.total else 0.5  # unknown = neutral

    def in_cooldown(self, now: float | None = None) -> bool:
        return (now or time.time()) < self.cooldown_until

    def record_ok(self, status: int, latency: float) -> None:
        self.ok += 1
        self.consecutive_fail = 0
        self.cooldown_until = 0.0
        self.last_ok = utc_now()
        self.last_status = status
        self.last_error = None
        # exponential moving average keeps recent latency weighted
        self.avg_latency = latency if self.avg_latency == 0 else \
            round(0.7 * self.avg_latency + 0.3 * latency, 2)

    def record_fail(self, status: int | None, error: str) -> None:
        self.fail += 1
        self.consecutive_fail += 1
        self.last_fail = utc_now()
        self.last_status = status
        self.last_error = error[:200]
        if self.consecutive_fail >= BREAKER_THRESHOLD:
            exp = self.consecutive_fail - BREAKER_THRESHOLD
            cooldown = min(BREAKER_BASE_COOLDOWN * (2 ** exp), BREAKER_MAX_COOLDOWN)
            self.cooldown_until = time.time() + cooldown


class HealthStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / HEALTH_FILE)
        self._data: dict[str, MirrorHealth] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for url, rec in raw.get("mirrors", {}).items():
                rec.pop("url", None)
                self._data[url] = MirrorHealth(url=url, **rec)
        except Exception as e:
            Log.dbg(f"Health store unreadable, starting fresh: {e}")
            self._data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated": utc_now(),
                "tool_version": __version__,
                "mirrors": {u: asdict(h) for u, h in self._data.items()},
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)  # atomic; never leaves a half-written file
        except OSError as e:
            Log.dbg(f"Could not persist health store: {e}")

    def get(self, url: str) -> MirrorHealth:
        with self._lock:
            if url not in self._data:
                self._data[url] = MirrorHealth(url=url)
            return self._data[url]

    def ordered(self, mirrors: list[str], respect_cooldown: bool = True) -> list[str]:
        """Best-first ordering: available before cooled-down, then by success
        rate, then by latency."""
        now = time.time()

        def rank(u: str):
            h = self.get(u)
            cooled = 1 if (respect_cooldown and h.in_cooldown(now)) else 0
            return (cooled, -h.success_rate, h.avg_latency or 999)

        return sorted(mirrors, key=rank)

    def all(self) -> dict[str, MirrorHealth]:
        return dict(self._data)

    def reset(self) -> None:
        with self._lock:
            self._data = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Per-host minimum interval. Prevents hammering a single index when
    running concurrently or paginating."""

    def __init__(self, min_interval: float = 1.5):
        self.min_interval = min_interval
        self._next_free: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            earliest = self._next_free.get(host, 0.0)
            delay = max(0.0, earliest - now)
            self._next_free[host] = max(now, earliest) + self.min_interval
        if delay > 0:
            time.sleep(delay)


# ══════════════════════════════════════════════════════════════════════════════
#  TOR TRANSPORT
# ══════════════════════════════════════════════════════════════════════════════

USER_AGENTS = [
    # Tor Browser fingerprints — blending in beats a unique UA string.
    "Mozilla/5.0 (Windows NT 10.0; rv:115.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}


def _isolation_token(n: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


class TorTransport:
    """Wraps a requests.Session bound to Tor's SOCKS5 proxy.

    Each source gets distinct SOCKS credentials. With Tor's IsolateSOCKSAuth
    (on by default) that forces a separate circuit per source, so one slow or
    blocked circuit cannot stall the others — the single largest cause of
    hangs in v1.x.
    """

    def __init__(self, host: str, port: int, isolation: str | None = None,
                 timeout: float = 45.0, user_agent: str | None = None):
        self.timeout = timeout
        token = isolation or _isolation_token()
        proxy = f"socks5h://{token}:{token}@{host}:{port}"
        self.session = requests.Session()
        self.session.proxies = {"http": proxy, "https": proxy}
        headers = dict(BASE_HEADERS)
        headers["User-Agent"] = user_agent or random.choice(USER_AGENTS)
        self.session.headers.update(headers)
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get(self, url: str):
        return self.session.get(url, timeout=self.timeout, allow_redirects=True)

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@dataclass
class FetchResult:
    ok: bool
    url: str
    status: int | None = None
    text: str = ""
    latency: float = 0.0
    error: str = ""


def fetch(transport: TorTransport, url: str, limiter: RateLimiter,
          retries: int = 2, backoff: float = 2.0,
          debug_label: str | None = None) -> FetchResult:
    """GET with rate limiting, bounded retries and exponential backoff.

    Never raises — all failure modes are folded into FetchResult so that one
    bad endpoint cannot abort a run.
    """
    host = urlparse(url).hostname or url
    last_err = ""
    last_status: int | None = None

    for attempt in range(retries + 1):
        limiter.wait(host)
        started = time.monotonic()
        try:
            resp = transport.get(url)
            latency = round(time.monotonic() - started, 2)
            last_status = resp.status_code

            if debug_label:
                _dump_debug(debug_label, resp.text)

            # 5xx and 429 are worth retrying; 4xx generally is not.
            if resp.status_code >= 500 or resp.status_code == 429:
                last_err = f"HTTP {resp.status_code}"
                if attempt < retries:
                    sleep_for = backoff * (2 ** attempt) + random.uniform(0, 1)
                    Log.dbg(f"{host}: {last_err}, retry in {sleep_for:.1f}s")
                    time.sleep(sleep_for)
                    continue
                return FetchResult(False, url, resp.status_code, "", latency, last_err)

            if resp.status_code >= 400:
                return FetchResult(False, url, resp.status_code, "", latency,
                                   f"HTTP {resp.status_code}")

            return FetchResult(True, url, resp.status_code, resp.text, latency)

        except requests.exceptions.Timeout:
            last_err = f"timeout after {transport.timeout}s"
        except requests.exceptions.ConnectionError as e:
            msg = str(e)
            if "Name or service not known" in msg or "0x04" in msg:
                last_err = "onion unreachable (host down or address changed)"
            elif "Connection refused" in msg:
                last_err = "SOCKS proxy refused — is Tor running?"
            else:
                last_err = f"connection error: {msg[:120]}"
        except Exception as e:  # noqa: BLE001 — deliberate catch-all
            last_err = f"{type(e).__name__}: {str(e)[:120]}"

        if attempt < retries:
            sleep_for = backoff * (2 ** attempt) + random.uniform(0, 1)
            Log.dbg(f"{host}: {last_err}, retry in {sleep_for:.1f}s")
            time.sleep(sleep_for)

    return FetchResult(False, url, last_status, "", 0.0, last_err)


def _dump_debug(label: str, html: str) -> None:
    try:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)[:80]
        path = Path("/tmp") / f"decint_{safe}.html"
        path.write_text(html, encoding="utf-8", errors="replace")
        Log.dbg(f"raw HTML → {path}")
    except OSError as e:
        Log.dbg(f"debug dump failed: {e}")


def check_tor(host: str, port: int, timeout: float = 30.0) -> tuple[bool, str]:
    """Confirm traffic is actually leaving through Tor."""
    if not _SOCKS_OK:
        return False, "PySocks missing — reinstall with: pip install \"requests[socks]\""
    try:
        with TorTransport(host, port, timeout=timeout) as t:
            resp = t.session.get("https://check.torproject.org/api/ip", timeout=timeout)
            data = resp.json()
            if data.get("IsTor"):
                return True, f"exit node {data.get('IP', 'unknown')}"
            return False, f"traffic is NOT going through Tor (IP {data.get('IP')})"
    except requests.exceptions.ConnectionError:
        return False, f"cannot reach SOCKS proxy at {host}:{port} — try: sudo systemctl start tor"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:150]}"


# ══════════════════════════════════════════════════════════════════════════════
#  PARSING
#
#  v1.x used brittle per-site CSS selectors that broke whenever an index
#  changed markup. v2 uses a heuristic extractor: locate anchors that look like
#  results, walk up to a sensible container, and treat the residual text as the
#  snippet. This degrades gracefully instead of returning zero.
# ══════════════════════════════════════════════════════════════════════════════

ONION_V3_RE = re.compile(r"\b([a-z2-7]{56})\.onion\b", re.I)
ONION_V2_RE = re.compile(r"\b([a-z2-7]{16})\.onion\b", re.I)
ANY_ONION_RE = re.compile(r"\b([a-z2-7]{16,56})\.onion\b", re.I)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")
BTC_RE = re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
XMR_RE = re.compile(r"\b[48][0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b")
PGP_RE = re.compile(r"-----BEGIN PGP (?:PUBLIC KEY BLOCK|MESSAGE|SIGNATURE)-----")

# Anchor text that is navigation chrome, not a result.
BOILERPLATE = {
    "home", "about", "contact", "next", "prev", "previous", "back", "more",
    "donate", "advertise", "add url", "submit", "login", "register", "help",
    "faq", "privacy", "terms", "search", "settings", "language", "index",
    "sign in", "sign up", "menu", "top", "first", "last", "close", "cancel",
    "add site", "report", "api", "blog", "news", "rss", "1", "2", "3", "»", "«",
}

CONTAINER_TAGS = ["li", "article", "dl", "tr", "div", "section", "p", "td"]

MAX_SNIPPET = 400
MAX_TITLE = 200


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_boilerplate(title: str, href: str) -> bool:
    t = title.strip().lower()
    if not t or t in BOILERPLATE:
        return True
    if len(t) <= 2 and not t.isdigit():
        return True
    if href in ("#", "/", "", "javascript:void(0)") or href.startswith("javascript:"):
        return True
    if href.startswith("mailto:"):
        return True
    return False


def parse_html_generic(html: str, source: Source, base_url: str,
                       onion_only: bool = False) -> list[dict]:
    """Heuristic result extraction. Works across index layouts."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        Log.dbg(f"{source.name}: HTML parse failed: {e}")
        return []

    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "form", "svg", "iframe"]):
        tag.decompose()

    engine_host = (urlparse(base_url).hostname or "").lower()
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        title = _clean_text(anchor.get_text())

        if _is_boilerplate(title, href):
            continue

        # Resolve relative links against the engine we queried.
        absolute = href if href.startswith(("http://", "https://")) \
            else urljoin(base_url, href)

        target_host = (urlparse(absolute).hostname or "").lower()

        # Drop self-referential links back into the search engine itself.
        if target_host and engine_host and target_host == engine_host:
            if not ANY_ONION_RE.search(href):
                continue

        if onion_only and ".onion" not in absolute.lower():
            continue

        key = absolute.lower().rstrip("/")
        if key in seen_hrefs:
            continue
        seen_hrefs.add(key)

        # Walk up for a container that holds the description text.
        snippet = ""
        node = anchor.parent
        depth = 0
        while node is not None and depth < 4:
            if getattr(node, "name", None) in CONTAINER_TAGS:
                text = _clean_text(node.get_text(" "))
                # Reject containers so large they're clearly the whole page.
                if len(text) <= 2000:
                    snippet = text.replace(title, "", 1).strip(" -–—|·:")
                    if len(snippet) >= 20:
                        break
                    snippet = ""
            node = node.parent
            depth += 1

        results.append({
            "source": source.name,
            "source_key": source.key,
            "title": title[:MAX_TITLE],
            "url": absolute,
            "snippet": snippet[:MAX_SNIPPET],
            "engine_url": base_url,
        })

    return results


def parse_json_api(payload_text: str, source: Source, base_url: str) -> list[dict]:
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError as e:
        Log.dbg(f"{source.name}: JSON decode failed: {e}")
        return []

    m = source.json_map or {}
    root_key = m.get("root")
    rows = data.get(root_key, []) if root_key else data
    if not isinstance(rows, list):
        Log.dbg(f"{source.name}: expected list at '{root_key}'")
        return []

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "source": source.name,
            "source_key": source.key,
            "title": _clean_text(str(row.get(m.get("title", "title"), "")))[:MAX_TITLE],
            "url": str(row.get(m.get("url", "url"), "")).strip(),
            "snippet": _clean_text(str(row.get(m.get("snippet", "snippet"), "")))[:MAX_SNIPPET],
            "engine_url": base_url,
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  SCORING & ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════════

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "at",
    "by", "with", "from", "as", "it", "be", "this", "that",
}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in STOPWORDS and len(t) > 1]


def score_result(record: dict, keyword: str, trust: float) -> float:
    """Relevance 0–100. Weighted toward title matches and exact phrase hits."""
    phrase = keyword.lower().strip()
    tokens = set(tokenize(keyword))
    if not tokens:
        return 0.0

    title = (record.get("title") or "").lower()
    snippet = (record.get("snippet") or "").lower()
    url = (record.get("url") or "").lower()

    raw = 0.0
    if phrase and phrase in title:
        raw += 30
    if phrase and phrase in snippet:
        raw += 15

    title_tokens = set(tokenize(title))
    snippet_tokens = set(tokenize(snippet))

    raw += 12 * len(tokens & title_tokens)
    raw += 5 * len(tokens & snippet_tokens)
    raw += 4 * sum(1 for t in tokens if t in url)

    # Coverage bonus: every query token represented somewhere.
    covered = sum(1 for t in tokens
                  if t in title_tokens or t in snippet_tokens or t in url)
    if covered == len(tokens):
        raw += 15

    # Substance signals — an empty stub is less useful than a described page.
    if len(snippet) > 80:
        raw += 5
    if ".onion" in url:
        raw += 5

    normalized = min(100.0, raw)
    return round(normalized * (0.6 + 0.4 * max(0.0, min(1.0, trust))), 1)


def extract_entities(record: dict) -> dict:
    blob = " ".join([
        record.get("title", ""), record.get("snippet", ""), record.get("url", ""),
    ])
    ents = {
        "onion_v3": sorted({f"{m.group(1).lower()}.onion" for m in ONION_V3_RE.finditer(blob)}),
        "onion_v2_deprecated": sorted({f"{m.group(1).lower()}.onion" for m in ONION_V2_RE.finditer(blob)}),
        "emails": sorted(set(EMAIL_RE.findall(blob))),
        "btc": sorted(set(BTC_RE.findall(blob))),
        "eth": sorted(set(ETH_RE.findall(blob))),
        "xmr": sorted(set(XMR_RE.findall(blob))),
        "pgp": bool(PGP_RE.search(blob)),
    }
    return {k: v for k, v in ents.items() if v}


def canonical_key(url: str) -> str:
    """Dedup key: onion host + normalized path. Ignores scheme, query, and
    trailing slash so mirrors of the same page collapse."""
    try:
        p = urlparse(url if "://" in url else f"http://{url}")
        host = (p.hostname or "").lower()
        path = (p.path or "/").rstrip("/").lower() or "/"
        return f"{host}{path}"
    except Exception:
        return (url or "").strip().lower()


def merge_results(records: list[dict]) -> list[dict]:
    """Collapse duplicates across sources. Multiple independent indexes
    returning the same page is a corroboration signal, so merged records get a
    confidence boost."""
    merged: dict[str, dict] = {}

    for rec in records:
        key = canonical_key(rec.get("url", ""))
        if not key or key == "/":
            continue

        if key not in merged:
            rec = dict(rec)
            rec["sources"] = [rec.get("source", "?")]
            rec["corroboration"] = 1
            merged[key] = rec
            continue

        existing = merged[key]
        if rec.get("source") not in existing["sources"]:
            existing["sources"].append(rec.get("source", "?"))
            existing["corroboration"] = len(existing["sources"])
        # Keep the richest title and snippet available.
        if len(rec.get("snippet", "")) > len(existing.get("snippet", "")):
            existing["snippet"] = rec["snippet"]
        if len(rec.get("title", "")) > len(existing.get("title", "")):
            existing["title"] = rec["title"]
        existing["score"] = max(existing.get("score", 0), rec.get("score", 0))

    out = []
    for rec in merged.values():
        if rec.get("corroboration", 1) > 1:
            boost = 1 + (0.12 * (rec["corroboration"] - 1))
            rec["score"] = round(min(100.0, rec.get("score", 0) * boost), 1)
        rec.pop("source", None)
        out.append(rec)

    return sorted(out, key=lambda r: r.get("score", 0), reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RunConfig:
    keyword: str
    tor_host: str = "127.0.0.1"
    tor_port: int = 9050
    timeout: float = 45.0
    retries: int = 2
    workers: int = 4
    pages: int = 1
    rate_limit: float = 1.5
    onion_only: bool = False
    debug: bool = False
    ignore_cooldown: bool = False


def query_source(source: Source, cfg: RunConfig, health: HealthStore,
                 limiter: RateLimiter) -> tuple[str, list[dict], str]:
    """Query one source across its mirrors until one succeeds.

    Returns (source_key, records, status_message). Never raises.
    """
    q = quote_plus(cfg.keyword)
    mirrors = health.ordered(source.mirrors, respect_cooldown=not cfg.ignore_cooldown)

    if not mirrors:
        return source.key, [], "no mirrors configured"

    with TorTransport(cfg.tor_host, cfg.tor_port,
                      isolation=f"decint-{source.key}-{_isolation_token(6)}",
                      timeout=cfg.timeout) as transport:

        for mirror in mirrors:
            h = health.get(mirror)
            if h.in_cooldown() and not cfg.ignore_cooldown:
                Log.dbg(f"{source.name}: skipping cooled-down mirror {mirror[:50]}")
                continue

            collected: list[dict] = []
            template = mirror.replace("{q}", q)

            for page in range(1, max(1, cfg.pages) + 1):
                url = template
                if page > 1:
                    if not source.page_param:
                        break
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}{source.page_param}={page}"

                label = f"{source.key}_p{page}" if cfg.debug else None
                res = fetch(transport, url, limiter, retries=cfg.retries,
                            debug_label=label)

                if not res.ok:
                    h.record_fail(res.status, res.error)
                    if page == 1:
                        Log.dbg(f"{source.name}: {mirror[:48]} → {res.error}")
                        break
                    break

                h.record_ok(res.status or 200, res.latency)

                if source.parser == "json_api":
                    page_records = parse_json_api(res.text, source, url)
                else:
                    page_records = parse_html_generic(res.text, source, url,
                                                      onion_only=cfg.onion_only)

                if not page_records:
                    break
                collected.extend(page_records)

            if collected:
                return source.key, collected, f"{len(collected)} raw hit(s)"

            # Reached a mirror successfully but parsed nothing — the query may
            # simply have no matches. Try the next mirror before giving up.
            if h.last_ok and h.consecutive_fail == 0:
                Log.dbg(f"{source.name}: mirror reachable, zero parsed results")

        return source.key, [], "no results from any mirror"


def run_search(sources: list[Source], cfg: RunConfig,
               health: HealthStore) -> tuple[list[dict], dict]:
    limiter = RateLimiter(cfg.rate_limit)
    raw: list[dict] = []
    per_source: dict = {}

    active = [s for s in sources if s.enabled and s.mirrors]
    if not active:
        Log.err("No enabled sources with mirrors. Check your registry.")
        return [], {}

    Log.info(f"Querying {len(active)} source(s) with {cfg.workers} worker(s)")

    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
        futures = {
            pool.submit(query_source, s, cfg, health, limiter): s for s in active
        }
        try:
            for future in as_completed(futures):
                src = futures[future]
                try:
                    key, records, status = future.result()
                except Exception as e:  # noqa: BLE001
                    Log.warn(f"{src.name}: worker error: {type(e).__name__}: {e}")
                    per_source[src.key] = {"name": src.name, "count": 0,
                                           "status": f"error: {e}"}
                    continue

                for rec in records:
                    rec["score"] = score_result(rec, cfg.keyword, src.trust)
                    ents = extract_entities(rec)
                    if ents:
                        rec["entities"] = ents

                raw.extend(records)
                per_source[key] = {"name": src.name, "count": len(records),
                                   "status": status}

                symbol = Style.g("✓") if records else Style.d("·")
                Log.info(f"  {symbol} {src.name:<12} {status}")

        except KeyboardInterrupt:
            Log.warn("Interrupted — cancelling outstanding queries")
            for f in futures:
                f.cancel()
            raise

    return raw, per_source


# ══════════════════════════════════════════════════════════════════════════════
#  PROBE / HEALTH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

def run_probe(sources: list[Source], cfg: RunConfig, health: HealthStore) -> int:
    """Connectivity check across every configured mirror."""
    limiter = RateLimiter(cfg.rate_limit)
    targets: list[tuple[Source, str]] = [
        (s, m) for s in sources if s.mirrors for m in s.mirrors
    ]

    print(f"\n{Style.c('PROBE')} — testing {len(targets)} mirror(s)\n")
    reachable = 0

    def probe_one(item):
        src, mirror = item
        url = mirror.replace("{q}", "test")
        with TorTransport(cfg.tor_host, cfg.tor_port,
                          isolation=f"probe-{_isolation_token(6)}",
                          timeout=cfg.timeout) as t:
            return src, mirror, fetch(t, url, limiter, retries=0)

    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
        for src, mirror, res in pool.map(probe_one, targets):
            h = health.get(mirror)
            host = urlparse(mirror).hostname or mirror
            if res.ok:
                h.record_ok(res.status or 200, res.latency)
                reachable += 1
                print(f"  {Style.g('UP  ')} {src.name:<12} "
                      f"{Style.d(f'{res.latency:>5.1f}s')}  HTTP {res.status}  {host[:52]}")
            else:
                h.record_fail(res.status, res.error)
                state = "SKIP" if not src.enabled else "DOWN"
                colour = Style.d if not src.enabled else Style.r
                print(f"  {colour(state)}  {src.name:<12} "
                      f"{Style.d('  --  ')}  {Style.d(res.error[:60])}")

    print(f"\n  {reachable}/{len(targets)} mirrors reachable\n")
    if reachable == 0:
        print(Style.y("  All mirrors failed. Either Tor is not routing, or the\n"
                      "  seeded addresses have rotated. Edit the registry:\n"
                      f"    {config_dir() / SOURCES_FILE}\n"))
    return 0 if reachable else 2


def show_health(health: HealthStore) -> int:
    records = health.all()
    if not records:
        print("\n  No health data yet. Run --probe or a search first.\n")
        return 0

    print(f"\n{Style.c('ENDPOINT HEALTH')}  {Style.d(str(health.path))}\n")
    header = f"  {'STATE':<9} {'OK/FAIL':<10} {'RATE':<7} {'LAT':<7} MIRROR"
    print(Style.b(header))
    print(Style.d("  " + "-" * 76))

    now = time.time()
    for url, h in sorted(records.items(), key=lambda kv: -kv[1].success_rate):
        # Pad the plain label first — ANSI codes have zero display width but
        # non-zero string length, which corrupts f-string column alignment.
        if h.in_cooldown(now):
            label, paint = f"COOL {int(h.cooldown_until - now)//60}m", Style.y
        elif h.success_rate >= 0.5 and h.ok:
            label, paint = "HEALTHY", Style.g
        elif h.total == 0:
            label, paint = "UNKNOWN", Style.d
        else:
            label, paint = "DEGRADED", Style.r
        state = paint(f"{label:<9}")

        counts = f"{h.ok}/{h.fail}"
        host = urlparse(url).hostname or url
        print(f"  {state} {counts:<10} {h.success_rate*100:>4.0f}%  "
              f"{h.avg_latency:>5.1f}s  {host[:46]}")
        if h.last_error:
            print(Style.d(f"      last error: {h.last_error[:66]}"))
    print()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

def evidence_hash(results: list[dict]) -> str:
    """Deterministic SHA-256 over the canonical result set, so a client can
    prove a report was not altered after generation."""
    canonical = json.dumps(
        [{"url": r.get("url"), "title": r.get("title"), "score": r.get("score")}
         for r in results],
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest(cfg: RunConfig, results: list[dict], per_source: dict,
                   started: str, elapsed: float) -> dict:
    return {
        "tool": __tool__,
        "version": __version__,
        "keyword": cfg.keyword,
        "started_utc": started,
        "completed_utc": utc_now(),
        "elapsed_seconds": round(elapsed, 2),
        "result_count": len(results),
        "sources": per_source,
        "parameters": {
            "timeout": cfg.timeout,
            "retries": cfg.retries,
            "workers": cfg.workers,
            "pages": cfg.pages,
            "onion_only": cfg.onion_only,
        },
        "sha256": evidence_hash(results),
    }


def print_table(results: list[dict], manifest: dict, limit: int | None) -> None:
    line = "═" * 72
    print(f"\n{Style.c(line)}")
    print(f"{Style.c('  DECINT // DARK WEB SEARCH')}  {Style.d('v' + __version__)}")
    print(f"  keyword : {Style.b(manifest['keyword'])}")
    print(f"  time    : {Style.d(manifest['completed_utc'])}")
    print(f"  elapsed : {Style.d(str(manifest['elapsed_seconds']) + 's')}")
    print(f"  results : {Style.b(str(manifest['result_count']))}")
    print(f"  sha256  : {Style.d(manifest['sha256'][:32] + '...')}")
    print(f"{Style.c(line)}\n")

    if not results:
        print(Style.d(
            "  No results.\n"
            "    --probe    check which mirrors are reachable\n"
            "    --health   review endpoint history\n"
            "    --debug    dump raw HTML to /tmp/decint_*.html\n"
        ))
        return

    shown = results[:limit] if limit else results
    for i, r in enumerate(shown, 1):
        score = r.get("score", 0)
        if score >= 60:
            score_txt = Style.g(f"{score:>5.1f}")
        elif score >= 30:
            score_txt = Style.y(f"{score:>5.1f}")
        else:
            score_txt = Style.d(f"{score:>5.1f}")

        srcs = ", ".join(r.get("sources", []))
        corr = r.get("corroboration", 1)
        corr_txt = Style.g(f" ×{corr}") if corr > 1 else ""

        print(f"{Style.c(f'[{i:03d}]')} score {score_txt}  "
              f"{Style.b(srcs)}{corr_txt}")
        print(f"  Title   : {r.get('title', '(untitled)')[:88]}")
        print(f"  URL     : {Style.d(r.get('url', '')[:88])}")

        snippet = r.get("snippet", "").strip()
        if snippet:
            wrapped = textwrap.fill(
                snippet[:280], width=78,
                initial_indent="  Snippet : ", subsequent_indent="            ",
            )
            print(Style.d(wrapped))

        ents = r.get("entities")
        if ents:
            bits = []
            for k, v in ents.items():
                if k == "pgp":
                    bits.append("PGP block")
                elif isinstance(v, list) and v:
                    bits.append(f"{k}:{len(v)}")
            if bits:
                print(f"  Entities: {Style.y(', '.join(bits))}")
        print()

    if limit and len(results) > limit:
        print(Style.d(f"  ... {len(results) - limit} more (raise --limit to show)\n"))


def write_output(path: str, results: list[dict], manifest: dict, fmt: str) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        cols = ["score", "sources", "corroboration", "title", "url", "snippet"]
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in results:
                row = dict(r)
                row["sources"] = "; ".join(r.get("sources", []))
                w.writerow(row)
    elif fmt == "jsonl":
        with p.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"_manifest": manifest}) + "\n")
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        p.write_text(
            json.dumps({"manifest": manifest, "results": results},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    Log.ok(f"Saved {len(results)} result(s) → {p}")


# ══════════════════════════════════════════════════════════════════════════════
#  SELF TEST
#  Offline validation. Lets a client confirm a working install without Tor,
#  and gives you a regression check before shipping an update.
# ══════════════════════════════════════════════════════════════════════════════

FIXTURE_HTML = """
<html><body>
  <nav><a href="/">Home</a><a href="/about">About</a></nav>
  <ul>
    <li class="result">
      <h4><a href="http://abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrstuvwx.onion/wiki">
        Sample Directory Wiki</a></h4>
      <p class="description">A directory listing of onion services covering
      security research topics and mirrors.</p>
    </li>
    <li class="result">
      <h4><a href="/redirect?u=http://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz.onion/forum">
        Research Forum</a></h4>
      <p>Discussion board. Contact admin@example.com or send to
      1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa for details.</p>
    </li>
  </ul>
  <footer><a href="/donate">Donate</a></footer>
</body></html>
"""


def self_test() -> int:
    passed, failed = 0, 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  {Style.g('PASS')}  {label}")
        else:
            failed += 1
            print(f"  {Style.r('FAIL')}  {label}" + (f"  — {detail}" if detail else ""))

    print(f"\n{Style.c('SELF TEST')}  {__tool__} v{__version__}\n")

    print(Style.b("  Environment"))
    check("requests importable", "requests" in sys.modules)
    check("beautifulsoup4 importable", "bs4" in sys.modules)
    check("PySocks present (SOCKS5 support)", _SOCKS_OK,
          'reinstall: pip install "requests[socks]"')
    check("Python >= 3.9", sys.version_info >= (3, 9),
          f"found {sys.version_info.major}.{sys.version_info.minor}")

    print(Style.b("\n  Registry"))
    sources = _parse_sources(DEFAULT_SOURCES)
    check("built-in registry parses", len(sources) > 0)
    check("every source has mirrors", all(s.mirrors for s in sources))
    check("every mirror has {q} placeholder",
          all("{q}" in m for s in sources for m in s.mirrors))
    check("defunct sources disabled by default",
          all(not s.enabled for s in sources if s.key == "darksearch"))

    print(Style.b("\n  Parser"))
    src = sources[0]
    parsed = parse_html_generic(FIXTURE_HTML, src, "http://example.onion/search?q=x")
    check("extracts result anchors", len(parsed) == 2, f"got {len(parsed)}")
    check("drops nav/footer boilerplate",
          not any("donate" in (r["title"] or "").lower() for r in parsed))
    check("captures snippets",
          all(len(r["snippet"]) > 10 for r in parsed) if parsed else False)
    check("resolves relative URLs",
          any(r["url"].startswith("http://example.onion/redirect") for r in parsed))

    print(Style.b("\n  Scoring & enrichment"))
    rec = {"title": "Security Research Directory",
           "snippet": "onion services for security research",
           "url": "http://x.onion/security"}
    high = score_result(rec, "security research", 1.0)
    low = score_result(rec, "unrelated cooking recipes", 1.0)
    check("relevant query scores higher", high > low, f"{high} vs {low}")
    check("score bounded 0-100", 0 <= high <= 100, str(high))

    ents = extract_entities({
        "title": "", "url": "",
        "snippet": "mail admin@example.com btc 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    })
    check("extracts email", ents.get("emails") == ["admin@example.com"])
    check("extracts BTC address", bool(ents.get("btc")))

    print(Style.b("\n  Deduplication"))
    dupes = [
        {"source": "A", "title": "X", "url": "http://aaa.onion/page/", "score": 40},
        {"source": "B", "title": "X longer", "url": "https://aaa.onion/page", "score": 40},
        {"source": "A", "title": "Y", "url": "http://bbb.onion/other", "score": 20},
    ]
    merged = merge_results(dupes)
    check("collapses scheme/slash variants", len(merged) == 2, f"got {len(merged)}")
    check("corroboration boost applied",
          merged[0].get("corroboration") == 2 and merged[0]["score"] > 40)
    check("sorted by score descending",
          all(merged[i]["score"] >= merged[i + 1]["score"] for i in range(len(merged) - 1)))

    print(Style.b("\n  Health / circuit breaker"))
    h = MirrorHealth(url="http://t.onion")
    for _ in range(3):
        h.record_fail(None, "timeout")
    check("breaker trips after 3 failures", h.in_cooldown())
    h.record_ok(200, 1.0)
    check("success clears cooldown", not h.in_cooldown() and h.consecutive_fail == 0)

    print(Style.b("\n  Evidence integrity"))
    a = evidence_hash([{"url": "u", "title": "t", "score": 1}])
    b = evidence_hash([{"url": "u", "title": "t", "score": 1}])
    c = evidence_hash([{"url": "u", "title": "t", "score": 2}])
    check("hash is deterministic", a == b)
    check("hash changes with content", a != c)

    total = passed + failed
    print(f"\n  {Style.b(f'{passed}/{total} checks passed')}")
    if failed:
        print(f"  {Style.r(f'{failed} failure(s) — do not ship this build')}\n")
        return 1
    print(f"  {Style.g('Install verified.')} Next: --probe to test Tor connectivity.\n")
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decint-darkweb",
        description=f"{__tool__} v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
            examples:
              %(prog)s --self-test                     verify install, no Tor needed
              %(prog)s --probe                         test mirror reachability
              %(prog)s "keyword"                       standard search
              %(prog)s "keyword" --sources ahmia torch
              %(prog)s "keyword" --pages 3 --min-score 25
              %(prog)s "keyword" --format csv --out hits.csv
              %(prog)s --health                        endpoint history
              %(prog)s --edit-config                   print registry path

            registry: {config_dir() / SOURCES_FILE}
            health:   {config_dir() / HEALTH_FILE}

            NOTE: .onion index addresses rotate. If results dry up, run --probe
            and update the mirrors in the registry file.
        """),
    )

    p.add_argument("keyword", nargs="?", help="search term or phrase")

    g = p.add_argument_group("modes")
    g.add_argument("--self-test", action="store_true",
                   help="run offline validation checks and exit")
    g.add_argument("--probe", action="store_true",
                   help="test reachability of all mirrors and exit")
    g.add_argument("--health", action="store_true",
                   help="show endpoint health history and exit")
    g.add_argument("--reset-health", action="store_true",
                   help="clear health history and exit")
    g.add_argument("--edit-config", action="store_true",
                   help="print config paths and exit")

    g = p.add_argument_group("search")
    g.add_argument("--sources", nargs="+", metavar="KEY",
                   help="restrict to specific source keys")
    g.add_argument("--pages", type=int, default=1,
                   help="pages per source where supported (default: 1)")
    g.add_argument("--min-score", type=float, default=0.0,
                   help="drop results below this relevance score (0-100)")
    g.add_argument("--limit", type=int, default=50,
                   help="max results displayed (default: 50, 0 = all)")
    g.add_argument("--onion-only", action="store_true",
                   help="discard non-.onion results")

    g = p.add_argument_group("network")
    g.add_argument("--tor-host", default="127.0.0.1", help="SOCKS host")
    g.add_argument("--tor-port", type=int, default=9050, help="SOCKS port")
    g.add_argument("--timeout", type=float, default=45.0, help="per-request timeout")
    g.add_argument("--retries", type=int, default=2, help="retries per request")
    g.add_argument("--workers", type=int, default=4, help="concurrent sources")
    g.add_argument("--rate-limit", type=float, default=1.5,
                   help="min seconds between requests to the same host")
    g.add_argument("--skip-tor-check", action="store_true",
                   help="skip the Tor verification step")
    g.add_argument("--ignore-cooldown", action="store_true",
                   help="query mirrors even if the circuit breaker tripped")

    g = p.add_argument_group("output")
    g.add_argument("--json", action="store_true", help="print JSON to stdout")
    g.add_argument("--format", choices=["json", "jsonl", "csv"], default="json",
                   help="file output format (default: json)")
    g.add_argument("--out", metavar="FILE", help="write results to file")
    g.add_argument("--config", metavar="FILE", help="alternate registry path")
    g.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    g.add_argument("--quiet", action="store_true", help="suppress progress output")
    g.add_argument("--verbose", action="store_true", help="verbose diagnostics")
    g.add_argument("--debug", action="store_true",
                   help="dump raw HTML to /tmp/decint_*.html")
    g.add_argument("--version", action="version",
                   version=f"{__tool__} v{__version__}")
    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.no_color:
        Style.enabled = False
    Log.quiet = args.quiet
    Log.verbose = args.verbose

    if args.self_test:
        return self_test()

    if args.edit_config:
        print(f"\n  registry : {config_dir() / SOURCES_FILE}")
        print(f"  health   : {config_dir() / HEALTH_FILE}\n")
        return 0

    health = HealthStore()

    if args.reset_health:
        health.reset()
        health.save()
        Log.ok("Health history cleared.")
        return 0

    if args.health:
        return show_health(health)

    config_path = Path(args.config).expanduser() if args.config else None
    sources = load_sources(config_path)
    if not sources:
        return 4

    if args.sources:
        wanted = {s.lower() for s in args.sources}
        known = {s.key for s in sources}
        unknown = wanted - known
        if unknown:
            Log.err(f"Unknown source key(s): {', '.join(sorted(unknown))}")
            Log.info(f"Available: {', '.join(sorted(known))}")
            return 4
        sources = [s for s in sources if s.key in wanted]
        for s in sources:
            s.enabled = True  # explicit selection overrides the disabled flag

    cfg = RunConfig(
        keyword=args.keyword or "",
        tor_host=args.tor_host,
        tor_port=args.tor_port,
        timeout=args.timeout,
        retries=args.retries,
        workers=args.workers,
        pages=args.pages,
        rate_limit=args.rate_limit,
        onion_only=args.onion_only,
        debug=args.debug,
        ignore_cooldown=args.ignore_cooldown,
    )

    if not args.probe and not cfg.keyword:
        Log.err("A keyword is required. See --help.")
        return 4

    # ── Tor verification ──────────────────────────────────────────────────
    if not args.skip_tor_check:
        Log.info("Verifying Tor connectivity...")
        ok, detail = check_tor(cfg.tor_host, cfg.tor_port, timeout=min(cfg.timeout, 30))
        if not ok:
            Log.err(f"Tor check failed: {detail}")
            Log.warn("Use --skip-tor-check to proceed anyway (NOT recommended).")
            return 3
        Log.ok(f"Tor OK — {detail}")

    if args.probe:
        try:
            rc = run_probe(sources, cfg, health)
        finally:
            health.save()
        return rc

    # ── Search ────────────────────────────────────────────────────────────
    started = utc_now()
    t0 = time.monotonic()
    interrupted = False
    raw: list[dict] = []
    per_source: dict = {}

    try:
        raw, per_source = run_search(sources, cfg, health)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        health.save()

    results = merge_results(raw)

    if args.min_score > 0:
        before = len(results)
        results = [r for r in results if r.get("score", 0) >= args.min_score]
        Log.dbg(f"min-score filter removed {before - len(results)} result(s)")

    manifest = build_manifest(cfg, results, per_source, started,
                              time.monotonic() - t0)
    manifest["interrupted"] = interrupted

    if args.json:
        print(json.dumps({"manifest": manifest, "results": results},
                         indent=2, ensure_ascii=False))
    else:
        print_table(results, manifest, args.limit if args.limit > 0 else None)

    if args.out:
        try:
            write_output(args.out, results, manifest, args.format)
        except OSError as e:
            Log.err(f"Could not write output: {e}")
            return 1

    if interrupted:
        return 130
    return 0 if results else 2


if __name__ == "__main__":
    # Restore default SIGPIPE so piping into head/less doesn't produce a
    # BrokenPipeError traceback.
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass

    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n[!] Interrupted.\n")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 — last-resort guard
        sys.stderr.write(
            f"\n[-] Unhandled error: {type(e).__name__}: {e}\n"
            f"    Re-run with --verbose --debug and send the output to support.\n"
        )
        sys.exit(1)

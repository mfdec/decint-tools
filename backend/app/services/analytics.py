"""Visitor analytics — enrichment and querying.

Turns a raw request (IP + User-Agent + whatever the beacon reported) into a
stored row: browser/OS/device, geography, network operator, and the hardware
signals the browser exposes.

Two things worth knowing before changing this:

* **Geo is resolved locally** from a MaxMind GeoLite2 .mmdb file if one is
  present. It deliberately does not call a third-party geo API — that would
  hand every one of your visitors' IPs to someone else, which is a strange
  thing for a site that advertises "runs entirely on infrastructure you
  control". With no .mmdb installed, geo fields are simply null.

* **Visitor identity is a rotating hash**, not a cookie: SHA-256 over
  (ip + user-agent + a salt that changes daily). That gives accurate
  unique-visitor counts within a day without planting anything on the
  visitor's machine. Set ANALYTICS_ID_MODE=persistent to use a stable salt
  instead, which lets you follow a visitor across days — see the note in
  config.py about what that implies.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from .. import db

# ─────────────────────────── user agent ───────────────────────────

_BOT_RE = re.compile(
    r"(bot|crawl|spider|slurp|curl|wget|python-requests|httpx|headless|"
    r"phantomjs|puppeteer|playwright|lighthouse|pingdom|uptime|monitor|"
    r"facebookexternalhit|preview|scraper|fetch)",
    re.I,
)

# Order matters — Edge and Opera both claim Chrome, Chrome claims Safari.
_BROWSERS = [
    ("Edge", r"Edg(?:e|A|iOS)?/([\d.]+)"),
    ("Opera", r"OPR/([\d.]+)"),
    ("Samsung Internet", r"SamsungBrowser/([\d.]+)"),
    ("Vivaldi", r"Vivaldi/([\d.]+)"),
    ("Brave", r"Brave/([\d.]+)"),
    ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
    ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"),
    ("Safari", r"Version/([\d.]+).*Safari"),
    ("Tor Browser", r"Tor/([\d.]+)"),
]

_OSES = [
    ("Windows", r"Windows NT ([\d.]+)"),
    ("Android", r"Android ([\d.]+)"),
    ("iOS", r"(?:iPhone )?OS ([\d_]+) like Mac"),
    ("macOS", r"Mac OS X ([\d_.]+)"),
    ("ChromeOS", r"CrOS \S+ ([\d.]+)"),
    ("Linux", r"(Linux)"),
]

_WIN_NT = {
    "10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7",
}


def parse_ua(ua: str) -> dict[str, Any]:
    """Best-effort User-Agent breakdown.

    Deliberately regex-based rather than pulling in a UA database: we only need
    coarse buckets (which browser, which OS, phone vs desktop) and a vendored
    regex set that never updates is worse than one you can read.
    """
    ua = ua or ""
    out: dict[str, Any] = {
        "browser": None, "browser_version": None,
        "os": None, "os_version": None,
        "device_type": "desktop", "device_model": None,
        "is_bot": 1 if _BOT_RE.search(ua) else 0,
    }
    if not ua:
        out["is_bot"] = 1
        return out

    for name, pat in _BROWSERS:
        m = re.search(pat, ua)
        if m:
            out["browser"] = name
            out["browser_version"] = m.group(1)
            break

    for name, pat in _OSES:
        m = re.search(pat, ua)
        if m:
            out["os"] = name
            v = m.group(1).replace("_", ".")
            if name == "Windows":
                v = _WIN_NT.get(v, v)
            out["os_version"] = None if v == "Linux" else v
            break

    if re.search(r"iPad|Tablet|PlayBook|Silk", ua, re.I):
        out["device_type"] = "tablet"
    elif re.search(r"Mobi|iPhone|Android.*Mobile|Windows Phone", ua, re.I):
        out["device_type"] = "mobile"
    elif out["is_bot"]:
        out["device_type"] = "bot"

    m = re.search(r"\(Linux; Android [\d.]+; ([^;)]+)", ua)
    if m:
        out["device_model"] = m.group(1).strip()
    elif "iPhone" in ua:
        out["device_model"] = "iPhone"
    elif "iPad" in ua:
        out["device_model"] = "iPad"

    return out


# ─────────────────────────── geography ───────────────────────────

_geo_reader = None
_geo_asn_reader = None
_geo_tried = False


def _load_geo():
    """Open the MaxMind readers once. Missing DB is not an error."""
    global _geo_reader, _geo_asn_reader, _geo_tried
    if _geo_tried:
        return
    _geo_tried = True
    try:
        import geoip2.database  # type: ignore
    except ImportError:
        return
    base = Path(settings.geoip_dir).expanduser()
    if not base.is_absolute():
        base = Path(__file__).resolve().parent.parent.parent / base
    city = base / "GeoLite2-City.mmdb"
    asn = base / "GeoLite2-ASN.mmdb"
    try:
        if city.exists():
            _geo_reader = geoip2.database.Reader(str(city))
    except Exception:
        pass
    try:
        if asn.exists():
            _geo_asn_reader = geoip2.database.Reader(str(asn))
    except Exception:
        pass


def geo_lookup(ip: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "country": None, "country_code": None, "region": None, "city": None,
        "latitude": None, "longitude": None, "timezone_geo": None,
        "asn": None, "org": None,
    }
    if not ip or is_private(ip):
        return out
    _load_geo()
    if _geo_reader is not None:
        try:
            r = _geo_reader.city(ip)
            out.update(
                country=r.country.name,
                country_code=r.country.iso_code,
                region=(r.subdivisions.most_specific.name if r.subdivisions else None),
                city=r.city.name,
                latitude=r.location.latitude,
                longitude=r.location.longitude,
                timezone_geo=r.location.time_zone,
            )
        except Exception:
            pass
    if _geo_asn_reader is not None:
        try:
            a = _geo_asn_reader.asn(ip)
            out.update(
                asn=f"AS{a.autonomous_system_number}",
                org=a.autonomous_system_organization,
            )
        except Exception:
            pass
    return out


def is_private(ip: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


def client_ip(request) -> str:
    """Real client IP behind Caddy/nginx.

    Only trusts forwarding headers when the immediate peer is a private address
    (i.e. our own reverse proxy). Otherwise a visitor could spoof their own
    X-Forwarded-For and poison the log.
    """
    peer = request.client.host if request.client else ""
    if peer and not is_private(peer):
        return peer
    for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        v = request.headers.get(header)
        if v:
            return v.split(",")[0].strip()
    return peer


# ─────────────────────────── identity ───────────────────────────

def visitor_id(ip: str, ua: str) -> str:
    if settings.analytics_id_mode == "persistent":
        salt = settings.session_secret
    else:
        salt = settings.session_secret + datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(f"{salt}|{ip}|{ua}".encode()).hexdigest()[:32]


def hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{settings.session_secret}|{ip}".encode()).hexdigest()[:32]


def store_ip(ip: str) -> str | None:
    """Apply the configured IP retention policy."""
    mode = settings.analytics_ip_mode
    if mode == "none":
        return None
    if mode == "anonymized":
        # Zero the last octet (v4) / last 80 bits (v6) — the GDPR-friendly form
        # most analytics tools use. Still tells you the network, not the person.
        if ":" in ip:
            parts = ip.split(":")
            return ":".join(parts[:3]) + "::" if len(parts) > 3 else ip
        parts = ip.split(".")
        return ".".join(parts[:3] + ["0"]) if len(parts) == 4 else ip
    return ip


# ─────────────────────────── record ───────────────────────────

_ALLOWED_EVENTS = {"pageview", "click", "session_start", "outbound", "download"}


def record(request, payload: dict) -> None:
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")
    parsed = parse_ua(ua)

    if parsed["is_bot"] and not settings.analytics_track_bots:
        return

    event = str(payload.get("event") or "pageview")
    if event not in _ALLOWED_EVENTS:
        event = "pageview"

    def _i(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _s(v, n=512):
        return str(v)[:n] if v not in (None, "") else None

    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "visitor_id": visitor_id(ip, ua),
        "session_id": _s(payload.get("session_id"), 64),
        "ip": store_ip(ip),
        "ip_hash": hash_ip(ip),
        "user_agent": _s(ua, 512),
        **parsed,
        **geo_lookup(ip),
        "path": _s(payload.get("path"), 512),
        "query": _s(payload.get("query"), 512),
        "referrer": _s(payload.get("referrer"), 512),
        "title": _s(payload.get("title"), 256),
        "screen_w": _i(payload.get("screen_w")),
        "screen_h": _i(payload.get("screen_h")),
        "viewport_w": _i(payload.get("viewport_w")),
        "viewport_h": _i(payload.get("viewport_h")),
        "pixel_ratio": _f(payload.get("pixel_ratio")),
        "color_depth": _i(payload.get("color_depth")),
        "cpu_cores": _i(payload.get("cpu_cores")),
        "device_memory": _f(payload.get("device_memory")),
        "gpu": _s(payload.get("gpu"), 256),
        "tz_client": _s(payload.get("timezone"), 64),
        "language": _s(payload.get("language"), 32),
        "languages": _s(payload.get("languages"), 128),
        "touch_points": _i(payload.get("touch_points")),
        "connection": _s(payload.get("connection"), 32),
        "meta": json.dumps(payload.get("meta"))[:1024] if payload.get("meta") else None,
    }
    db.insert_visit(row)


# ─────────────────────────── query ───────────────────────────

def _bots_clause(include_bots: bool) -> str:
    return "" if include_bots else " AND is_bot = 0"


def recent(limit: int = 200, include_bots: bool = False) -> list[dict]:
    return db.query(
        f"SELECT * FROM visits WHERE 1=1{_bots_clause(include_bots)} "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def summary(days: int = 7, include_bots: bool = False) -> dict:
    since = f"-{int(days)} days"
    bots = _bots_clause(include_bots)

    totals = db.query(
        f"SELECT COUNT(*) AS events, COUNT(DISTINCT visitor_id) AS visitors, "
        f"COUNT(DISTINCT session_id) AS sessions "
        f"FROM visits WHERE ts >= datetime('now', ?){bots}",
        (since,),
    )[0]

    def top(col: str, n: int = 10):
        return db.query(
            f"SELECT {col} AS label, COUNT(*) AS n, "
            f"COUNT(DISTINCT visitor_id) AS visitors "
            f"FROM visits WHERE ts >= datetime('now', ?){bots} AND {col} IS NOT NULL "
            f"GROUP BY {col} ORDER BY n DESC LIMIT {int(n)}",
            (since,),
        )

    by_day = db.query(
        f"SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n, "
        f"COUNT(DISTINCT visitor_id) AS visitors "
        f"FROM visits WHERE ts >= datetime('now', ?){bots} "
        f"GROUP BY day ORDER BY day",
        (since,),
    )

    bot_count = db.query(
        "SELECT COUNT(*) AS n FROM visits WHERE ts >= datetime('now', ?) AND is_bot = 1",
        (since,),
    )[0]["n"]

    return {
        "days": days,
        "events": totals["events"],
        "visitors": totals["visitors"],
        "sessions": totals["sessions"],
        "bots_filtered": bot_count,
        "by_day": by_day,
        "top_pages": top("path"),
        "top_countries": top("country"),
        "top_referrers": top("referrer"),
        "top_browsers": top("browser"),
        "top_os": top("os"),
        "top_devices": top("device_type"),
        "top_orgs": top("org"),
        "geo_enabled": _geo_reader is not None,
    }


def visitor_detail(vid: str) -> dict:
    rows = db.query(
        "SELECT * FROM visits WHERE visitor_id = ? ORDER BY id DESC LIMIT 500", (vid,)
    )
    return {"visitor_id": vid, "events": len(rows), "timeline": rows}

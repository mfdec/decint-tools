"""IP lookup — where an address is, who runs it, and who it is registered to.

Every source is free and needs no key:

* **Location and ASN** come from local .mmdb files in GEOIP_DIR, so the
  address never leaves this server for them. DB-IP's Lite databases
  (db-ip.com, CC BY 4.0, city-level) are the default and are fetched here when
  missing and again each month (`update_databases`). MaxMind GeoLite2 files in
  the same directory are preferred when present; the visitor analytics already
  read those.
* **Registration** comes from RDAP, the registries' JSON successor to whois.
  rdap.org redirects to the registry that holds the address (ARIN, RIPE NCC,
  APNIC, LACNIC, AFRINIC), which answers with the registered range, its
  holder and the abuse contact. That registry sees the address.
* **Reverse DNS** goes through this server's resolver, and a PTR name is only
  called confirmed when it resolves back to the address.
* **Tor exit** status is checked against the Tor Project's bulk exit list,
  fetched at most hourly and held in memory.
* **Reputation** comes from AbuseIPDB's community abuse reports when
  IPLOOKUP_ABUSEIPDB_KEY is set (free plan: 1,000 checks a day). AbuseIPDB
  sees the address.

Private and reserved addresses are classified and nothing more: no registry
has anything to say about 10.0.0.1, and asking would only leak it.

RDAP and AbuseIPDB answers are kept in memory for IPLOOKUP_CACHE_TTL, because
registries throttle repeat queries and AbuseIPDB's checks are rationed.
Nothing is written to disk or logged.
"""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import settings
from ..models import (
    IpLocation, IpLookupResponse, IpNetwork, IpRegistration, IpReputation, IpResult, IpSource,
)
from . import sourcehealth

log = logging.getLogger("decint.iplookup")

_UA = {"User-Agent": "decint-tools/1.0 (ip lookup)"}

DBIP_URL = "https://download.db-ip.com/free/dbip-{kind}-lite-{year:04d}-{month:02d}.mmdb.gz"
# Preferred first. GeoLite2 is the more accurate of the two where an operator
# has gone to the trouble of a MaxMind account; DB-IP needs none.
CITY_FILES = ("GeoLite2-City.mmdb", "dbip-city-lite.mmdb")
ASN_FILES = ("GeoLite2-ASN.mmdb", "dbip-asn-lite.mmdb")
DBIP_FILES = {"city": "dbip-city-lite.mmdb", "asn": "dbip-asn-lite.mmdb"}
# The City Lite archive is ~60 MB and unpacks to ~130 MB.
_MAX_DOWNLOAD = 300 * 1024 * 1024
_MAX_UNPACKED = 800 * 1024 * 1024

_REGISTRIES = {
    "arin": "ARIN", "ripe": "RIPE NCC", "apnic": "APNIC",
    "lacnic": "LACNIC", "afrinic": "AFRINIC",
}

_DOCUMENTATION = [
    ipaddress.ip_network(n) for n in
    ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32", "3fff::/20")
]
_SHARED = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class LookupFailed(Exception):
    """A source could not answer. The message says why, for the console."""


# ─────────────────────────── the target ───────────────────────────

@dataclass
class Target:
    kind: str  # "ip" | "hostname"
    query: str  # as normalised
    ips: list[str] = field(default_factory=list)
    more: list[str] = field(default_factory=list)  # resolved but not looked up


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        addr = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def parse_target(raw: str) -> tuple[str, str]:
    """("ip", address) or ("hostname", name), or ValueError saying what's wrong.

    Takes what people paste: a bare address, `1.2.3.4:443`, `[2001:db8::1]:443`,
    a hostname, or a URL, from which the host is used."""
    v = raw.strip()
    if not v:
        raise ValueError("Enter an IP address or a hostname.")
    if "://" in v:
        try:
            v = urlsplit(v).hostname or ""
        except ValueError:
            v = ""
        if not v:
            raise ValueError("That URL has no host in it.")
    elif _looks_like_cidr(v):
        raise ValueError("Look up one address at a time, not a range.")
    elif v.startswith("["):  # [v6] or [v6]:port
        v = v[1:].split("]", 1)[0]
    else:
        v = v.split("/", 1)[0]  # example.com/some/path

    addr = _as_ip(v)
    if addr is None and v.count(":") == 1:  # host:port or v4:port
        v = v.rsplit(":", 1)[0]
        addr = _as_ip(v)
    if addr is not None:
        return "ip", str(addr)

    host = v.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("That is not a valid hostname.") from None
    labels = host.split(".")
    if (
        len(host) > 253 or len(labels) < 2
        or not all(_LABEL.match(lb) for lb in labels)
        or labels[-1].isdigit()
    ):
        raise ValueError("Enter an IP address (v4 or v6) or a hostname such as example.com.")
    return "hostname", host


def _looks_like_cidr(v: str) -> bool:
    head, sep, tail = v.partition("/")
    return bool(sep) and tail.isdigit() and _as_ip(head) is not None


async def resolve_target(raw: str) -> Target:
    """Parse, and for a hostname resolve it. ValueError when there is nothing
    to look up — before anything is charged."""
    kind, value = parse_target(raw)
    if kind == "ip":
        return Target(kind="ip", query=value, ips=[value])

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(value, None, type=socket.SOCK_STREAM),
            timeout=settings.iplookup_timeout,
        )
    except (socket.gaierror, UnicodeError):
        raise ValueError(f"{value} does not resolve to any address.") from None
    except asyncio.TimeoutError:
        raise ValueError(f"Resolving {value} timed out.") from None

    seen: dict[str, int] = {}
    for *_, sockaddr in infos:
        addr = _as_ip(str(sockaddr[0]))
        if addr is not None:
            seen.setdefault(str(addr), addr.version)
    if not seen:
        raise ValueError(f"{value} does not resolve to any address.")
    # IPv4 first: it is what most people mean, and what the databases know best.
    ordered = sorted(seen, key=lambda ip: seen[ip])
    n = max(1, settings.iplookup_max_addresses)
    return Target(kind="hostname", query=value, ips=ordered[:n], more=ordered[n:])


def scope_of(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link_local"
    if addr.is_multicast:
        return "multicast"
    if any(addr in n for n in _DOCUMENTATION):
        return "documentation"
    if addr.version == 4 and addr in _SHARED:
        return "shared"
    if addr.is_reserved:  # before private: Python counts 240.0.0.0/4 as both
        return "reserved"
    if addr.is_private:
        return "private"
    return "public" if addr.is_global else "reserved"


# ─────────────────────────── local databases ───────────────────────────

def geo_dir() -> Path:
    base = Path(settings.geoip_dir).expanduser()
    if not base.is_absolute():
        base = Path(__file__).resolve().parent.parent.parent / base
    return base


class _Mmdb:
    """The first of `names` present in GEOIP_DIR, reopened whenever the file on
    disk changes — so the monthly update takes effect without a restart."""

    def __init__(self, names: tuple[str, ...]):
        self.names = names
        self._reader: Any = None
        self._key: tuple | None = None

    def get(self) -> Any:
        import maxminddb

        for name in self.names:
            path = geo_dir() / name
            try:
                st = path.stat()
            except OSError:
                continue
            key = (str(path), st.st_mtime_ns, st.st_size)
            if key != self._key:
                try:
                    reader = maxminddb.open_database(str(path))
                except Exception as e:
                    log.warning("cannot open %s: %s", path, e)
                    continue
                old, self._reader, self._key = self._reader, reader, key
                if old is not None:
                    old.close()
            return self._reader
        return None

    def kind(self) -> str | None:
        """'geolite2' or 'dbip', for the attribution each licence asks for."""
        r = self.get()
        if r is None:
            return None
        return "geolite2" if r.metadata().database_type.startswith("GeoLite2") else "dbip"


_CITY = _Mmdb(CITY_FILES)
_ASN = _Mmdb(ASN_FILES)


def _name(obj: dict | None) -> str | None:
    if not obj:
        return None
    return (obj.get("names") or {}).get("en")


def _location(ip: str) -> IpLocation | None:
    reader = _CITY.get()
    if reader is None:
        raise LookupFailed("no location database installed")
    rec = reader.get(ip)
    if not rec:
        return None
    loc = rec.get("location") or {}
    country = rec.get("country") or {}
    subs = rec.get("subdivisions") or []
    out = IpLocation(
        city=_name(rec.get("city")),
        region=_name(subs[0]) if subs else None,
        country=_name(country),
        country_code=country.get("iso_code"),
        continent=_name(rec.get("continent")),
        in_eu=country.get("is_in_european_union"),
        latitude=loc.get("latitude"),
        longitude=loc.get("longitude"),
        accuracy_km=loc.get("accuracy_radius"),
        timezone=loc.get("time_zone"),
    )
    return out if (out.country or out.city or out.latitude is not None) else None


def _network(ip: str) -> IpNetwork | None:
    reader = _ASN.get()
    if reader is None:
        raise LookupFailed("no ASN database installed")
    rec, plen = reader.get_with_prefix_len(ip)
    if not rec:
        return None
    return IpNetwork(
        asn=rec.get("autonomous_system_number"),
        as_org=rec.get("autonomous_system_organization"),
        prefix=str(ipaddress.ip_network(f"{ip}/{plen}", strict=False)),
    )


# ─────────────────────────── RDAP ───────────────────────────

_rdap_cache: dict[str, tuple[float, IpRegistration]] = {}
_RDAP_CACHE_MAX = 4096


def _vcard(entity: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"emails": []}
    try:
        props = entity["vcardArray"][1]
    except (KeyError, IndexError, TypeError):
        return out
    for prop in props:
        if not isinstance(prop, list) or len(prop) < 4:
            continue
        name, params, _type, value = prop[0], prop[1] or {}, prop[2], prop[3]
        if name == "fn" and isinstance(value, str) and value.strip():
            out.setdefault("fn", value.strip())
        elif name == "kind" and isinstance(value, str):
            out.setdefault("kind", value)
        elif name == "email" and isinstance(value, str):
            kind = params.get("type") if isinstance(params, dict) else None
            out["emails"].append((value.strip(), kind))
        elif name == "adr":
            label = params.get("label") if isinstance(params, dict) else None
            if isinstance(label, str) and label.strip():
                out.setdefault("adr", ", ".join(x.strip() for x in label.splitlines() if x.strip()))
    return out


def _entities(entities: Any):
    for e in entities or []:
        if isinstance(e, dict):
            yield e
            yield from _entities(e.get("entities"))


def parse_rdap(data: dict, url_host: str = "") -> IpRegistration:
    ents = list(_entities(data.get("entities")))

    def roles(e: dict) -> list[str]:
        return [str(r).lower() for r in e.get("roles") or []]

    # The holder: a registrant that is an organisation, else any registrant
    # with a name (RIPE lists the maintainer as a registrant too, as kind
    # "individual"), else whoever administers the range.
    org_ent = None
    registrants = [e for e in ents if "registrant" in roles(e) and _vcard(e).get("fn")]
    for e in registrants:
        if _vcard(e).get("kind") == "org":
            org_ent = e
            break
    if org_ent is None and registrants:
        org_ent = registrants[0]
    if org_ent is None:
        org_ent = next(
            (e for e in ents if "administrative" in roles(e) and _vcard(e).get("fn")), None
        )
    org = _vcard(org_ent) if org_ent else {}

    abuse = None
    for e in ents:
        if "abuse" in roles(e):
            emails = _vcard(e)["emails"]
            if emails:
                abuse = emails[0][0]
                break
    if abuse is None:
        abuse = next(
            (m for e in ents for m, t in _vcard(e)["emails"] if t == "abuse"), None
        )

    events: dict[str, str] = {}
    for ev in data.get("events") or []:
        if isinstance(ev, dict) and ev.get("eventAction") and ev.get("eventDate"):
            events.setdefault(str(ev["eventAction"]).lower(), str(ev["eventDate"]))

    cidrs = []
    for c in data.get("cidr0_cidrs") or []:
        prefix = c.get("v4prefix") or c.get("v6prefix")
        if prefix and c.get("length") is not None:
            cidrs.append(f"{prefix}/{c['length']}")

    registry = None
    hint = f"{data.get('port43') or ''} {url_host}".lower()
    for key, label in _REGISTRIES.items():
        if key in hint:
            registry = label
            break

    start, end = data.get("startAddress"), data.get("endAddress")
    return IpRegistration(
        registry=registry,
        handle=data.get("handle"),
        name=data.get("name"),
        type=data.get("type"),
        range=f"{start} - {end}" if start and end else None,
        cidrs=cidrs,
        country=data.get("country"),
        org=org.get("fn"),
        org_address=org.get("adr"),
        abuse_email=abuse,
        registered=events.get("registration"),
        last_changed=events.get("last changed"),
    )


async def _rdap(client: httpx.AsyncClient, ip: str) -> IpRegistration:
    hit = _rdap_cache.get(ip)
    if hit and time.monotonic() - hit[0] < settings.iplookup_cache_ttl:
        return hit[1]
    reg = await _rdap_fetch(client, ip)
    if len(_rdap_cache) >= _RDAP_CACHE_MAX:
        for k in sorted(_rdap_cache, key=lambda k: _rdap_cache[k][0])[: _RDAP_CACHE_MAX // 4]:
            _rdap_cache.pop(k, None)
    _rdap_cache[ip] = (time.monotonic(), reg)
    return reg


@sourcehealth.tracked(
    "ip", "rdap", "RDAP (rdap.org)", safe=(LookupFailed,),
    # No record for an address is the registry answering.
    answered=lambda e: "no registry record" in str(e),
)
async def _rdap_fetch(client: httpx.AsyncClient, ip: str) -> IpRegistration:
    try:
        r = await client.get(
            settings.iplookup_rdap_url + ip,
            headers={**_UA, "Accept": "application/rdap+json, application/json"},
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        raise LookupFailed(f"RDAP: {type(e).__name__}") from e
    if r.status_code == 404:
        raise LookupFailed("RDAP: no registry record for this address")
    if r.status_code == 429:
        raise LookupFailed("RDAP: the registry is rate-limiting us, try again shortly")
    if r.status_code >= 400:
        raise LookupFailed(f"RDAP: HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as e:
        raise LookupFailed("RDAP: unexpected response") from e
    if not isinstance(data, dict) or data.get("objectClassName") not in (None, "ip network"):
        raise LookupFailed("RDAP: unexpected response")
    return parse_rdap(data, r.url.host or "")


# ─────────────────────────── reverse DNS ───────────────────────────

async def _ptr(ip: str) -> tuple[str | None, bool | None]:
    """(name, confirmed). No PTR record is an answer, not a failure."""
    loop = asyncio.get_running_loop()
    timeout = min(settings.iplookup_timeout, 5.0)
    try:
        host, _ = await asyncio.wait_for(
            loop.getnameinfo((ip, 0), socket.NI_NAMEREQD), timeout=timeout
        )
    except (socket.gaierror, socket.herror, OSError):
        return None, None
    except asyncio.TimeoutError as e:
        raise LookupFailed("reverse DNS timed out") from e
    host = host.rstrip(".")
    if not host or host == ip:
        return None, None
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=timeout
        )
    except (socket.gaierror, OSError, UnicodeError, asyncio.TimeoutError):
        return host, False
    back = {str(_as_ip(str(i[4][0]))) for i in infos}
    return host, ip in back


# ─────────────────────────── Tor exits ───────────────────────────

_tor: dict[str, Any] = {"ips": None, "fetched": 0.0, "tried": 0.0}
_tor_lock = asyncio.Lock()
_TOR_TTL = 3600.0
_TOR_RETRY = 300.0
_TOR_LABEL = "Tor bulk exit list"
sourcehealth.register("ip", "tor_list", _TOR_LABEL)


async def _tor_exits(client: httpx.AsyncClient) -> frozenset[str] | None:
    """The current exit list, or None if it has never been fetched."""
    now = time.monotonic()
    if _tor["ips"] is not None and now - _tor["fetched"] < _TOR_TTL:
        return _tor["ips"]
    async with _tor_lock:
        now = time.monotonic()
        fresh = _tor["ips"] is not None and now - _tor["fetched"] < _TOR_TTL
        if fresh or now - _tor["tried"] < _TOR_RETRY:
            return _tor["ips"]
        _tor["tried"] = now
        t = sourcehealth.Timer()
        try:
            r = await client.get(settings.iplookup_tor_list_url, headers=_UA)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.info("tor exit list unavailable: %s", type(e).__name__)
            sourcehealth.record("ip", "tor_list", _TOR_LABEL, False, error=type(e).__name__)
            return _tor["ips"]  # a stale list beats none
        ips = set()
        for line in r.text.splitlines():
            addr = _as_ip(line.strip())
            if addr is not None:
                ips.add(str(addr))
        if ips:
            _tor["ips"], _tor["fetched"] = frozenset(ips), now
            sourcehealth.record("ip", "tor_list", _TOR_LABEL, True, latency_ms=t.ms)
        else:
            sourcehealth.record("ip", "tor_list", _TOR_LABEL, False, error="the list came back empty")
        return _tor["ips"]


# ─────────────────────────── reputation (AbuseIPDB) ───────────────────────────

_ABUSE_LABEL = "AbuseIPDB"
_abuse_cache: dict[str, tuple[float, IpReputation]] = {}
# AbuseIPDB is not asked again before this (epoch seconds). Set from its own
# reset time when it says the day's checks are spent, so a spent quota costs
# one refused call rather than one per lookup.
_abuse_pause: dict[str, float] = {"until": 0.0}


def reputation_enabled() -> bool:
    return bool(settings.iplookup_abuseipdb_key.strip())


async def _reputation(client: httpx.AsyncClient, ip: str) -> IpReputation:
    hit = _abuse_cache.get(ip)
    if hit and time.monotonic() - hit[0] < settings.iplookup_cache_ttl:
        return hit[1]
    if time.time() < _abuse_pause["until"]:
        raise LookupFailed("AbuseIPDB: today's checks are used up")
    rep = await _abuseipdb(client, ip)
    if len(_abuse_cache) >= _RDAP_CACHE_MAX:
        for k in sorted(_abuse_cache, key=lambda k: _abuse_cache[k][0])[: _RDAP_CACHE_MAX // 4]:
            _abuse_cache.pop(k, None)
    _abuse_cache[ip] = (time.monotonic(), rep)
    return rep


def _quota_reset(r: httpx.Response) -> float:
    """When AbuseIPDB will take checks again, kept within a day from now."""
    now = time.time()
    when = now + 3600
    try:
        when = float(r.headers["X-RateLimit-Reset"])
    except (KeyError, ValueError):
        try:
            when = now + float(r.headers["Retry-After"])
        except (KeyError, ValueError):
            pass
    return min(max(when, now + 60), now + 86400 + 60)


@sourcehealth.tracked("ip", "abuseipdb", _ABUSE_LABEL, safe=(LookupFailed,))
async def _abuseipdb(client: httpx.AsyncClient, ip: str) -> IpReputation:
    max_age = max(1, min(365, settings.iplookup_abuseipdb_max_age))
    try:
        r = await client.get(
            settings.iplookup_abuseipdb_url,
            params={"ipAddress": ip, "maxAgeInDays": max_age},
            headers={
                **_UA,
                "Key": settings.iplookup_abuseipdb_key.strip(),
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as e:
        raise LookupFailed(f"AbuseIPDB: {type(e).__name__}") from e
    if r.status_code == 429:
        _abuse_pause["until"] = _quota_reset(r)
        raise LookupFailed("AbuseIPDB: today's checks are used up")
    if r.status_code in (401, 403):
        raise LookupFailed("AbuseIPDB: the API key was refused")
    if r.status_code >= 400:
        raise LookupFailed(f"AbuseIPDB: HTTP {r.status_code}")
    try:
        d = r.json()["data"]
        return IpReputation(
            abuse_score=int(d["abuseConfidenceScore"]),
            total_reports=int(d.get("totalReports") or 0),
            distinct_reporters=int(d.get("numDistinctUsers") or 0),
            last_reported=d.get("lastReportedAt") or None,
            max_age_days=max_age,
            usage_type=d.get("usageType") or None,
            isp=d.get("isp") or None,
            domain=d.get("domain") or None,
            whitelisted=d.get("isWhitelisted"),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise LookupFailed("AbuseIPDB: unexpected response") from e


# ─────────────────────────── the lookup ───────────────────────────

def attribution() -> list[str]:
    out = []
    kinds = {_CITY.kind(), _ASN.kind()}
    if "dbip" in kinds:
        out.append("IP geolocation by DB-IP (db-ip.com), CC BY 4.0")
    if "geolite2" in kinds:
        out.append("GeoLite2 data created by MaxMind (maxmind.com)")
    out.append("Registration data from the regional internet registries via RDAP (rdap.org)")
    out.append("Tor exit list from the Tor Project")
    if reputation_enabled():
        out.append("IP reputation from AbuseIPDB (abuseipdb.com) community reports")
    return out


async def lookup(target: Target) -> IpLookupResponse:
    """Look up every address in `target`. A source that fails is reported
    against the address, never passed off as "nothing found"."""
    any_public = any(scope_of(ipaddress.ip_address(ip)) == "public" for ip in target.ips)
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.iplookup_timeout)) as client:
        tor = await _tor_exits(client) if any_public else None

        async def one(ip: str) -> IpResult:
            addr = ipaddress.ip_address(ip)
            res = IpResult(ip=ip, version=addr.version, scope=scope_of(addr))
            if res.scope != "public":
                return res
            for key, fn, attr in (("location", _location, "location"),
                                  ("network", _network, "network")):
                try:
                    setattr(res, attr, fn(ip))
                except LookupFailed as e:
                    res.errors[key] = str(e)
                except Exception as e:  # a corrupt .mmdb must not sink the rest
                    res.errors[key] = f"{key} database error: {type(e).__name__}"

            calls = [_rdap(client, ip), _ptr(ip)]
            if reputation_enabled():
                calls.append(_reputation(client, ip))
            rdap, ptr, *rep = await asyncio.gather(*calls, return_exceptions=True)
            if rep:
                if isinstance(rep[0], IpReputation):
                    res.reputation = rep[0]
                else:
                    res.errors["reputation"] = (
                        str(rep[0]) if isinstance(rep[0], LookupFailed) else "AbuseIPDB failed"
                    )
            if isinstance(rdap, IpRegistration):
                res.registration = rdap
            else:
                res.errors["rdap"] = str(rdap) if isinstance(rdap, LookupFailed) else "RDAP failed"
            if isinstance(ptr, tuple):
                res.ptr, res.ptr_confirmed = ptr
            else:
                res.errors["ptr"] = str(ptr) if isinstance(ptr, LookupFailed) else "reverse DNS failed"
            res.tor_exit = None if tor is None else ip in tor
            return res

        results = list(await asyncio.gather(*(one(ip) for ip in target.ips)))

    public = [r for r in results if r.scope == "public"]
    sources = [
        _source("location", _db_label(_CITY, "location"), public),
        _source("network", _db_label(_ASN, "ASN"), public),
        _source("rdap", "RDAP registry", public),
        _source("ptr", "reverse DNS", public),
        *([_source("reputation", "AbuseIPDB reputation", public)] if reputation_enabled() else []),
        IpSource(
            key="tor", label="Tor exit list", ok=tor is not None or not public,
            status=(f"{len(tor):,} exits listed" if tor is not None
                    else "could not be fetched" if public else "not needed: no public address"),
        ),
    ]
    return IpLookupResponse(
        query=target.query,
        kind=target.kind,  # type: ignore[arg-type]
        hostname=target.query if target.kind == "hostname" else None,
        more_addresses=target.more,
        results=results,
        sources=sources,
        attribution=attribution(),
    )


def _db_label(db: _Mmdb, what: str) -> str:
    kind = db.kind()
    return f"{what} · {'GeoLite2' if kind == 'geolite2' else 'DB-IP Lite' if kind else 'no database'}"


def _source(key: str, label: str, public: list[IpResult]) -> IpSource:
    if not public:
        return IpSource(key=key, label=label, ok=True, status="not needed: no public address")
    failed = [r.errors[key] for r in public if key in r.errors]
    if not failed:
        return IpSource(key=key, label=label, ok=True, status=f"answered for {len(public)}")
    return IpSource(key=key, label=label, ok=len(failed) < len(public), status=failed[0])


def answered(resp: IpLookupResponse) -> bool:
    """Did any source say anything? A lookup that learned nothing — every
    address private, or every source down — is not charged as a search."""
    return any(r.location or r.network or r.registration or r.reputation for r in resp.results)


# ─────────────────────────── DB-IP updates ───────────────────────────

class _NotPublished(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _edition_file(path: Path) -> Path:
    return path.with_name(path.name + ".edition")


def installed_edition(path: Path) -> tuple[int, int] | None:
    """(year, month) of the DB-IP edition at `path`. Read from the note written
    on install; for a file put there by hand, from its build date (an edition
    is built a day or so before the month it is named for)."""
    try:
        y, m = _edition_file(path).read_text().strip().split("-")
        return int(y), int(m)
    except (OSError, ValueError):
        pass
    try:
        import maxminddb

        with maxminddb.open_database(str(path)) as r:
            built = datetime.fromtimestamp(r.metadata().build_epoch, timezone.utc)
    except Exception:
        return None
    built += timedelta(days=5)
    return built.year, built.month


def _unpack(gz: Path, dest: Path, kind: str) -> None:
    import maxminddb

    written = 0
    with gzip.open(gz, "rb") as src, open(dest, "wb") as out:
        while chunk := src.read(1 << 20):
            written += len(chunk)
            if written > _MAX_UNPACKED:
                raise ValueError("archive unpacks larger than expected")
            out.write(chunk)
    with maxminddb.open_database(str(dest)) as r:
        dbtype = r.metadata().database_type
    want = "DBIP-City" if kind == "city" else "DBIP-ASN"
    if not dbtype.startswith(want):
        raise ValueError(f"expected a {want} database, got {dbtype}")


async def _fetch(client: httpx.AsyncClient, url: str, dest: Path, kind: str) -> None:
    gz = dest.with_name(dest.name + ".gz.part")
    part = dest.with_name(dest.name + ".part")
    try:
        async with client.stream("GET", url, headers=_UA) as r:
            if r.status_code == 404:
                raise _NotPublished(url)
            r.raise_for_status()
            size = 0
            with open(gz, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    size += len(chunk)
                    if size > _MAX_DOWNLOAD:
                        raise ValueError("download larger than expected")
                    f.write(chunk)
        await asyncio.to_thread(_unpack, gz, part, kind)
        os.replace(part, dest)
    finally:
        for p in (gz, part):
            p.unlink(missing_ok=True)


async def update_databases(force: bool = False) -> dict[str, str]:
    """Install this month's DB-IP Lite City and ASN databases where they are
    missing or out of date. {kind: what happened}. Never raises for a
    download problem; the old file stays in place."""
    base = geo_dir()
    base.mkdir(parents=True, exist_ok=True)
    now = _now()
    this = (now.year, now.month)
    last = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    report: dict[str, str] = {}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True
    ) as client:
        for kind, fname in DBIP_FILES.items():
            geolite = CITY_FILES[0] if kind == "city" else ASN_FILES[0]
            if (base / geolite).exists() and not force:
                report[kind] = f"skipped: {geolite} is installed and preferred"
                continue
            path = base / fname
            have = installed_edition(path) if path.exists() else None
            if have and have >= this and not force:
                report[kind] = f"current ({have[0]}-{have[1]:02d})"
                continue
            # This month's edition is published on the 1st or so; until it is,
            # last month's will do for a first install.
            wanted = [this] if have else [this, last]
            report[kind] = (
                f"kept {have[0]}-{have[1]:02d}: {this[0]}-{this[1]:02d} not published yet"
                if have else "not installed: no edition published"
            )
            for y, m in wanted:
                url = DBIP_URL.format(kind=kind, year=y, month=m)
                try:
                    await _fetch(client, url, path, kind)
                except _NotPublished:
                    continue
                except Exception as e:
                    report[kind] = f"failed: {type(e).__name__}: {e}"
                    log.warning("DB-IP %s update failed: %s", kind, e)
                    break
                _edition_file(path).write_text(f"{y}-{m:02d}\n")
                report[kind] = f"installed {y}-{m:02d}"
                log.info("DB-IP %s %d-%02d installed", kind, y, m)
                break
    return report


async def update_loop(interval_s: int = 86400) -> None:
    """Check once a day; DB-IP publishes a new edition each month."""
    await asyncio.sleep(20)  # out of the way of startup
    while True:
        try:
            await update_databases()
        except Exception:
            log.exception("DB-IP update failed")
        await asyncio.sleep(interval_s)

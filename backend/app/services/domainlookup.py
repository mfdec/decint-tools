"""Domain / website lookup — what a domain resolves to, who registered it, how
its mail is protected, which names it holds certificates for, and what its
website says about itself.

Every source is free and needs no key:

* **DNS** over DNS-over-HTTPS JSON APIs (Google's, then Cloudflare's when
  Google fails): A, AAAA, CNAME, MX, NS, TXT, CAA and SOA for the name, and
  the _dmarc / _mta-sts records that say how its mail is protected. The
  resolver sees the name.
* **Registration** over RDAP via rdap.org, which redirects to the registry for
  the TLD: registrar, dates, status codes, name servers, DNSSEC. It is asked
  about the registered domain, found from the zone apex in DNS, since
  registries hold no record for www.example.com.
* **Subdomains** from certificate-transparency logs: crt.sh, and Cert Spotter
  when crt.sh fails (it often does). Public CAs log every certificate they
  issue, so names turn up here that DNS would never list.
* **Website**: this server fetches the site (status, redirect chain, server
  and security headers, page title) and reads its TLS certificate. Only
  public addresses are connected to, and each connection is pinned to the
  address that was checked, so a lookup can't be pointed at this server's
  own network, by the name or by a redirect.
* **Hosting**: the addresses the name resolves to, placed with the IP lookup's
  local location/ASN databases. Nothing is sent anywhere for that.
* **Archive**: the first capture in the Wayback Machine: how long the site
  has been around, which the registration date alone doesn't say.

Registration, certificate and archive answers are kept in memory for
DOMAIN_CACHE_TTL. Nothing is written to disk or logged.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import re
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..config import settings
from ..models import (
    DnsRecord, DomainAddress, DomainArchive, DomainCertificates, DomainEmail,
    DomainLookupResponse, DomainRegistration, DomainTls, DomainWebsite, IpSource, WebHop,
)
from . import iplookup, sourcehealth

log = logging.getLogger("decint.domainlookup")

_UA = {"User-Agent": "decint-tools/1.0 (domain lookup)"}
# Some sites turn away anything that doesn't look like a browser; this still
# says what it is.
_SITE_UA = {"User-Agent": "Mozilla/5.0 (compatible; decint-tools/1.0; domain lookup)"}

RTYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "MX": 15, "TXT": 16, "AAAA": 28, "CAA": 257}
_RNAMES = {v: k for k, v in RTYPES.items()}

SECURITY_HEADERS = (
    "strict-transport-security", "content-security-policy", "x-frame-options",
    "x-content-type-options", "referrer-policy", "permissions-policy",
)
_MAX_HOPS = 5
_MAX_BODY = 256 * 1024
_MAX_CT_BYTES = 24 * 1024 * 1024  # crt.sh's answer for a big domain runs to hundreds of MB
_ADDRESSES = 4  # resolved addresses placed on the map
_PORTS = {80, 443, 8080, 8443}  # where a redirect may send the fetch
_CRTSH_REST = 600.0  # seconds crt.sh is skipped after it has failed repeatedly
_WAYBACK_REST = 600.0  # likewise the Wayback Machine, which is often slow to answer


class LookupFailed(Exception):
    """A source could not answer. The message says why, for the console."""


# ─────────────────────────── the target ───────────────────────────

def parse_target(raw: str) -> str:
    """The hostname to look up, or ValueError saying what's wrong. Takes what
    people paste: a domain, a URL (its host is used) or an email address (its
    domain is used)."""
    v = raw.strip()
    if not v:
        raise ValueError("Enter a domain, a URL or an email address.")
    if "://" in v:
        try:
            v = urlsplit(v).hostname or ""
        except ValueError:
            v = ""
        if not v:
            raise ValueError("That URL has no host in it.")
    else:
        if "@" in v and "/" not in v:
            v = v.rsplit("@", 1)[1]
        v = v.split("/", 1)[0].split("?", 1)[0]
        if v.count(":") == 1:  # host:port
            v = v.split(":", 1)[0]
    if iplookup._as_ip(v.strip("[]")) is not None:
        raise ValueError("That is an IP address: look it up with the IP tool (ip <address>).")

    host = v.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("That is not a valid domain name.") from None
    labels = host.split(".")
    if (
        len(host) > 253 or len(labels) < 2
        or not all(iplookup._LABEL.match(lb) for lb in labels)
        or labels[-1].isdigit()
    ):
        raise ValueError("Enter a domain such as example.com, a URL or an email address.")
    return host


def _parents(host: str) -> list[str]:
    """example.com's parents down to two labels: a.b.example.com -> b.example.com, example.com."""
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(1, len(labels) - 1)]


# ─────────────────────────── small TTL cache ───────────────────────────

class _Cache:
    def __init__(self, cap: int = 1024):
        self.cap = cap
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self._d.get(key)
        if hit and time.monotonic() - hit[0] < settings.domain_cache_ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        if len(self._d) >= self.cap:
            for k in sorted(self._d, key=lambda k: self._d[k][0])[: self.cap // 4]:
                self._d.pop(k, None)
        self._d[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._d.clear()


_rdap_cache = _Cache()
_ct_cache = _Cache()
_archive_cache = _Cache()


def clear_caches() -> None:
    for c in (_rdap_cache, _ct_cache, _archive_cache):
        c.clear()


# ─────────────────────────── DNS over HTTPS ───────────────────────────

def _doh_key(url: str) -> tuple[str, str]:
    host = urlsplit(url).hostname or url
    if "google" in host:
        return "doh_google", "DNS over HTTPS (Google)"
    if "cloudflare" in host:
        return "doh_cloudflare", "DNS over HTTPS (Cloudflare)"
    return "doh_" + re.sub(r"[^a-z0-9]+", "_", host), f"DNS over HTTPS ({host})"


for _u in settings.domain_doh_list:
    sourcehealth.register("domain", *_doh_key(_u))


async def _doh_one(client: httpx.AsyncClient, url: str, name: str, rtype: str) -> dict:
    key, label = _doh_key(url)
    t = sourcehealth.Timer()
    try:
        r = await client.get(
            url, params={"name": name, "type": rtype},
            headers={**_UA, "Accept": "application/dns-json"},
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or "Status" not in data:
            raise ValueError("no Status")
    except (httpx.HTTPError, ValueError) as e:
        sourcehealth.record("domain", key, label, False, error=type(e).__name__)
        raise LookupFailed(f"DNS: {label} failed ({type(e).__name__})") from e
    sourcehealth.record("domain", key, label, True, latency_ms=t.ms)
    return data


async def _doh(client: httpx.AsyncClient, name: str, rtype: str) -> dict:
    """The resolver's answer, from the first DoH service that gives one.
    SERVFAIL from one is retried on the next: it is often that resolver's
    own trouble (a DNSSEC or upstream fault) rather than the name's."""
    last: Exception | None = None
    answer: dict | None = None
    for url in settings.domain_doh_list:
        try:
            answer = await _doh_one(client, url, name, rtype)
        except LookupFailed as e:
            last = e
            continue
        if answer.get("Status") != 2:
            return answer
    if answer is not None:
        return answer
    raise last or LookupFailed("DNS: no DNS-over-HTTPS service is configured")


def _norm(name: str) -> str:
    return str(name).rstrip(".").lower()


def _txt(data: str) -> str:
    """TXT data as one string. Cloudflare quotes each character-string;
    Google sends them already joined."""
    data = str(data)
    if data.startswith('"'):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', data)
        return "".join(re.sub(r"\\(.)", r"\1", p) for p in parts)
    return data


def _caa(data: str) -> str:
    """CAA data as `flags tag "value"`. Cloudflare can send the RFC 3597
    generic form (`\\# 22 00 05 69 73 73 75 65 ...`)."""
    data = str(data)
    if not data.startswith("\\#"):
        return data
    try:
        raw = bytes.fromhex("".join(data.split()[2:]))
        flags, tlen = raw[0], raw[1]
        tag = raw[2:2 + tlen].decode("ascii")
        value = raw[2 + tlen:].decode("utf-8", "replace")
        return f'{flags} {tag} "{value}"'
    except (ValueError, IndexError):
        return data


def _records(answer: dict) -> list[DnsRecord]:
    out = []
    for rr in answer.get("Answer") or []:
        rtype = _RNAMES.get(rr.get("type"))
        if not rtype:
            continue
        value = str(rr.get("data", ""))
        if rtype == "TXT":
            value = _txt(value)
        elif rtype == "CAA":
            value = _caa(value)
        elif rtype in ("CNAME", "NS"):
            value = _norm(value)
        elif rtype == "MX":
            pref, _, host = value.partition(" ")
            value = f"{pref} {_norm(host) or '.'}"
        out.append(DnsRecord(type=rtype, name=_norm(rr.get("name", "")), value=value, ttl=rr.get("TTL")))
    return out


def _apex_from(name: str, answer: dict) -> str | None:
    """The zone `name` sits in, read from an SOA query's answer, or None when
    the answer can't say (a CNAME's SOA is the target's zone, not this one)."""
    rrs = answer.get("Answer") or []
    for rr in rrs:
        if rr.get("type") == 6 and _norm(rr.get("name", "")) == name:
            return name
    if any(rr.get("type") == 5 for rr in rrs):
        return None
    for rr in answer.get("Authority") or []:
        if rr.get("type") == 6:
            owner = _norm(rr.get("name", ""))
            if "." in owner and (name == owner or name.endswith("." + owner)):
                return owner
    return None


async def _zone_apex(client: httpx.AsyncClient, host: str, soa: dict | None) -> str | None:
    if soa is not None:
        apex = _apex_from(host, soa)
        if apex:
            return apex
    for parent in _parents(host)[:3]:
        try:
            ans = await _doh(client, parent, "SOA")
        except LookupFailed:
            return None
        apex = _apex_from(parent, ans)
        if apex:
            return apex
    return None


# ─────────────────────────── email security ───────────────────────────

def _tags(record: str) -> dict[str, str]:
    out = {}
    for part in record.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep:
            out[k.strip().lower()] = v.strip()
    return out


def email_security(
    mx: list[DnsRecord], txt: list[DnsRecord], dmarc_txt: list[DnsRecord],
    sts_txt: list[DnsRecord], dmarc_from: str | None = None,
) -> DomainEmail:
    def pref(r: DnsRecord) -> int:
        try:
            return int(r.value.split()[0])
        except (ValueError, IndexError):
            return 65535

    mx_sorted = sorted((r for r in mx if r.type == "MX"), key=pref)
    null_mx = len(mx_sorted) == 1 and mx_sorted[0].value.split()[-1] == "."
    spfs = [r.value for r in txt if r.type == "TXT" and r.value.lower().startswith("v=spf1")]
    spf = spfs[0] if spfs else None
    spf_all = None
    if spf:
        m = re.search(r"(?:^|\s)([-~?+]?)all(?:\s|$)", spf, re.I)
        if m:
            spf_all = (m.group(1) or "+") + "all"
    dmarcs = [r.value for r in dmarc_txt if r.type == "TXT" and r.value.lower().startswith("v=dmarc1")]
    dmarc = dmarcs[0] if len(dmarcs) == 1 else None
    tags = _tags(dmarc) if dmarc else {}
    policy = tags.get("p", "").lower() or None
    pct = None
    if "pct" in tags:
        try:
            pct = int(tags["pct"])
        except ValueError:
            pct = None
    reports = [a.strip() for a in tags.get("rua", "").split(",") if a.strip()]
    sts = any(r.type == "TXT" and r.value.lower().startswith("v=stsv1") for r in sts_txt)

    notes: list[str] = []
    if null_mx:
        notes.append("Null MX: the domain says it accepts no mail.")
    elif not mx_sorted:
        notes.append("No MX records: mail would go to the A record, if anywhere.")
    if len(spfs) > 1:
        notes.append("More than one SPF record: receivers treat that as an SPF error (permerror).")
    if not spf:
        notes.append("No SPF record: nothing says which servers may send its mail.")
    elif spf_all == "+all":
        notes.append("SPF ends in +all: any server in the world passes.")
    elif spf_all in ("?all", None):
        notes.append("SPF doesn't end in -all or ~all, so it rejects nothing.")
    if len(dmarcs) > 1:
        notes.append("More than one DMARC record: receivers ignore DMARC entirely.")
    elif not dmarc:
        notes.append("No DMARC record: spoofed mail is neither blocked nor reported.")
    elif policy == "none":
        notes.append("DMARC policy is none: spoofed mail is reported, not blocked.")
    elif pct is not None and pct < 100:
        notes.append(f"DMARC enforced on {pct}% of failing mail only.")

    enforced = policy in ("quarantine", "reject") and (pct is None or pct >= 100)
    if enforced and spf and spf_all in ("-all", "~all") and len(spfs) == 1:
        protection = "strong"
    elif enforced or (dmarc and spf) or (spf and spf_all in ("-all", "~all")):
        protection = "partial"
    elif spf or dmarc:
        protection = "weak"
    else:
        protection = "none"
    # A domain that sends no mail and says so can't be spoofed into inboxes
    # that check, whatever else is missing.
    if null_mx and spf_all == "-all" and enforced:
        protection = "strong"

    return DomainEmail(
        mx=[r.value for r in mx_sorted],
        null_mx=null_mx,
        spf=spf,
        spf_count=len(spfs),
        spf_all=spf_all,
        dmarc=dmarc,
        dmarc_policy=policy,
        dmarc_subdomain_policy=tags.get("sp", "").lower() or None,
        dmarc_pct=pct,
        dmarc_reports=reports,
        dmarc_inherited_from=dmarc_from if dmarc else None,
        mta_sts=sts,
        protection=protection,
        notes=notes,
    )


# ─────────────────────────── RDAP ───────────────────────────

def _redacted(text: str | None) -> bool:
    return bool(text) and bool(re.search(r"redact|privacy|withheld|not disclosed|data protected", text, re.I))


def parse_rdap(data: dict, registry_host: str = "") -> DomainRegistration:
    ents = list(iplookup._entities(data.get("entities")))

    def roles(e: dict) -> list[str]:
        return [str(r).lower() for r in e.get("roles") or []]

    registrar = next((e for e in ents if "registrar" in roles(e)), None)
    registrar_name = iplookup._vcard(registrar).get("fn") if registrar else None
    iana = None
    if registrar:
        for pid in registrar.get("publicIds") or []:
            if isinstance(pid, dict) and "iana" in str(pid.get("type", "")).lower():
                iana = str(pid.get("identifier"))
                break
    abuse = None
    for e in iplookup._entities((registrar or {}).get("entities")):
        if "abuse" in roles(e):
            emails = iplookup._vcard(e)["emails"]
            if emails:
                abuse = emails[0][0]
                break

    registrant = next((e for e in ents if "registrant" in roles(e)), None)
    reg_name = None
    redacted = False
    if registrant:
        card = iplookup._vcard(registrant)
        reg_name = card.get("fn")
        # Some registries only fill in the organisation, as `org`.
        try:
            for prop in registrant["vcardArray"][1]:
                if isinstance(prop, list) and prop and prop[0] == "org" and prop[-1]:
                    org = prop[-1] if isinstance(prop[-1], str) else " ".join(map(str, prop[-1]))
                    if org.strip():
                        reg_name = org.strip() if not reg_name or _redacted(reg_name) else reg_name
                    break
        except (KeyError, IndexError, TypeError):
            pass
        if _redacted(reg_name) or any("redacted" in str(r).lower() for r in registrant.get("remarks") or []):
            redacted, reg_name = True, None
    elif any("redact" in str(n).lower() for n in data.get("notices") or []) or data.get("redacted"):
        redacted = True

    events: dict[str, str] = {}
    for ev in data.get("events") or []:
        if isinstance(ev, dict) and ev.get("eventAction") and ev.get("eventDate"):
            events.setdefault(str(ev["eventAction"]).lower(), str(ev["eventDate"]))

    ns = sorted({_norm(n.get("ldhName", "")) for n in data.get("nameservers") or []
                 if isinstance(n, dict) and n.get("ldhName")})
    secure = data.get("secureDNS") or {}
    dnssec = secure.get("delegationSigned") if isinstance(secure, dict) else None

    return DomainRegistration(
        domain=_norm(data.get("ldhName") or data.get("unicodeName") or ""),
        registry=registry_host or None,
        registrar=registrar_name,
        registrar_iana_id=iana,
        registrar_abuse_email=abuse,
        registrant=reg_name,
        registrant_redacted=redacted,
        created=events.get("registration"),
        updated=events.get("last changed"),
        expires=events.get("expiration"),
        status=[str(s) for s in data.get("status") or []],
        nameservers=ns,
        dnssec=dnssec if isinstance(dnssec, bool) else None,
    )


class _NoRecord(LookupFailed):
    """The registry has no record for this name: an answer, not a failure."""


@sourcehealth.tracked(
    "domain", "rdap", "RDAP (rdap.org)", safe=(LookupFailed,),
    answered=lambda e: isinstance(e, _NoRecord),
)
async def _rdap_fetch(client: httpx.AsyncClient, domain: str) -> DomainRegistration:
    try:
        r = await client.get(
            settings.domain_rdap_url + domain,
            headers={**_UA, "Accept": "application/rdap+json, application/json"},
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        raise LookupFailed(f"RDAP: {type(e).__name__}") from e
    if r.status_code == 404:
        raise _NoRecord("RDAP: no registration record")
    if r.status_code == 429:
        raise LookupFailed("RDAP: the registry is rate-limiting us, try again shortly")
    if r.status_code >= 400:
        raise LookupFailed(f"RDAP: HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as e:
        raise LookupFailed("RDAP: unexpected response") from e
    if not isinstance(data, dict) or data.get("objectClassName") not in (None, "domain"):
        raise LookupFailed("RDAP: unexpected response")
    return parse_rdap(data, r.url.host or "")


async def _registration(client: httpx.AsyncClient, candidates: list[str]) -> DomainRegistration | None:
    """The first candidate the registry knows, or None if none of them is
    registered. A delegated subzone (dept.example.com) has no record of its
    own, so its parent is asked next."""
    for name in candidates:
        hit = _rdap_cache.get(name)
        if hit is False:  # known not to be registered
            continue
        if hit is not None:
            return hit
        try:
            reg = await _rdap_fetch(client, name)
        except _NoRecord:
            _rdap_cache.put(name, False)
            continue
        _rdap_cache.put(name, reg)
        return reg
    return None


# ─────────────────────────── certificate transparency ───────────────────────────

def _names_under(names: list[str], apex: str) -> set[str]:
    out = set()
    for n in names:
        n = str(n).strip().lower().rstrip(".")
        if n.startswith("*."):
            n = n[2:]
        if n == apex or n.endswith("." + apex):
            out.add(n)
    return out


def _certs(source: str, certificates: int, names: set[str], apex: str, partial: bool) -> DomainCertificates:
    subs = sorted(n for n in names if n != apex)
    cap = max(0, settings.domain_subdomain_max)
    return DomainCertificates(
        source=source, certificates=certificates, subdomains=subs[:cap],
        total_names=len(subs), partial=partial,
    )


@sourcehealth.tracked("domain", "crtsh", "crt.sh certificate search", safe=(LookupFailed,))
async def _crtsh(client: httpx.AsyncClient, apex: str) -> DomainCertificates:
    body = bytearray()
    try:
        async with asyncio.timeout(settings.domain_ct_timeout):
            async with client.stream(
                "GET", "https://crt.sh/", params={"q": f"%.{apex}", "output": "json"},
                headers=_UA, timeout=settings.domain_ct_timeout,
            ) as r:
                if r.status_code >= 400:
                    raise LookupFailed(f"crt.sh: HTTP {r.status_code}")
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_CT_BYTES:
                        raise LookupFailed("crt.sh: too many certificates to read")
    except TimeoutError as e:
        raise LookupFailed("crt.sh: timed out") from e
    except httpx.HTTPError as e:
        raise LookupFailed(f"crt.sh: {type(e).__name__}") from e
    try:
        rows = json.loads(bytes(body) or b"[]")
    except ValueError as e:
        raise LookupFailed("crt.sh: unexpected response") from e
    if not isinstance(rows, list):
        raise LookupFailed("crt.sh: unexpected response")
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            names += str(row.get("name_value", "")).splitlines()
            names.append(str(row.get("common_name", "")))
    return _certs("crt.sh", len(rows), _names_under(names, apex), apex, partial=False)


@sourcehealth.tracked("domain", "certspotter", "Cert Spotter", safe=(LookupFailed,))
async def _certspotter(client: httpx.AsyncClient, apex: str) -> DomainCertificates:
    try:
        r = await client.get(
            "https://api.certspotter.com/v1/issuances",
            params={"domain": apex, "include_subdomains": "true", "expand": "dns_names"},
            headers=_UA, timeout=settings.domain_ct_timeout,
        )
    except httpx.HTTPError as e:
        raise LookupFailed(f"Cert Spotter: {type(e).__name__}") from e
    if r.status_code == 429:
        raise LookupFailed("Cert Spotter: rate-limited, try again later")
    if r.status_code >= 400:
        raise LookupFailed(f"Cert Spotter: HTTP {r.status_code}")
    try:
        rows = r.json()
    except ValueError as e:
        raise LookupFailed("Cert Spotter: unexpected response") from e
    if not isinstance(rows, list):
        raise LookupFailed("Cert Spotter: unexpected response")
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            names += [str(n) for n in row.get("dns_names") or []]
    partial = 'rel="next"' in r.headers.get("link", "")
    return _certs("Cert Spotter", len(rows), _names_under(names, apex), apex, partial=partial)


async def _certificates(client: httpx.AsyncClient, apex: str) -> DomainCertificates:
    hit = _ct_cache.get(apex)
    if hit is not None:
        return hit
    try:
        # crt.sh goes down for hours at a time. Once it has failed a few
        # times running, give it a rest and go straight to Cert Spotter.
        if sourcehealth.resting("domain", "crtsh", _CRTSH_REST):
            raise LookupFailed("crt.sh: down, resting")
        res = await _crtsh(client, apex)
    except LookupFailed as first:
        try:
            res = await _certspotter(client, apex)
        except LookupFailed as second:
            raise LookupFailed(f"{first}; {second}") from second
    _ct_cache.put(apex, res)
    return res


# ─────────────────────────── the website ───────────────────────────

def _public(ips: list[str]) -> list[str]:
    return [ip for ip in ips if iplookup.scope_of(ipaddress.ip_address(ip)) == "public"]


async def _resolve(client: httpx.AsyncClient, host: str) -> list[str]:
    """A and AAAA addresses for a redirect target, over the same resolvers."""
    out: list[str] = []
    for rtype in ("A", "AAAA"):
        try:
            ans = await _doh(client, host, rtype)
        except LookupFailed:
            continue
        out += [r.value for r in _records(ans) if r.type == rtype]
    return out


def _pinned_url(url: str, ip: str) -> str:
    parts = urlsplit(url)
    netloc = f"[{ip}]" if ":" in ip else ip
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


async def _get_pinned(client: httpx.AsyncClient, url: str, ip: str) -> tuple[httpx.Response, bytes]:
    """GET `url` from `ip` and nowhere else, with the URL's host as both the
    Host header and the TLS server name. At most _MAX_BODY of the body."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    host_header = host if not parts.port else f"{host}:{parts.port}"
    req = client.build_request(
        "GET", _pinned_url(url, ip),
        headers={**_SITE_UA, "Host": host_header, "Accept": "text/html,*/*;q=0.8"},
        extensions={"sni_hostname": host},
    )
    body = bytearray()
    try:
        async with asyncio.timeout(settings.domain_timeout * 1.5):
            resp = await client.send(req, stream=True)
            try:
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) >= _MAX_BODY:
                        break
            finally:
                await resp.aclose()
    except TimeoutError:
        raise httpx.ReadTimeout("the site was too slow to answer") from None
    return resp, bytes(body[:_MAX_BODY])


def _page_meta(body: bytes, content_type: str) -> tuple[str | None, str | None]:
    if "html" not in content_type.lower() and not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return None, None
    text = body.decode("utf-8", "replace")
    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:200] or None
    gen = None
    for tag in re.findall(r"<meta\b[^>]*>", text, re.I):
        if re.search(r"""name\s*=\s*["']?generator\b""", tag, re.I):
            c = re.search(r"""content\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.I)
            if c:
                gen = html.unescape(c.group(1) or c.group(2) or "").strip()[:120] or None
            break
    return title, gen


def _cert_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def _dn(seq: Any, *keys: str) -> str | None:
    found: dict[str, str] = {}
    for rdn in seq or ():
        for k, v in rdn:
            found.setdefault(k, v)
    parts = [found[k] for k in keys if k in found]
    return " · ".join(dict.fromkeys(parts)) or None


async def _tls_handshake(host: str, ip: str, ctx: ssl.SSLContext) -> tuple[dict, str | None]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, 443, ssl=ctx, server_hostname=host),
        timeout=settings.domain_timeout,
    )
    try:
        obj = writer.get_extra_info("ssl_object")
        return (obj.getpeercert() if obj else {}) or {}, (obj.version() if obj else None)
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except (OSError, asyncio.TimeoutError, ssl.SSLError):
            pass


async def _tls(host: str, ip: str) -> DomainTls:
    """The certificate the site presents on 443, verified as a browser would.
    When verification fails, why; and when only the name is wrong, the
    certificate itself (read with the name check off)."""
    error = None
    try:
        cert, version = await _tls_handshake(host, ip, ssl.create_default_context())
    except ssl.SSLCertVerificationError as e:
        error = e.verify_message or str(e)
        loose = ssl.create_default_context()
        loose.check_hostname = False
        try:
            cert, version = await _tls_handshake(host, ip, loose)
        except (ssl.SSLError, OSError, asyncio.TimeoutError):
            return DomainTls(valid=False, error=error)
    except (ssl.SSLError, OSError, asyncio.TimeoutError) as e:
        raise LookupFailed(f"TLS: no handshake on port 443 ({type(e).__name__})") from e

    not_after = _cert_time(cert.get("notAfter"))
    days_left = None
    if not_after:
        days_left = int((datetime.fromisoformat(not_after) - datetime.now(timezone.utc)).total_seconds() // 86400)
    names = [v for k, v in cert.get("subjectAltName") or () if k == "DNS"]
    return DomainTls(
        valid=error is None,
        error=error,
        version=version,
        subject=_dn(cert.get("subject"), "commonName", "organizationName"),
        issuer=_dn(cert.get("issuer"), "organizationName", "commonName"),
        not_before=_cert_time(cert.get("notBefore")),
        not_after=not_after,
        days_left=days_left,
        names=names[:50],
    )


async def _website(client: httpx.AsyncClient, host: str, addresses: list[str]) -> DomainWebsite:
    public = _public(addresses)
    if not addresses:
        return DomainWebsite(url=f"https://{host}/", note="no A or AAAA records: there is nothing to fetch")
    if not public:
        return DomainWebsite(
            url=f"https://{host}/",
            note="resolves only to private or reserved addresses, so it was not fetched",
        )
    ip = public[0]
    site = DomainWebsite(url=f"https://{host}/", ip=ip)

    tls_task = asyncio.create_task(_tls(host, ip))
    try:
        return await _fetch_site(client, host, ip, site, tls_task)
    finally:
        if not tls_task.done():
            tls_task.cancel()


async def _fetch_site(
    client: httpx.AsyncClient, host: str, ip: str, site: DomainWebsite, tls_task: asyncio.Task,
) -> DomainWebsite:
    url, cur_ip = site.url, ip
    t = sourcehealth.Timer()
    resp = body = None
    try:
        resp, body = await _get_pinned(client, url, cur_ip)
    except httpx.HTTPError:
        # No HTTPS: try plain HTTP before giving up.
        url = site.url = f"http://{host}/"
        try:
            resp, body = await _get_pinned(client, url, cur_ip)
        except httpx.HTTPError as e:
            raise LookupFailed(f"website: no answer over HTTPS or HTTP ({type(e).__name__})") from e
    site.response_ms = round(t.ms)

    hops = 0
    while resp.is_redirect and hops < _MAX_HOPS:
        loc = resp.headers.get("location", "")
        site.redirects.append(WebHop(url=url, status=resp.status_code, location=loc or None))
        nxt = urljoin(url, loc)
        parts = urlsplit(nxt)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if parts.scheme not in ("http", "https") or not parts.hostname or port not in _PORTS:
            site.note = f"stopped at a redirect to {parts.scheme or '?'}://{parts.hostname or '?'}:{port}"
            break
        nhost = parts.hostname.lower()
        if nhost != urlsplit(url).hostname:
            if iplookup._as_ip(nhost) is not None:
                ips = [str(iplookup._as_ip(nhost))]
            else:
                ips = await _resolve(client, nhost)
            pub = _public(ips)
            if not pub:
                site.note = f"redirects to {nhost}, which has no public address; not followed"
                break
            cur_ip = pub[0]
        url = nxt
        hops += 1
        try:
            resp, body = await _get_pinned(client, url, cur_ip)
        except httpx.HTTPError as e:
            site.note = f"the redirect to {nhost} did not answer ({type(e).__name__})"
            resp = None
            break
    else:
        if resp is not None and resp.is_redirect:
            site.note = f"more than {_MAX_HOPS} redirects; stopped"

    if resp is not None:
        site.final_url = url
        site.status = resp.status_code
        site.server = resp.headers.get("server")
        site.powered_by = resp.headers.get("x-powered-by")
        site.security_headers = {h: resp.headers.get(h) for h in SECURITY_HEADERS}
        site.title, site.generator = _page_meta(body or b"", resp.headers.get("content-type", ""))

    # Does plain HTTP send visitors on to HTTPS?
    if site.url.startswith("https://"):
        try:
            plain, _ = await _get_pinned(client, f"http://{host}/", ip)
            loc = plain.headers.get("location", "") if plain.is_redirect else ""
            site.https_redirect = urljoin(f"http://{host}/", loc).startswith("https://") if loc else False
        except httpx.HTTPError:
            site.https_redirect = None  # port 80 closed: nothing to redirect

    try:
        site.tls = await tls_task
    except LookupFailed:
        site.tls = None
    return site


# ─────────────────────────── archive ───────────────────────────

def _wayback_ts(ts: str) -> str | None:
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


@sourcehealth.tracked("domain", "wayback", "Wayback Machine (archive.org)", safe=(LookupFailed,))
async def _wayback_first(client: httpx.AsyncClient, host: str) -> tuple[str, str] | None:
    """(timestamp, url) of the first capture, or None if there is none."""
    try:
        r = await client.get(
            "https://web.archive.org/cdx/search/cdx",
            params={"url": host, "output": "json", "fl": "timestamp,original", "limit": "1"},
            # The archive is the slowest source by far and the least needed,
            # so it gets the short timeout rather than holding the lookup.
            headers=_UA, timeout=settings.domain_timeout,
        )
    except httpx.HTTPError as e:
        raise LookupFailed(f"Wayback Machine: {type(e).__name__}") from e
    if r.status_code == 429:
        raise LookupFailed("Wayback Machine: rate-limited, try again later")
    if r.status_code >= 400:
        raise LookupFailed(f"Wayback Machine: HTTP {r.status_code}")
    text = r.text.strip()
    if not text:
        return None
    try:
        rows = r.json()
    except ValueError as e:
        raise LookupFailed("Wayback Machine: unexpected response") from e
    rows = [row for row in rows if isinstance(row, list) and len(row) >= 2][1:]  # the first row is the header
    return (str(rows[0][0]), str(rows[0][1])) if rows else None


async def _archive(client: httpx.AsyncClient, host: str) -> DomainArchive:
    hit = _archive_cache.get(host)
    if hit is not None:
        return hit
    # When it has stopped answering, don't make every lookup wait out its timeout.
    if sourcehealth.resting("domain", "wayback", _WAYBACK_REST):
        raise LookupFailed("Wayback Machine: not answering lately, skipped for now")
    first = await _wayback_first(client, host)
    out = DomainArchive()
    if first:
        out.first, out.first_url = _wayback_ts(first[0]), f"https://web.archive.org/web/{first[0]}/{first[1]}"
    _archive_cache.put(host, out)
    return out


# ─────────────────────────── hosting ───────────────────────────

def _place(ip: str) -> DomainAddress:
    addr = ipaddress.ip_address(ip)
    out = DomainAddress(ip=ip, version=addr.version, scope=iplookup.scope_of(addr))  # type: ignore[arg-type]
    if out.scope != "public":
        return out
    try:
        loc = iplookup._location(ip)
        if loc:
            out.country, out.country_code, out.city = loc.country, loc.country_code, loc.city
    except Exception:  # no database, or a corrupt one: the rest still stands
        pass
    try:
        net = iplookup._network(ip)
        if net:
            out.asn, out.as_org = net.asn, net.as_org
    except Exception:
        pass
    return out


# ─────────────────────────── the lookup ───────────────────────────

def attribution() -> list[str]:
    out = ["DNS answered over DNS-over-HTTPS by Google Public DNS or Cloudflare"]
    out.append("Registration data from the domain registries via RDAP (rdap.org)")
    out.append("Certificates from the certificate-transparency logs via crt.sh or Cert Spotter (sslmate.com)")
    if settings.domain_wayback:
        out.append("Captures from the Internet Archive's Wayback Machine (archive.org)")
    kinds = {iplookup._CITY.kind(), iplookup._ASN.kind()}
    if "dbip" in kinds:
        out.append("IP geolocation by DB-IP (db-ip.com), CC BY 4.0")
    if "geolite2" in kinds:
        out.append("GeoLite2 data created by MaxMind (maxmind.com)")
    return out


_DNS_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "CAA", "SOA")


async def _deadline(coro: Any, seconds: float, label: str) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise LookupFailed(f"{label}: timed out") from None


async def lookup(host: str) -> DomainLookupResponse:
    """Everything about `host`. A source that fails is reported in `errors`
    and `sources`, never passed off as "nothing found"."""
    res = DomainLookupResponse(query=host, domain=host, sources=[], attribution=attribution())
    timeout = httpx.Timeout(settings.domain_timeout)
    async with httpx.AsyncClient(timeout=timeout) as client, \
            httpx.AsyncClient(timeout=timeout, verify=False) as site_client:
        # 1. DNS: every record type, plus the mail-policy names, at once.
        names = [(host, t) for t in _DNS_TYPES] + [(f"_dmarc.{host}", "TXT"), (f"_mta-sts.{host}", "TXT")]
        answers = await asyncio.gather(*(_doh(client, n, t) for n, t in names), return_exceptions=True)
        by: dict[tuple[str, str], dict] = {}
        dns_errors = []
        for key, ans in zip(names, answers):
            if isinstance(ans, dict):
                by[key] = ans
            else:
                dns_errors.append(str(ans) if isinstance(ans, LookupFailed) else "DNS failed")

        seen: set[tuple[str, str, str]] = set()
        for t in _DNS_TYPES:
            for rec in _records(by.get((host, t), {})):
                sig = (rec.type, rec.name, rec.value)
                if sig not in seen:
                    seen.add(sig)
                    res.dns.append(rec)
        a_ans = by.get((host, "A"))
        if a_ans is not None:
            status = a_ans.get("Status")
            res.exists = True if status == 0 else False if status == 3 else None
            res.dnssec_validated = bool(a_ans.get("AD"))
        if dns_errors and len(dns_errors) == len(names):
            res.errors["dns"] = dns_errors[0]

        # 2. Where the zone starts: the registered domain, near enough.
        apex = await _zone_apex(client, host, by.get((host, "SOA")))
        res.registered_domain = apex

        # Records that belong to the name itself, not to a CNAME target.
        own = [r for r in res.dns if r.name == host]
        addresses = list(dict.fromkeys(r.value for r in res.dns if r.type in ("A", "AAAA")))

        # 3. Mail policy. DMARC falls back to the registered domain, as
        # receivers do.
        dmarc = _records(by.get((f"_dmarc.{host}", "TXT"), {}))
        dmarc_from = None
        if not any(r.value.lower().startswith("v=dmarc1") for r in dmarc) and apex and apex != host:
            try:
                dmarc = _records(await _doh(client, f"_dmarc.{apex}", "TXT"))
                dmarc_from = apex
            except LookupFailed:
                dmarc = []
        if res.exists and ((host, "MX") in by or (host, "TXT") in by):
            res.email = email_security(
                [r for r in own if r.type == "MX"], [r for r in own if r.type == "TXT"],
                dmarc, _records(by.get((f"_mta-sts.{host}", "TXT"), {})), dmarc_from,
            )

        # 4. Everything else at once.
        rdap_names = list(dict.fromkeys(
            ([apex] + _parents(apex)[:1]) if apex else ([host] + _parents(host))[:3]
        ))
        # Hard ceilings: no one source may hold the lookup past its own budget.
        site_deadline = settings.domain_timeout * 4
        slow_deadline = settings.domain_ct_timeout * 2 + 5
        jobs: dict[str, Any] = {
            "rdap": _deadline(_registration(client, rdap_names), slow_deadline, "RDAP"),
            "certificates": _deadline(_certificates(client, apex or host), slow_deadline, "certificate logs"),
        }
        if settings.domain_fetch_site:
            jobs["website"] = _deadline(_website(site_client, host, addresses), site_deadline, "website")
        if settings.domain_wayback:
            jobs["archive"] = _deadline(_archive(client, host), slow_deadline, "Wayback Machine")
        done = await asyncio.gather(*jobs.values(), return_exceptions=True)
        out = dict(zip(jobs, done))

    for key, value in out.items():
        if isinstance(value, BaseException):
            res.errors[key] = str(value) if isinstance(value, LookupFailed) else f"{key} failed"
            if not isinstance(value, LookupFailed):
                log.warning("domain lookup %s crashed: %s", key, type(value).__name__)
            continue
        if key == "rdap":
            res.registration = value
        else:
            setattr(res, key, value)

    res.addresses = [_place(ip) for ip in addresses[:_ADDRESSES]]
    res.sources = _sources(res)
    return res


def _sources(res: DomainLookupResponse) -> list[IpSource]:
    def src(key: str, label: str, ok_status: str) -> IpSource:
        if key in res.errors:
            return IpSource(key=key, label=label, ok=False, status=res.errors[key])
        return IpSource(key=key, label=label, ok=True, status=ok_status)

    out = [
        src("dns", "DNS", f"{len(res.dns)} record(s)" if res.exists is not False else "the name does not exist"),
        src("rdap", "RDAP registry",
            f"registered as {res.registration.domain}" if res.registration else "no registration record"),
        src("certificates", "certificate logs",
            f"{res.certificates.total_names} name(s) via {res.certificates.source}" if res.certificates else "none"),
    ]
    if settings.domain_fetch_site:
        w = res.website
        out.append(src("website", "website",
                       f"HTTP {w.status}" if w and w.status else (w.note if w and w.note else "not fetched")))
    if settings.domain_wayback:
        out.append(src("archive", "Wayback Machine",
                       "captured" if res.archive and res.archive.first else "no captures"))
    return out


def answered(res: DomainLookupResponse) -> bool:
    """Did anything come back? A name that doesn't exist, isn't registered,
    has no certificates and was never archived teaches nothing, and every
    source failing teaches less; neither is charged as a search."""
    return bool(
        res.exists or res.registration
        or (res.certificates and res.certificates.total_names)
        or (res.archive and res.archive.first)
    )

"""Pivot modules: given one node, each finds what links out from it.

A module declares the node types it `consumes` and implements
`expand(client, node) -> list[Finding]`. The contract is the same one the leak
providers follow — **a module never raises**: a dead source, a rate-limit or a
parse error returns `[]` (and may leave a short note on `node.detail`), so one
flaky source can't sink a whole scan.

Every outward request carries only the single identifier being expanded — a
username to the sites it is checked on, an email's MD5 to Gravatar, a domain to
the DNS/certificate/RDAP services. A person's `name` is never sent anywhere: it
is expanded only by the `leaks` module, and only against uploaded datasets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ...config import settings
from .graph import Node

log = logging.getLogger("decint.spider")

_UA = {"User-Agent": "decint-tools/1.0 (osint research)"}


@dataclass
class Finding:
    """One thing a module discovered, hanging off the node it expanded."""
    type: str                 # target node type
    value: str                # raw value (graph canonicalises it)
    label: str = ""           # relationship label for the edge, e.g. "profile"
    detail: str | None = None
    url: str | None = None
    display: str = ""         # display label for the node, when it differs from value


class Module:
    key: str = "base"
    name: str = "Base"
    consumes: tuple[str, ...] = ()
    # Modules that fan out to many hosts (username_sites) are heavier; the
    # engine uses this to order light modules first within a lookup budget.
    heavy: bool = False

    def enabled(self) -> bool:
        return self.key in settings.spider_module_list

    async def expand(self, client: httpx.AsyncClient, node: Node) -> list[Finding]:  # pragma: no cover - interface
        raise NotImplementedError


# ─────────────────────────── leaks (reuses the leak aggregator) ───────────────────────────

class LeaksModule(Module):
    """Expands an identifier through the existing leak search — every breach,
    and every other identifier co-located with it in a hit."""

    key = "leaks"
    name = "Leak sources"
    consumes = ("email", "username", "domain", "name")

    async def expand(self, client, node) -> list[Finding]:
        # Imported here, not at module load, to avoid a circular import at
        # startup (leaks.service imports nothing from spider, but keep it lazy).
        from ..leaks import search_leaks

        try:
            resp = await search_leaks(node.value, kind=node.type, reveal=True)
        except Exception as e:  # noqa: BLE001 — a provider fault is not the scan's fault
            log.debug("leaks module failed for %s: %s", node.type, type(e).__name__)
            return []

        out: list[Finding] = []
        for h in resp.hits:
            where = h.breach or h.source_label or "leak"
            if h.breach:
                out.append(Finding("breach", h.breach, "exposed in",
                                   detail=h.date, url=h.url, display=h.breach))
            # Each co-located identifier becomes its own node, linked to this one.
            if h.email and h.email != node.value:
                out.append(Finding("email", h.email, f"with · {where}"))
            if h.username and h.username != node.value:
                out.append(Finding("username", h.username, f"with · {where}"))
            # The leak schema has no domain field, but an email carries one.
            email_domain = h.email.rsplit("@", 1)[-1] if h.email and "@" in h.email else None
            if email_domain and email_domain != node.value:
                out.append(Finding("domain", email_domain, f"with · {where}"))
            full = " ".join(p for p in (h.first_name, h.last_name) if p).strip()
            if full:
                out.append(Finding("name", full, f"named in · {where}", display=full))
            if h.password:
                out.append(Finding("password", h.password, f"password · {where}"))
        return out


# ─────────────────────────── gravatar (email → profile) ───────────────────────────

class GravatarModule(Module):
    """An email's public Gravatar profile: display name, preferred username and
    any accounts the owner linked to it."""

    key = "gravatar"
    name = "Gravatar profile"
    consumes = ("email",)

    async def expand(self, client, node) -> list[Finding]:
        digest = hashlib.md5(node.value.strip().lower().encode()).hexdigest()
        try:
            r = await client.get(f"https://gravatar.com/{digest}.json", headers=_UA)
            if r.status_code == 404:
                return []
            r.raise_for_status()
            entries = (r.json() or {}).get("entry") or []
        except (httpx.HTTPError, ValueError) as e:
            log.debug("gravatar failed: %s", type(e).__name__)
            return []
        if not entries:
            return []
        e = entries[0]
        out: list[Finding] = []
        display = (e.get("displayName") or "").strip()
        name = e.get("name") or {}
        full = (name.get("formatted") or
                " ".join(p for p in (name.get("givenName"), name.get("familyName")) if p)).strip()
        if full:
            out.append(Finding("name", full, "gravatar name", display=full))
        pref = (e.get("preferredUsername") or "").strip()
        if pref:
            out.append(Finding("username", pref, "gravatar username"))
        for acct in e.get("accounts") or []:
            uname = (acct.get("username") or acct.get("display") or "").strip()
            svc = acct.get("shortname") or acct.get("name") or "account"
            url = acct.get("url")
            if uname:
                out.append(Finding("username", uname, f"gravatar · {svc}"))
            if url:
                out.append(Finding("account", url, f"linked · {svc}", url=url,
                                   display=f"{svc}: {uname or url}"))
        if display and not full:
            node.detail = node.detail or f"gravatar: {display}"
        return out


# ─────────────────────────── username presence across sites ───────────────────────────

def _load_sites() -> list[dict]:
    path = Path(settings.spider_username_sites_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("sites", data) if isinstance(data, dict) else data
    except (OSError, ValueError) as e:
        log.warning("spider username-site list unreadable (%s): %s", path, e)
        return []


_SITES: list[dict] | None = None


def _sites() -> list[dict]:
    global _SITES
    if _SITES is None:
        _SITES = _load_sites()
    return _SITES


class UsernameSitesModule(Module):
    """Checks whether a username exists on a curated list of sites — the
    biggest 'these accounts are the same person' signal, and the noisiest in
    outbound requests, so it is count- and concurrency-capped."""

    key = "username_sites"
    name = "Username across sites"
    consumes = ("username",)
    heavy = True

    async def expand(self, client, node) -> list[Finding]:
        import asyncio

        uname = node.value
        if not re.fullmatch(r"[A-Za-z0-9._\-]{2,64}", uname):
            return []  # not a plausible handle; don't spray it at 100 sites
        sites = _sites()[: settings.spider_username_sites_max]
        sem = asyncio.Semaphore(max(1, settings.spider_username_sites_concurrency))

        async def check(site: dict) -> Finding | None:
            url = str(site.get("url", "")).replace("{}", uname)
            if not url:
                return None
            probe = str(site.get("probe", url)).replace("{}", uname)
            expect = int(site.get("code", 200))
            needle = site.get("contains")
            if needle:
                needle = needle.replace("{}", uname)
            async with sem:
                try:
                    r = await client.get(probe, headers=_UA,
                                         timeout=settings.spider_username_sites_timeout)
                except httpx.HTTPError:
                    return None
            if r.status_code != expect:
                return None
            if needle and needle not in r.text:
                return None
            svc = site.get("name", "site")
            return Finding("account", url, f"account · {svc}", url=url,
                           display=f"{svc}: {uname}")

        results = await asyncio.gather(*(check(s) for s in sites), return_exceptions=True)
        return [f for f in results if isinstance(f, Finding)]


# ─────────────────────────── domain intelligence ───────────────────────────

class DomainModule(Module):
    """DNS (MX/NS over DNS-over-HTTPS), certificate-transparency subdomains
    (crt.sh), and registration (RDAP). Mail/name-server hosts and subdomains
    become their own domain nodes; registrar detail annotates the domain."""

    key = "domain"
    name = "Domain intel"
    consumes = ("domain",)

    async def expand(self, client, node) -> list[Finding]:
        out: list[Finding] = []
        out += await self._dns(client, node)
        out += await self._crtsh(client, node)
        await self._rdap(client, node)
        return out

    async def _dns(self, client, node) -> list[Finding]:
        found: list[Finding] = []
        for rtype in ("MX", "NS"):
            try:
                r = await client.get("https://dns.google/resolve",
                                     params={"name": node.value, "type": rtype}, headers=_UA)
                r.raise_for_status()
                answers = (r.json() or {}).get("Answer") or []
            except (httpx.HTTPError, ValueError):
                continue
            for a in answers:
                data = str(a.get("data", "")).strip().rstrip(".")
                host = data.split()[-1] if rtype == "MX" else data  # MX = "10 mail.host"
                host = host.rstrip(".")
                if host and host != node.value:
                    found.append(Finding("domain", host, f"{rtype.lower()} record"))
        return found

    async def _crtsh(self, client, node) -> list[Finding]:
        try:
            r = await client.get("https://crt.sh/", params={"q": f"%.{node.value}", "output": "json"},
                                 headers=_UA)
            r.raise_for_status()
            rows = r.json() or []
        except (httpx.HTTPError, ValueError):
            return []
        names: set[str] = set()
        for row in rows:
            for n in str(row.get("name_value", "")).splitlines():
                n = n.strip().lower().lstrip("*.")
                if n and n.endswith(node.value) and n != node.value:
                    names.add(n)
        cap = settings.spider_subdomain_max
        return [Finding("domain", n, "subdomain") for n in sorted(names)[:cap]]

    async def _rdap(self, client, node) -> None:
        try:
            r = await client.get(f"https://rdap.org/domain/{node.value}", headers=_UA)
            if r.status_code >= 400:
                return
            data = r.json() or {}
        except (httpx.HTTPError, ValueError):
            return
        registrar = None
        for ent in data.get("entities") or []:
            if "registrar" in (ent.get("roles") or []):
                registrar = _vcard_name(ent)
                break
        created = next((e.get("eventDate") for e in data.get("events") or []
                        if e.get("eventAction") == "registration"), None)
        bits = [b for b in (f"registrar: {registrar}" if registrar else None,
                            f"registered: {created[:10]}" if created else None) if b]
        if bits:
            node.detail = node.detail or " · ".join(bits)


def _vcard_name(entity: dict) -> str | None:
    for item in (entity.get("vcardArray") or [None, []])[1]:
        if isinstance(item, list) and item and item[0] == "fn":
            return str(item[-1])
    return None


# ─────────────────────────── dark-web mentions ───────────────────────────

class DarkwebMentionsModule(Module):
    """Runs an identifier through the fast (gateway) dark-web search and pulls
    out the indicators that co-occur with it — onion addresses, other emails,
    and crypto wallets."""

    key = "darkweb_mentions"
    name = "Dark-web mentions"
    consumes = ("email", "username")

    async def expand(self, client, node) -> list[Finding]:
        from .. import darkweb as dw

        response = None
        try:
            async with dw.search_slot():
                async for event in dw.search_events(
                    node.value, "gateway", pages=1, experimental=False,
                    limit=settings.spider_darkweb_limit,
                ):
                    if event["type"] == "results":
                        response = event["response"]
        except Exception as e:  # noqa: BLE001
            log.debug("darkweb mentions failed: %s", type(e).__name__)
            return []
        if response is None or getattr(response, "blocked_query", False):
            return []

        out: list[Finding] = []
        seen: set[tuple[str, str]] = set()
        for res in getattr(response, "results", [])[: settings.spider_darkweb_limit]:
            ent = getattr(res, "entities", None) or {}
            src = getattr(res, "title", "") or getattr(res, "url", "")
            for onion in ent.get("onion_v3", []):
                out.append(Finding("onion", onion, "mentioned near", url=f"http://{onion}"))
            for email in ent.get("emails", []):
                if email.lower() != node.value:
                    out.append(Finding("email", email, "co-mentioned"))
            for kind in ("btc", "eth", "xmr"):
                for w in ent.get(kind, []):
                    if (kind, w) not in seen:
                        seen.add((kind, w))
                        out.append(Finding("wallet", w, f"{kind} · near mention",
                                           display=f"{kind.upper()}: {w}"))
        return out


MODULES: dict[str, Module] = {
    m.key: m
    for m in (
        LeaksModule(),
        GravatarModule(),
        UsernameSitesModule(),
        DomainModule(),
        DarkwebMentionsModule(),
    )
}


def enabled_modules() -> list[Module]:
    """The configured modules, light ones first so a tight lookup budget spends
    itself on the cheap, high-signal sources before the heavy fan-out."""
    mods = [m for m in MODULES.values() if m.enabled()]
    return sorted(mods, key=lambda m: m.heavy)

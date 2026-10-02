"""Free public breach-source adapters.

None of these require an API key. They are, by nature, best-effort: endpoints
change, rate-limit, or go offline. Every adapter catches its own errors and
reports ok=False rather than raising.

Sources:
  * XposedOrNot   — email → breach names + analytics   (free, no key)
  * ProxyNova COMB — query → combolist lines            (free, no key)
  * LeakCheck public — email/username → source list     (free, no key)
  * HIBP catalog  — breach metadata / domain filter     (free, no key)
"""

from __future__ import annotations

import asyncio
import json

import httpx

from ...models import LeakHit
from . import local
from .base import LeakProvider, ProviderResult

_UA = {"User-Agent": "decint-tools/1.0 (osint research)"}


class XposedOrNotProvider(LeakProvider):
    key = "xposedornot"
    label = "XposedOrNot"
    supported_kinds = ("email",)

    async def search(self, client, query, kind) -> ProviderResult:
        if kind != "email":
            return self._skip()
        try:
            # breach-analytics gives rich per-breach detail; check-email is the
            # fallback if analytics is unavailable.
            r = await client.get(
                "https://api.xposedornot.com/v1/breach-analytics",
                params={"email": query},
                headers=_UA,
            )
            if r.status_code == 404:
                return self._ok([], "no breaches found")
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return self._fail(f"error: {type(e).__name__}")

        hits: list[LeakHit] = []
        details = (data.get("ExposedBreaches") or {}).get("breaches_details") or []
        for b in details:
            hits.append(
                LeakHit(
                    source=self.key,
                    source_label=self.label,
                    breach=b.get("breach"),
                    email=query,
                    date=str(b.get("xposed_date") or "") or None,
                    detail=(b.get("details") or None),
                    fields=[
                        f.strip()
                        for f in (b.get("xposed_data") or "").split(";")
                        if f.strip()
                    ],
                    url=b.get("references") or None,
                )
            )
        if not hits:
            # analytics returned but with no detailed breaches; try the simple list
            names = (data.get("breaches") or [[]])
            flat = names[0] if names and isinstance(names[0], list) else names
            for name in flat or []:
                hits.append(
                    LeakHit(source=self.key, source_label=self.label,
                            breach=str(name), email=query)
                )
        return self._ok(hits)


class ProxyNovaProvider(LeakProvider):
    key = "proxynova"
    label = "ProxyNova COMB"
    supported_kinds = ("email", "username", "domain")

    async def search(self, client, query, kind) -> ProviderResult:
        try:
            r = await client.get(
                "https://api.proxynova.com/comb",
                params={"query": query, "start": 0, "limit": 25},
                headers=_UA,
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return self._fail(f"error: {type(e).__name__}")

        lines = data.get("lines") or []
        hits: list[LeakHit] = []
        for line in lines:
            ident, secret = line, None
            for sep in (":", ";", "|"):
                if sep in line:
                    ident, _, secret = line.partition(sep)
                    break
            hit = LeakHit(
                source=self.key,
                source_label=self.label,
                breach="COMB (compilation)",
                line=line,
                password=secret,
            )
            if "@" in ident:
                hit.email = ident
            else:
                hit.username = ident
            hits.append(hit)
        total = data.get("count", len(lines))
        return self._ok(hits, f"{len(hits)} of {total} line(s)")


class LeakCheckProvider(LeakProvider):
    key = "leakcheck"
    label = "LeakCheck (public)"
    supported_kinds = ("email", "username")

    async def search(self, client, query, kind) -> ProviderResult:
        if kind == "domain":
            return self._skip()
        try:
            r = await client.get(
                "https://leakcheck.io/api/public",
                params={"check": query},
                headers=_UA,
            )
            if r.status_code == 429:
                return self._fail("rate limited")
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return self._fail(f"error: {type(e).__name__}")

        if not data.get("success"):
            return self._ok([], data.get("error") or "no results")

        fields = data.get("fields") or []
        sources = data.get("sources") or []
        hits: list[LeakHit] = []
        for src in sources:
            hit = LeakHit(
                source=self.key,
                source_label=self.label,
                breach=src.get("name"),
                date=src.get("date") or None,
                fields=fields,
            )
            if kind == "email":
                hit.email = query
            else:
                hit.username = query
            hits.append(hit)
        return self._ok(hits, f"found in {data.get('found', len(hits))} source(s)")


class HibpCatalogProvider(LeakProvider):
    """HIBP breach catalog — free, no key. Not an account lookup: it returns
    breach metadata. For a domain query we filter the catalog by that domain,
    which is a genuine signal (which breaches involved that domain)."""

    key = "hibp_catalog"
    label = "HIBP catalog"
    supported_kinds = ("domain",)

    async def search(self, client, query, kind) -> ProviderResult:
        if kind != "domain":
            return self._skip("catalog is queried for domains only")
        try:
            r = await client.get(
                "https://haveibeenpwned.com/api/v3/breaches",
                params={"domain": query},
                headers=_UA,
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return self._fail(f"error: {type(e).__name__}")

        hits: list[LeakHit] = []
        for b in data or []:
            hits.append(
                LeakHit(
                    source=self.key,
                    source_label=self.label,
                    breach=b.get("Name"),
                    date=b.get("BreachDate") or None,
                    detail=_strip_html(b.get("Description", "")) or None,
                    fields=b.get("DataClasses") or [],
                    url=f"https://haveibeenpwned.com/PwnedWebsites#{b.get('Name', '')}",
                )
            )
        return self._ok(hits, f"{len(hits)} breach(es) for domain")


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text).strip()


class LocalDatasetProvider(LeakProvider):
    """Datasets an admin uploaded (Admin ▸ Data ▸ Leak datasets). Local, so it
    needs no HTTP client and is not subject to anyone's rate limit.

    Not listed in LEAKS_PROVIDERS: the aggregator adds it by itself whenever at
    least one uploaded dataset is enabled, so uploading is the only switch."""

    key = "local"
    label = "Uploaded datasets"

    async def search(self, client, query, kind) -> ProviderResult:
        try:
            rows, total = await asyncio.to_thread(local.search, query, kind)
        except Exception as e:  # a corrupt or locked index must not sink the query
            return self._fail(f"error: {type(e).__name__}")
        hits: list[LeakHit] = []
        for r in rows:
            try:
                fields = json.loads(r["fields"]) if r["fields"] else []
            except ValueError:
                fields = []
            hits.append(
                LeakHit(
                    source=self.key,
                    source_label=self.label,
                    breach=r["dataset"],
                    email=r["email"],
                    username=r["username"],
                    password=r["secret"],
                    fields=[f for f in fields if not f.startswith("col:")],
                    detail=f"domain: {r['domain']}" if r["domain"] and not r["email"] else None,
                )
            )
        capped = "+" if total > local.COUNT_CAP else ""
        status = (f"{len(hits)} of {min(total, local.COUNT_CAP):,}{capped} record(s)"
                  if hits else "no results")
        return self._ok(hits, status)


REGISTRY: dict[str, LeakProvider] = {
    p.key: p
    for p in (
        XposedOrNotProvider(),
        ProxyNovaProvider(),
        LeakCheckProvider(),
        HibpCatalogProvider(),
        LocalDatasetProvider(),
    )
}

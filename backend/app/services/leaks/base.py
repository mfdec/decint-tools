"""Leak-provider interface + shared helpers.

Each provider is a thin adapter over one free public breach source. They all
return the same normalized shape so the aggregator can merge them, and they
never raise — a dead or rate-limited source degrades to ok=False with a status
string, so one flaky provider can't sink the whole query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from ...models import LeakHit

# ── query-kind detection ──

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I)


# Two or more words made only of letters, apostrophes, hyphens and dots —
# "jane doe", "mary-ann o'neil". A single word stays a username; pick the
# `name` kind to look one up as a first or last name.
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '.\-]+[^\W\d_]+)+$")


def detect_kind(query: str) -> str:
    q = query.strip()
    if _EMAIL_RE.match(q):
        return "email"
    if _DOMAIN_RE.match(q) and "@" not in q:
        return "domain"
    if " " in q and _NAME_RE.match(q):
        return "name"
    return "username"


def mask_secret(value: str | None) -> str | None:
    """Mask a password/secret: keep first + last char, dot out the middle."""
    if not value:
        return value
    if len(value) <= 2:
        return "•" * len(value)
    if len(value) <= 4:
        return value[0] + "•" * (len(value) - 1)
    return value[0] + "•" * (len(value) - 2) + value[-1]


def mask_line(line: str | None) -> str | None:
    """Mask the secret half of an `identifier:secret` combolist line."""
    if not line:
        return line
    for sep in (":", ";", "|"):
        if sep in line:
            ident, _, secret = line.partition(sep)
            return f"{ident}{sep}{mask_secret(secret)}"
    return line


@dataclass
class ProviderResult:
    key: str
    label: str
    ok: bool
    count: int
    status: str
    hits: list[LeakHit] = field(default_factory=list)


class LeakProvider:
    key: str = "base"
    label: str = "Base"
    supported_kinds: tuple[str, ...] = ("email", "username", "domain")

    def supports(self, kind: str) -> bool:
        return kind in self.supported_kinds

    async def search(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> ProviderResult:  # pragma: no cover - interface
        raise NotImplementedError

    # helpers for subclasses
    def _skip(self, reason: str = "not applicable for this query type") -> ProviderResult:
        return ProviderResult(self.key, self.label, ok=True, count=0, status=reason)

    def _fail(self, reason: str) -> ProviderResult:
        return ProviderResult(self.key, self.label, ok=False, count=0, status=reason)

    def _ok(self, hits: list[LeakHit], status: str | None = None) -> ProviderResult:
        return ProviderResult(
            self.key,
            self.label,
            ok=True,
            count=len(hits),
            status=status or (f"{len(hits)} hit(s)" if hits else "no results"),
            hits=hits,
        )

"""Password checker — has this password turned up in a breach, and how often?

The console hashes the password in the browser and sends only its SHA-1, so
the plaintext never reaches this server. Each hash is looked up with
leakedpassword.com, which answers from Have I Been Pwned's Pwned Passwords:

    GET https://leakedpassword.com/api/?p=&s=<sha1>
    {"password": {"leak": true, "hash": "<sha1>", "seen": 52372427}}

Two quirks of that API, both visible in its source
(github.com/triss90/leakedpassword.com, api/index.php):

* A hash-only query, `?s=<sha1>`, is refused as "Invalid API query": the guard
  reads `!isset($_GET['p']) || …`, so `p` has to be present. It is sent empty.
  The password parameter never carries anything.
* `leak` is computed as `$match != ""`, which on PHP 7 reads a match on the
  first line of the range response as no match while still filling in `seen`.
  A non-zero `seen` is therefore taken as leaked whatever `leak` says.

Errors arrive as HTTP 200 with {"error": "..."}; a failure on its side calling
Pwned Passwords comes back as "Query from non-secure connection". On any of
those, or a timeout, the hash is looked up in the Pwned Passwords range API
directly when PASSWORDS_HIBP_FALLBACK is on. Same data, and only the first five
hex characters of the hash leave this server (k-anonymity).

Nothing here logs, caches or stores a hash.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from ..config import settings
from ..models import PasswordCheckResponse, PasswordResult

HIBP_RANGE = "https://api.pwnedpasswords.com/range/"
# Pwned Passwords is CC BY 4.0, and leakedpassword.com's terms require the
# source to be named wherever the data is shown.
ATTRIBUTION = (
    "Pwned Passwords by Have I Been Pwned (haveibeenpwned.com, CC BY 4.0), "
    "via leakedpassword.com"
)
# Both APIs' terms ask for a user agent that names the client honestly.
_UA = {"User-Agent": "decint-tools/1.0 (password checker)"}
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
# What leakedpassword.com says when its own call to Pwned Passwords fails —
# nothing to do with how it was asked, so it is reported as what it means.
_UPSTREAM_FAILED = "Query from non-secure connection"


class LookupFailed(Exception):
    """A source could not answer for this hash. The message names the source."""


def normalize(value: str) -> str | None:
    """The value as a lowercase SHA-1 hex digest, or None if it is not one."""
    v = value.strip().lower()
    return v if _SHA1.match(v) else None


async def _leakedpassword(client: httpx.AsyncClient, sha1: str) -> tuple[bool, int]:
    """(leaked, seen) from leakedpassword.com, or LookupFailed."""
    try:
        r = await client.get(
            settings.passwords_api_url, params={"p": "", "s": sha1}, headers=_UA
        )
        r.raise_for_status()
        # Served as "Content-Type: charset=utf-8", so r.json() can't be trusted
        # to recognise it; the body is JSON regardless.
        data = json.loads(r.text)
    except (httpx.HTTPError, ValueError) as e:
        raise LookupFailed(f"leakedpassword.com: {type(e).__name__}") from e

    if not isinstance(data, dict):
        raise LookupFailed("leakedpassword.com: unexpected response")
    if "error" in data:
        if data["error"] == _UPSTREAM_FAILED:
            raise LookupFailed("leakedpassword.com: its Pwned Passwords lookup failed")
        raise LookupFailed(f"leakedpassword.com: {data['error']}")
    pw = data.get("password")
    # The hash is echoed back; an answer about some other hash is no answer.
    if not isinstance(pw, dict) or str(pw.get("hash", "")).lower() != sha1:
        raise LookupFailed("leakedpassword.com: unexpected response")
    try:
        seen = max(0, int(pw.get("seen") or 0))
    except (TypeError, ValueError) as e:
        raise LookupFailed("leakedpassword.com: unexpected response") from e
    return bool(pw.get("leak")) or seen > 0, seen


async def _hibp_range(client: httpx.AsyncClient, prefix: str) -> dict[str, int]:
    """{suffix: count} for every hash starting with `prefix`, or LookupFailed."""
    try:
        r = await client.get(
            HIBP_RANGE + prefix.upper(),
            # Pads the response with fake zero-count rows so its size doesn't
            # hint at which prefix was asked for.
            headers={**_UA, "Add-Padding": "true"},
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LookupFailed(f"Pwned Passwords: {type(e).__name__}") from e

    out: dict[str, int] = {}
    for line in r.text.splitlines():
        suffix, _, count = line.strip().partition(":")
        try:
            n = int(count)
        except ValueError:
            continue
        if n > 0:  # padding rows have a count of 0
            out[suffix.lower()] = n
    return out


async def check(hashes: list[str]) -> PasswordCheckResponse:
    """Look up validated, de-duplicated SHA-1 digests. One result per hash, in
    the order given. A hash neither source could answer for is ok=False with
    the reason; it is never reported as "not leaked"."""
    sem = asyncio.Semaphore(max(1, settings.passwords_concurrency))
    results: dict[str, PasswordResult] = {}
    errors: dict[str, str] = {}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.passwords_timeout), follow_redirects=True
    ) as client:

        async def primary(h: str) -> None:
            async with sem:
                try:
                    leaked, seen = await _leakedpassword(client, h)
                except LookupFailed as e:
                    errors[h] = str(e)
                    return
            results[h] = PasswordResult(
                hash=h, ok=True, leaked=leaked, seen=seen, source="leakedpassword"
            )

        await asyncio.gather(*(primary(h) for h in hashes))

        failed = [h for h in hashes if h not in results]
        if failed and settings.passwords_hibp_fallback:
            # Hashes sharing a prefix share one range response.
            ranges: dict[str, dict[str, int]] = {}
            range_errors: dict[str, str] = {}

            async def fallback(prefix: str) -> None:
                async with sem:
                    try:
                        ranges[prefix] = await _hibp_range(client, prefix)
                    except LookupFailed as e:
                        range_errors[prefix] = str(e)

            await asyncio.gather(*(fallback(p) for p in sorted({h[:5] for h in failed})))
            for h in failed:
                rng = ranges.get(h[:5])
                if rng is None:
                    errors[h] = f"{errors[h]}; {range_errors[h[:5]]}"
                    continue
                seen = rng.get(h[5:], 0)
                results[h] = PasswordResult(
                    hash=h, ok=True, leaked=seen > 0, seen=seen, source="hibp_range"
                )

    ordered = [
        results.get(h) or PasswordResult(hash=h, ok=False, error=errors.get(h, "lookup failed"))
        for h in hashes
    ]
    return PasswordCheckResponse(
        total=len(ordered),
        leaked=sum(r.leaked for r in ordered),
        failed=sum(not r.ok for r in ordered),
        results=ordered,
        attribution=ATTRIBUTION,
    )

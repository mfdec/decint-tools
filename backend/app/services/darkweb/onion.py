"""Onion address helpers: v3 checksum validation and host extraction."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from urllib.parse import urlsplit

# A v3 service id is 56 base32 chars; v2 ids were 16 chars and stopped working in Oct 2021.
ONION_HOST_RE = re.compile(r"(?:[a-z0-9-]+\.)*([a-z2-7]{56}|[a-z2-7]{16})\.onion\b", re.IGNORECASE)
ONION_URL_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:[a-z2-7]{56}|[a-z2-7]{16})\.onion(?::\d+)?(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def service_id(host: str) -> str | None:
    """Return the service id (the label right before `.onion`) of a host, or None."""
    host = host.lower().strip().rstrip(".")
    if not host.endswith(".onion"):
        return None
    labels = host[: -len(".onion")].split(".")
    return labels[-1] if labels and labels[-1] else None


def is_valid_v3(host_or_id: str) -> bool:
    """Check the length, version byte and SHA3-256 checksum of a v3 onion address."""
    label = host_or_id.lower().strip()
    if label.endswith(".onion"):
        label = service_id(label) or ""
    if len(label) != 56:
        return False
    try:
        raw = base64.b32decode(label.upper())
    except (binascii.Error, ValueError):
        return False
    pubkey, checksum, version = raw[:32], raw[32:34], raw[34:35]
    if version != b"\x03":
        return False
    expected = hashlib.sha3_256(b".onion checksum" + pubkey + version).digest()[:2]
    return checksum == expected


def is_v2(host: str) -> bool:
    sid = service_id(host)
    return sid is not None and len(sid) == 16


def host_of(url: str) -> str:
    """Lower-cased hostname of a URL ("" when there is none)."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def onion_host(url: str) -> str | None:
    """Return the `.onion` hostname of a URL, or None for clearnet/invalid URLs."""
    host = host_of(url)
    return host if host.endswith(".onion") else None


def find_onion_urls(text: str) -> list[str]:
    """All onion URLs mentioned in free text, in order of appearance, de-duplicated."""
    seen: dict[str, None] = {}
    for match in ONION_URL_RE.finditer(text):
        seen.setdefault(match.group(0).rstrip(".,;:)]}"), None)
    return list(seen)

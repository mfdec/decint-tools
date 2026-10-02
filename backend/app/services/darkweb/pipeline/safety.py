"""Mandatory child-safety filtering. Not configurable and not disableable.

Queries that look like searches for child sexual abuse material are refused outright, and any
result whose title/snippet/URL matches is dropped and only counted (never listed, not even in the
pruned view). Results on Ahmia's banned-onion list (MD5 of the hostname) are handled the same way.
"""

from __future__ import annotations

import hashlib
import re

# Terms that on their own indicate CSAM.
_HARD_TERMS = [
    r"child\s*porn\w*", r"kiddie\s*porn\w*", r"kiddy\s*porn\w*", r"pthc", r"hurtcore",
    r"jail\s*bait", r"pre-?teens?", r"pedo(?:s|phile|philes|philia)?", r"paedo(?:s|phile|philes|philia)?",
    r"lolitas?", r"loli", r"shota(?:con)?", r"csam", r"cp\s*(?:porn|video|videos|vids|pics|links|collection)",
    r"underage\s*(?:porn|sex|nude\w*|girls?|boys?)",
]
# A term referring to minors plus a sexual term in the same text.
_MINOR_TERMS = [
    r"child(?:ren)?", r"kids?", r"minors?", r"underage", r"under-age", r"toddlers?", r"infants?",
    r"bab(?:y|ies)", r"school\s*girls?", r"school\s*boys?", r"teens?", r"(?:[5-9]|1[0-7])\s*(?:yo|y/o|yrs?|years?\s*old)",
]
_SEXUAL_TERMS = [
    r"porn\w*", r"sex\w*", r"nude\w*", r"naked", r"xxx", r"erotic\w*", r"hardcore", r"nsfw", r"rape\w*",
]

_TEST_EXTRA_TERMS: list[str] = []  # tests inject placeholder terms here; never set in production


def _compile(terms: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.IGNORECASE)


_HARD_RE = _compile(_HARD_TERMS)
_MINOR_RE = _compile(_MINOR_TERMS)
_SEXUAL_RE = _compile(_SEXUAL_TERMS)


def _normalise(text: str) -> str:
    # Catch separators used to dodge filters ("p.t.h.c", "child_porn").
    return re.sub(r"[_\.\-+]+", " ", text or "")


def is_blocked_text(text: str) -> bool:
    norm = _normalise(text)
    if _HARD_RE.search(norm):
        return True
    if _TEST_EXTRA_TERMS and _compile([re.escape(t) for t in _TEST_EXTRA_TERMS]).search(norm):
        return True
    return bool(_MINOR_RE.search(norm) and _SEXUAL_RE.search(norm))


def is_blocked_query(query: str) -> bool:
    return is_blocked_text(query)


def host_md5(host: str) -> str:
    return hashlib.md5(host.lower().encode("utf-8"), usedforsecurity=False).hexdigest()


def is_banned_host(host: str, banned_md5: set[str]) -> bool:
    return bool(banned_md5) and host_md5(host) in banned_md5

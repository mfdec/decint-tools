"""Text cleanup and tokenisation shared by parsers and the ranking pipeline."""

from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because been before being
    below between both but by can did do does doing down during each few for from further had has
    have having he her here hers herself him himself his how i if in into is it its itself just me
    more most my myself no nor not now of off on once only or other our ours ourselves out over own
    same she should so some such than that the their theirs them themselves then there these they
    this those through to too under until up very was we were what when where which while who whom
    why will with you your yours yourself yourselves http https www com onion html php htm index
    """.split()  # noqa: SIM905
)


def clean_text(value: str | None, max_len: int | None = 400) -> str:
    """Strip markup/entities/zero-width chars and collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", value))
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    if max_len and len(text) > max_len:
        cut = text[:max_len].rsplit(" ", 1)[0]
        text = cut.rstrip(" .,;:-") + "…"
    return text


_URL_IN_TEXT_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)*[a-z2-7]{56}\.onion(?::\d+)?(?:/[^\s<>\"']*)?|https?://[^\s<>\"']+", re.I
)


def strip_urls(text: str) -> str:
    """Remove URLs/onion addresses from a snippet (engines often echo the address in it)."""
    return _WS_RE.sub(" ", _URL_IN_TEXT_RE.sub(" ", text)).strip(" -|·:")


_PLACEHOLDER_RE =re.compile(r"^(?:no description(?: provided| available)?|no title|n/?a|none|-+)\.?$", re.I)


def is_placeholder(text: str) -> bool:
    """Engine filler such as "No description provided"."""
    return bool(_PLACEHOLDER_RE.match(text.strip()))


def is_truncated(text: str) -> bool:
    return text.endswith(("...", "…"))


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(unicodedata.normalize("NFKC", text or ""))]
    if keep_stopwords:
        return tokens
    return [t for t in tokens if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]


def has_alnum(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def symbol_count(text: str) -> int:
    """Count emoji / pictographic symbols (Unicode category So/Sk, plus variation selectors)."""
    return sum(1 for ch in text if unicodedata.category(ch) in ("So", "Sk"))

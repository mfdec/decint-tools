"""Search-box syntax: "quoted phrases", -excluded words and -"excluded phrases".

Onion engines are crude full-text indexes and mostly ignore operators, so we do not forward
them. The engines get the plain words; the operators are enforced afterwards on the merged
results (see pipeline/filters.py), where every pruned hit stays visible with its reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# -"phrase" or "phrase"; a lone opening quote is treated as an ordinary character.
_QUOTED_RE = re.compile(r'(-?)"([^"]+)"')
_MAX_OPERATORS = 8  # a pasted wall of -words must not turn into a wall of filters


@dataclass(frozen=True)
class QuerySpec:
    raw: str
    terms: list[str] = field(default_factory=list)  # plain words, as typed
    phrases: list[str] = field(default_factory=list)  # required exact phrases
    excluded: list[str] = field(default_factory=list)  # words a result must not contain
    excluded_phrases: list[str] = field(default_factory=list)

    @property
    def engine_query(self) -> str:
        """What is actually sent to the engines: words and phrase text, no operators."""
        return " ".join([*self.terms, *self.phrases])

    @property
    def searchable(self) -> bool:
        return bool(self.terms or self.phrases)

    @property
    def has_operators(self) -> bool:
        return bool(self.phrases or self.excluded or self.excluded_phrases)

    @property
    def operators(self) -> dict[str, list[str]]:
        """The operators in force, for the manifest and the UI."""
        out = {
            "phrases": self.phrases,
            "excluded": self.excluded,
            "excluded_phrases": self.excluded_phrases,
        }
        return {k: v for k, v in out.items() if v}


def parse_query(raw: str) -> QuerySpec:
    raw = " ".join((raw or "").split())
    phrases: list[str] = []
    excluded_phrases: list[str] = []

    def take(match: re.Match[str]) -> str:
        text = match.group(2).strip()
        if text:
            (excluded_phrases if match.group(1) else phrases).append(text)
        return " "

    rest = _QUOTED_RE.sub(take, raw)
    terms: list[str] = []
    excluded: list[str] = []
    for word in rest.replace('"', " ").split():
        if word.startswith("-") and len(word) > 1:
            excluded.append(word[1:])
        elif word.startswith("+") and len(word) > 1:
            terms.append(word[1:])
        elif word.strip("-+"):
            terms.append(word)
    return QuerySpec(
        raw=raw,
        terms=_dedupe(terms),
        phrases=_dedupe(phrases)[:_MAX_OPERATORS],
        excluded=_dedupe(excluded)[:_MAX_OPERATORS],
        excluded_phrases=_dedupe(excluded_phrases)[:_MAX_OPERATORS],
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out

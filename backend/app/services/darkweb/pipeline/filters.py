"""Enforce the search-box operators ("phrases", -excluded) on merged results."""

from __future__ import annotations

from dataclasses import dataclass

from ..query import QuerySpec
from ..text import tokenize
from .dedupe import Merged
from .rank import stem, stems, url_terms

# (phrase as typed, its stemmed tokens): kept together so the reason can quote what the user wrote.
Phrase = tuple[str, tuple[str, ...]]


def _phrase(typed: str) -> Phrase | None:
    # Stopwords stay inside a phrase: "end of life" must match as written.
    tokens = tuple(stem(t) for t in tokenize(typed, keep_stopwords=True))
    return (typed, tokens) if tokens else None


def _contains(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return n > 0 and any(tuple(tokens[i : i + n]) == phrase for i in range(len(tokens) - n + 1))


@dataclass(frozen=True)
class QueryFilters:
    excluded: frozenset[str] = frozenset()
    phrases: tuple[Phrase, ...] = ()
    excluded_phrases: tuple[Phrase, ...] = ()

    @classmethod
    def from_spec(cls, spec: QuerySpec) -> QueryFilters:
        return cls(
            excluded=frozenset(s for w in spec.excluded for s in stems(w)),
            phrases=tuple(p for p in map(_phrase, spec.phrases) if p),
            excluded_phrases=tuple(p for p in map(_phrase, spec.excluded_phrases) if p),
        )

    @property
    def active(self) -> bool:
        return bool(self.excluded or self.phrases or self.excluded_phrases)

    def verdict(self, m: Merged) -> tuple[str, str] | None:
        """(prune reason, detail) when a result breaks an operator, else None.

        Judged on everything any engine said about the page: a phrase one engine's snippet
        carries is enough, and an excluded word in any engine's snippet is enough to drop it.
        """
        if not self.active:
            return None
        text = " ".join([*m.titles, *m.snippets])
        words = set(stems(text)) | set(url_terms(m.key))
        hit = self.excluded & words
        if hit:
            return "excluded", f"contains excluded term: {', '.join(sorted(hit))}"
        tokens = [stem(t) for t in tokenize(text, keep_stopwords=True)]
        for typed, phrase in self.excluded_phrases:
            if _contains(tokens, phrase):
                return "excluded", f'contains excluded phrase: "{typed}"'
        for typed, phrase in self.phrases:
            if not _contains(tokens, phrase):
                return "phrase_missing", f'phrase not found in title or description: "{typed}"'
        return None

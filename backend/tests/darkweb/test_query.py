"""Search-box syntax: parsing, and enforcement on real replayed results."""

from urllib.parse import unquote_plus

from app.services.darkweb.pipeline import safety
from app.services.darkweb.query import QuerySpec, parse_query
from app.services.darkweb.tor import FetchResponse, ReplayFetcher

# ─────────────────────────── parsing ───────────────────────────


def test_plain_words():
    spec = parse_query("ransomware  leak   site")
    assert spec.terms == ["ransomware", "leak", "site"]
    assert not spec.has_operators and spec.searchable
    assert spec.engine_query == "ransomware leak site"


def test_phrase_exclusion_and_required_marker():
    spec = parse_query('"leaked database" -conti +acme -lockbit')
    assert spec.phrases == ["leaked database"]
    assert spec.terms == ["acme"]
    assert spec.excluded == ["conti", "lockbit"]
    assert spec.operators == {"phrases": ["leaked database"], "excluded": ["conti", "lockbit"]}


def test_excluded_phrase_is_not_read_as_a_required_one():
    spec = parse_query('bitcoin -"hacker for hire"')
    assert spec.excluded_phrases == ["hacker for hire"]
    assert spec.phrases == []  # would invert the searcher's intent


def test_engines_never_see_operators():
    spec = parse_query('"bitcoin core" wallet -doubler -"hire a hacker"')
    assert spec.engine_query == "wallet bitcoin core"
    assert '"' not in spec.engine_query and "-" not in spec.engine_query


def test_query_of_only_exclusions_is_not_searchable():
    for q in ("-doubler", '-"a b"', "-x -y", "   ", '""'):
        assert not parse_query(q).searchable, q


def test_hyphens_inside_words_and_stray_quotes_are_ordinary():
    assert parse_query("anti-virus e-mail").terms == ["anti-virus", "e-mail"]
    spec = parse_query('foo "bar')
    assert spec.terms == ["foo", "bar"] and not spec.phrases
    assert parse_query("5 - 3").terms == ["5", "3"]  # a lone dash is not an exclusion


def test_duplicates_collapse_and_operator_count_is_capped():
    assert parse_query("a A a").terms == ["a"]
    spec = parse_query(" ".join(f"-w{i}" for i in range(50)) + " keep")
    assert len(spec.excluded) == 8 and spec.terms == ["keep"]


def test_spec_is_hashable_data():
    assert parse_query("a") == QuerySpec(raw="a", terms=["a"])


# ─────────────────────────── enforcement on replayed engines ───────────────────────────


class Recording(ReplayFetcher):
    """Replay fetcher that remembers every URL it was asked for, and why."""

    def __init__(self, inner):
        super().__init__(inner.directory)
        self.calls: list[tuple[str, str]] = []  # (url, kind: page1 | prep | banlist ...)

    @property
    def urls(self) -> list[str]:
        return [u for u, _ in self.calls]

    @property
    def searches(self) -> list[str]:
        """Only the result-page requests, with the query decoded back to text."""
        return [unquote_plus(u) for u, kind in self.calls if kind.startswith("page")]

    async def get(self, url, *, engine, kind="page1", timeout=None) -> FetchResponse:
        self.calls.append((url, kind))
        return await super().get(url, engine=engine, kind=kind, timeout=timeout)


async def test_excluded_word_is_dropped_and_explained(replay_aggregator):
    base = await replay_aggregator.search("bitcoin")
    resp = await replay_aggregator.search("bitcoin -doubler")
    assert resp.operators == {"excluded": ["doubler"]}
    hit = [p for p in resp.pruned if p.reason == "excluded"]
    assert len(hit) == 1 and "doubler" in hit[0].title.lower()
    assert "doubler" in hit[0].detail
    assert not any("doubler" in r.title.lower() for r in resp.results)
    # Everything else is untouched: only that one result left the page.
    assert len(resp.results) == len(base.results)


async def test_excluded_phrase(replay_aggregator):
    resp = await replay_aggregator.search('bitcoin -"hacker for hire"')
    dropped = [p for p in resp.pruned if p.reason == "excluded"]
    assert len(dropped) == 1 and 'excluded phrase: "hacker for hire"' in dropped[0].detail
    assert not any("hacker for hire" in (r.title + r.snippet).lower() for r in resp.results)


async def test_required_phrase_keeps_only_pages_that_say_it(replay_aggregator):
    resp = await replay_aggregator.search('"bitcoin core"')
    assert resp.operators == {"phrases": ["bitcoin core"]}
    assert resp.results, "the Bitcoin Core page says it"
    for r in resp.results:
        assert "bitcoin core" in f"{r.title} {r.snippet}".lower()
    missing = [p for p in resp.pruned if p.reason == "phrase_missing"]
    assert missing and all('"bitcoin core"' in p.detail for p in missing)
    assert resp.stats.pruned_by_reason["phrase_missing"] == len(missing)


async def test_operators_reach_engines_as_plain_words(replay_aggregator):
    replay_aggregator._fetcher = rec = Recording(replay_aggregator.fetcher)
    resp = await replay_aggregator.search('"bitcoin core" -doubler')
    assert resp.results
    assert len(rec.searches) >= 10, "every engine got a search request"
    for sent in rec.searches:
        assert "bitcoin core" in sent, sent  # the phrase's words, in order, unquoted
        assert "doubler" not in sent and '"' not in sent and "-" not in sent.split("?", 1)[1], sent


async def test_only_exclusions_is_refused_with_a_message_and_no_traffic(replay_aggregator):
    replay_aggregator._fetcher = rec = Recording(replay_aggregator.fetcher)
    resp = await replay_aggregator.search("-doubler")
    assert resp.results == [] and "at least one search word" in resp.message
    assert rec.urls == []


# ─────────────────────────── the safety gate cannot be disguised ───────────────────────────


async def test_blocked_term_is_caught_however_the_query_is_dressed(replay_aggregator, monkeypatch):
    monkeypatch.setattr(safety, "_TEST_EXTRA_TERMS", ["zzblockedplaceholderzz"])
    replay_aggregator._fetcher = rec = Recording(replay_aggregator.fetcher)
    for q in (
        "zzblockedplaceholderzz",
        '"zzblockedplaceholderzz"',
        'bitcoin "zzblockedplaceholderzz"',
        'bitcoin -wallet "zzblockedplaceholderzz"',
        "ZZBLOCKEDPLACEHOLDERZZ -bitcoin +x",
    ):
        resp = await replay_aggregator.search(q)
        assert resp.blocked_query and resp.results == [], q
        assert "refuses searches for child sexual abuse material" in resp.message
    assert rec.urls == [], "a blocked query must not reach a single engine"


async def test_term_hidden_only_in_an_exclusion_still_counts_as_a_blocked_query(replay_aggregator, monkeypatch):
    # The raw text is checked as typed, so excluding a blocked word cannot be used to probe.
    monkeypatch.setattr(safety, "_TEST_EXTRA_TERMS", ["zzblockedplaceholderzz"])
    resp = await replay_aggregator.search("bitcoin -zzblockedplaceholderzz")
    assert resp.blocked_query

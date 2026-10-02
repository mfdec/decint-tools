"""End-to-end: replay 11 engines' saved result pages through the full aggregator and pipeline.

vormweb/onionland/onionsearchengine/onionengine are real captures of the clearnet gateways;
the onion-only engines are synthetic pages built from their documented markup (tests/README.md).
"""

from collections import Counter

from app.services.darkweb.onion import is_valid_v3

BTC_CORE = "6hasakffvppilxgehrswmffqurlcjjjhd76jgvaqmsg6ul25s7t3rzyd.onion"
TROCADOR = "65bsisadnxvw4kfz7h7a3jwcyenrhluuj3kd5toslfzxbk5q4m3wy6qd.onion"
MIXTUM = "mixtumjzn2tsiusfkdhutntamspsg43kgt764qbdaxjebce4h6fcfiad.onion"


async def test_full_search_over_replayed_engines(replay_aggregator):
    events = [e async for e in replay_aggregator.stream("bitcoin")]
    kinds = Counter(e["type"] for e in events)
    assert kinds == {"start": 1, "engine": 11, "results": 1}
    resp = events[-1]["response"]

    assert resp.transport == "replay"
    assert resp.stats.engines_queried == 11 and resp.stats.engines_with_results == 11
    assert resp.stats.raw_results > 70
    assert resp.stats.merged_duplicates >= 20

    # Consensus: the page every engine agrees on wins.
    top = resp.results[0]
    assert top.host == BTC_CORE
    assert len(top.engines) >= 5

    # The phishing clone of Trocador is collapsed under the real one, not shown separately.
    troc = next(r for r in resp.results if r.host == TROCADOR)
    assert len(troc.mirrors) == 1 and "verified" in troc.flags
    assert all(r.host != troc.mirrors[0].url.split("/")[2] for r in resp.results)

    # Two front-ends of one index (Onion Search Engine + Onion Engine) count once for consensus.
    mixtum = next(r for r in resp.results if r.host == MIXTUM)
    assert {"onionsearchengine", "onionengine", "torch"} <= set(mixtum.engines)
    assert mixtum.breakdown.consensus < top.breakdown.consensus

    reasons = Counter(p.reason for p in resp.pruned)
    assert reasons["sponsored"] == 4          # OnionLand's injected market ads
    assert reasons["dead_v2"] == 1
    assert reasons["invalid_address"] == 1    # Trocador look-alike with a bad checksum
    assert reasons["non_onion"] == 2          # clearnet blockchair result from both OSE front-ends
    assert reasons["near_duplicate"] >= 1     # Bitcoin Core /en page
    assert reasons["low_relevance"] >= 3      # sourdough recipes, off-topic market pages
    spam_titles = " ".join(p.title for p in resp.pruned if p.reason == "spam").lower()
    assert "doubler" in spam_titles and "hacker" in spam_titles

    # Ahmia-banned host is withheld and only counted.
    assert resp.stats.safety_blocked >= 1
    assert not any("giveaway" in r.title.lower() for r in resp.results)
    assert not any("giveaway" in p.title.lower() for p in resp.pruned)

    # OnionLand ran this URL as an ad and also listed it organically: the organic copy is kept for
    # judging, but it is an advertiser's link to another directory's search page, so it sinks.
    free = [r for r in resp.results if r.title == "FREE BITCOIN"]
    if free:
        assert {"advertiser", "search-page"} <= set(free[0].flags)
        assert resp.results.index(free[0]) > len(resp.results) // 2
    else:
        assert any(p.title == "FREE BITCOIN" and p.reason == "spam" for p in resp.pruned)

    # Every result is a valid v3 onion, no site floods the page, scores are sorted.
    assert all(is_valid_v3(r.host) for r in resp.results)
    assert max(Counter(r.host for r in resp.results).values()) <= replay_aggregator.settings.max_per_host
    assert [r.score for r in resp.results] == sorted((r.score for r in resp.results), reverse=True)


async def test_engine_filter_and_unknown_engine(replay_aggregator):
    resp = await replay_aggregator.search("bitcoin", engines=["vormweb", "ahmia"])
    assert sorted(r.engine for r in resp.engines) == ["ahmia", "vormweb"]
    resp = await replay_aggregator.search("bitcoin", engines=["does-not-exist"])
    assert resp.engines == [] and resp.results == []

from app.services.darkweb.config import Settings
from app.services.darkweb.models import RawResult
from app.services.darkweb.pipeline import PipelineContext, run_pipeline
from app.services.darkweb.pipeline.rank import bm25, stem

from .conftest import fake_onion


def raw(engine, host_seed, title, snippet, rank=1, path="/", **kw):
    return RawResult(engine=engine, url=f"http://{fake_onion(host_seed)}{path}", title=title,
                     snippet=snippet, rank=rank, **kw)


def run(results, query, groups=None, weights=None, **settings_kw):
    s = Settings(**settings_kw)
    ctx = PipelineContext(settings=s, weights=weights or {}, groups=groups or {})
    return run_pipeline(query, results, ctx)


TEXT = ("Monero wallet guide", "Set up a Monero wallet, verify downloads and keep your keys offline.")


def text(i):
    """Distinct but on-topic text (identical text on different hosts would cluster as mirrors)."""
    topics = ["cold storage", "hardware keys", "view keys", "subaddresses", "multisig", "seed backups",
              "node sync", "fee tuning", "ring size", "remote nodes", "cli usage", "gui setup"]
    t = topics[i % len(topics)]
    return f"Monero wallet {t} #{i}", f"Notes on {t} for a Monero wallet, part {i}, with examples."


def test_consensus_beats_single_engine_with_identical_text():
    results, _, _ = run([
        raw("tor66", "solo", *TEXT, rank=1),
        raw("ahmia", "popular", *TEXT[:1], TEXT[1] + " Popular.", rank=2),
        raw("torch", "popular", *TEXT[:1], TEXT[1] + " Popular.", rank=2),
        raw("tordex", "popular", *TEXT[:1], TEXT[1] + " Popular.", rank=3),
    ], "monero wallet")
    assert results[0].host == fake_onion("popular")
    assert results[0].breakdown.consensus > results[1].breakdown.consensus


def test_shared_index_counts_once_for_consensus():
    groups = {"onionsearchengine": "ose", "onionengine": "ose"}
    results, _, _ = run([
        raw("onionsearchengine", "a", *text(1)), raw("onionengine", "a", *text(1)),
        raw("ahmia", "b", *text(2)), raw("tor66", "b", *text(2)),
    ], "monero wallet", groups=groups)
    by_host = {r.host: r for r in results}
    assert by_host[fake_onion("b")].breakdown.consensus > by_host[fake_onion("a")].breakdown.consensus


def test_relevance_orders_by_query_match():
    results, _, _ = run([
        raw("a", "loose", "Crypto news", "Weekly market digest mentioning monero once.", rank=1),
        raw("a", "exact", "Monero wallet setup", "Monero wallet setup guide: monero wallet backup and restore.", rank=2),
    ], "monero wallet")
    assert results[0].host == fake_onion("exact")
    assert results[0].breakdown.relevance > results[1].breakdown.relevance


def test_scam_lexicon_is_pruned_but_exempt_when_queried():
    scam = raw("a", "scam", "Bitcoin doubler - double your bitcoin", "100% legit bitcoin doubler, guaranteed profit, free bitcoin.")
    legit = raw("a", "legit", "Bitcoin wallet guide", "How to secure a bitcoin wallet with backups.")
    results, pruned, _ = run([scam, legit], "bitcoin")
    assert [r.host for r in results] == [fake_onion("legit")]
    assert pruned[0].reason == "spam" and "scam-signals" in pruned[0].detail
    # Researching scams on purpose: the queried phrase is not held against results.
    cvv = raw("a", "shop", "CVV shop reviews", "Forum thread reviewing cvv shop listings and exit scams.")
    results, _, _ = run([cvv], "cvv shop")
    assert results and "scam-signals" not in results[0].flags


def test_emoji_and_shouting_titles_rank_below_clean_ones():
    results, _, _ = run([
        raw("a", "loud", "🔥🔥🔥 BEST MONERO WALLET EVER 🔥🔥🔥", "Monero wallet download.", rank=1),
        raw("a", "calm", "Monero wallet", "Monero wallet download and verification guide.", rank=2),
    ], "monero wallet")
    assert results[0].host == fake_onion("calm")
    loud = next(r for r in results if r.host == fake_onion("loud"))
    assert {"emoji-spam", "shouting"} <= set(loud.flags)


def test_vormweb_badges_adjust_quality():
    results, _, _ = run([
        raw("vormweb", "v", *text(1), badge="Verified"),
        raw("vormweb", "r", *text(2), badge="Risky"),
    ], "monero wallet")
    q = {r.host: r.quality for r in results}
    assert q[fake_onion("v")] > q[fake_onion("r")]


def test_low_relevance_only_when_single_source():
    results, pruned, _ = run([
        raw("a", "offtopic", "Cooking recipes", "Bread and soup."),
        raw("a", "offtopic2", "Garden tips", "Tomatoes and roses."),
        raw("b", "offtopic2", "Garden tips", "Tomatoes and roses."),
    ], "monero")
    assert [p.reason for p in pruned] == ["low_relevance"]
    assert [r.host for r in results] == [fake_onion("offtopic2")]


def test_host_crowding_collapses_extra_pages():
    pages = [raw("a", "site", f"Monero wallet page {i}", f"Monero wallet topic number {i} explained.", rank=i, path=f"/p{i}")
             for i in range(1, 6)]
    results, _, stats = run(pages + [raw("a", "other", *TEXT, rank=9)], "monero wallet", max_per_host=2)
    site = [r for r in results if r.host == fake_onion("site")]
    assert len(site) == 2
    assert len(site[0].more_from_site) == 3
    assert stats.collapsed == 3


def test_breakdown_and_limits():
    results, _, stats = run([raw("a", f"h{i}", *text(i), rank=i) for i in range(1, 30)], "monero wallet", max_results=10)
    assert len(results) == 10 and stats.shown == 10
    for r in results:
        b = r.breakdown
        assert 0 <= b.relevance <= 1 and 0 <= b.fusion <= 1 and 0 <= b.consensus <= 1
        assert r.score == b.final
    assert results == sorted(results, key=lambda r: r.score, reverse=True)


def test_bm25_and_stemming():
    assert stem("wallets") == stem("wallet") == "wallet"
    assert stem("hacking") == "hack"
    assert stem("glass") == "glass"
    scores = bm25([["monero", "wallet"], ["bread"], ["monero", "monero", "wallet", "wallet"]], ["monero", "wallet"])
    assert scores[1] == 0 and scores[2] > scores[0] > 0

import pytest

from app.services.darkweb.config import Settings
from app.services.darkweb.models import RawResult
from app.services.darkweb.pipeline import PipelineContext, run_pipeline, safety
from app.services.darkweb.pipeline.safety import host_md5

from .conftest import fake_onion

ENGINE_HOST = fake_onion("search-engine")


def raw(engine, url, title="Bitcoin wallet guide for beginners", snippet="How to keep your bitcoin wallet safe offline.", rank=1, **kw):
    return RawResult(engine=engine, url=url, title=title, snippet=snippet, rank=rank, **kw)


def run_ctx(results, query="bitcoin wallet", banned=frozenset(), groups=None, **settings_kw):
    s = Settings(**settings_kw)
    ctx = PipelineContext(settings=s, engine_hosts={ENGINE_HOST, "engine.example"}, weights={},
                          groups=groups or {}, banned_md5=set(banned))
    return run_pipeline(query, results, ctx)


def reasons(pruned):
    return {p.reason for p in pruned}


def test_same_page_from_three_engines_merges_into_one_result():
    site = fake_onion("wallet-guide")
    results, pruned, stats = run_ctx([
        raw("ahmia", f"http://{site}/", rank=3),
        raw("tor66", f"http://www.{site}/index.html?utm_source=tor66", rank=1),
        raw("torch", f"https://{site.upper()}", snippet="", rank=7),
    ])
    assert len(results) == 1
    r = results[0]
    assert sorted(r.engines) == ["ahmia", "tor66", "torch"]
    assert r.engine_ranks == {"ahmia": 3, "tor66": 1, "torch": 7}
    assert r.merged == 3 and stats.merged_duplicates == 2
    assert r.snippet == "How to keep your bitcoin wallet safe offline."


def test_hard_filters_record_reasons():
    good = fake_onion("good")
    tampered = good[:5] + ("a" if good[5] != "a" else "b") + good[6:]
    results, pruned, stats = run_ctx([
        raw("a", f"http://{good}/"),
        raw("a", f"http://{fake_onion('ad')}/", sponsored=True),
        raw("a", f"http://{ENGINE_HOST}/search?q=next"),
        raw("a", "https://engine.example/about"),
        raw("a", "https://news.example.com/bitcoin-wallet"),
        raw("a", "http://3g2upl4pq6kufc4m.onion/"),
        raw("a", f"http://{tampered}/"),
        raw("a", f"http://{fake_onion('empty')}/", title="★★★", snippet=""),
    ])
    assert [r.host for r in results] == [good]
    assert {p.reason: p.url.split("/")[2][:12] for p in pruned}.keys() == {
        "sponsored", "self_link", "non_onion", "dead_v2", "invalid_address", "empty"}
    assert stats.pruned_by_reason["self_link"] == 2


def test_mirror_clone_collapses_under_best_supported_copy():
    real, clone = fake_onion("exchange"), fake_onion("exchange-clone")
    title = "Trocador exchange aggregator - swap bitcoin and monero privately"
    snippet = "Swap coins privately and safely with our exchange aggregator over Tor, no JavaScript, no accounts."
    results, pruned, stats = run_ctx([
        raw("ahmia", f"http://{real}/", title, snippet),
        raw("tordex", f"http://{real}/", title, snippet),
        raw("tor66", f"http://{clone}/", title, snippet + " "),
    ], query="exchange")
    assert len(results) == 1
    assert results[0].host == real
    assert [m.url for m in results[0].mirrors] == [f"http://{clone}/"]
    assert "has-mirrors" in results[0].flags
    assert stats.mirror_clusters == 1


def test_near_duplicate_page_of_same_site_is_absorbed():
    site = fake_onion("core")
    title = "Bitcoin Core reference implementation downloads"
    snippet = "Download the latest Bitcoin Core release, read the release notes and documentation for full nodes."
    results, pruned, _ = run_ctx([
        raw("ahmia", f"http://{site}/", title, snippet),
        raw("torch", f"http://{site}/en/", title, snippet),
    ], query="bitcoin core")
    assert len(results) == 1
    assert sorted(results[0].engines) == ["ahmia", "torch"]  # absorbed page's engine counts
    assert [p.reason for p in pruned] == ["near_duplicate"]


def test_shared_boilerplate_snippets_do_not_merge_different_pages():
    site = fake_onion("market")
    boiler = "Sign in Sign up Email address Please provide a valid email address. Password Remember me Sign in"
    results, pruned, _ = run_ctx([
        raw("a", f"http://{site}/featured.php", "Featured listings - Dark Market", boiler),
        raw("a", f"http://{site}/wishlist.php", "Wishlist - Dark Market", boiler),
    ], query="market", max_per_host=5)
    assert len(results) == 2 and not pruned


def test_snippet_cleanup_strips_urls_and_placeholders():
    site = fake_onion("x")
    results, _, _ = run_ctx([
        raw("tor66", f"http://{site}/", "Bitcoin wallet", f"Great wallet guide http://{site}/ for beginners"),
        raw("ahmia", f"http://{fake_onion('y')}/", "Bitcoin wallet tips", "No description provided"),
    ])
    by_host = {r.host: r for r in results}
    assert by_host[site].snippet == "Great wallet guide for beginners"
    assert by_host[fake_onion("y")].snippet == ""


def test_safety_filter_withholds_and_counts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(safety, "_TEST_EXTRA_TERMS", ["zzblockedplaceholderzz"])
    ok_site, bad_site = fake_onion("ok"), fake_onion("bad")
    results, pruned, stats = run_ctx([
        raw("a", f"http://{ok_site}/"),
        raw("a", f"http://{bad_site}/", title="bitcoin wallet zzblockedplaceholderzz"),
    ])
    assert [r.host for r in results] == [ok_site]
    assert stats.safety_blocked == 1
    assert all(bad_site not in p.url for p in pruned)  # never listed, not even as pruned


def test_ahmia_banlist_hosts_are_withheld():
    banned = fake_onion("banned")
    results, pruned, stats = run_ctx(
        [raw("a", f"http://{banned}/"), raw("a", f"http://{fake_onion('fine')}/")],
        banned={host_md5(banned)},
    )
    assert banned not in {r.host for r in results}
    assert stats.safety_blocked == 1 and not pruned


def test_query_safety_examples():
    assert safety.is_blocked_query("pthc")
    assert safety.is_blocked_query("child   porn")
    assert safety.is_blocked_query("teen_nude pics")
    assert not safety.is_blocked_query("bitcoin wallet")
    assert not safety.is_blocked_query("child safety online resources")
    assert not safety.is_blocked_query("porn")  # adult content alone is not blocked

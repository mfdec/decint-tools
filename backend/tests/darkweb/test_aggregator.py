import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from app.services.darkweb import aggregator as aggregator_mod
from app.services.darkweb.aggregator import Aggregator
from app.services.darkweb.engines import Engine, EngineSpec
from app.services.darkweb.health import HealthStore
from app.services.darkweb.tor import HttpFetcher

from .conftest import fake_onion

A, B, C, D = (fake_onion(f"engine-{x}") for x in "abcd")
SITE1, SITE2, SITE3 = fake_onion("site1"), fake_onion("site2"), fake_onion("site3")


def page(*hits):
    items = "".join(f'<div class="hit"><a href="http://{h}/">Bitcoin page on {h[:6]}</a><p>All about bitcoin here.</p></div>'
                    for h in hits)
    return f"<html><body>{items}</body></html>"


def spec(name, mirrors, **kw):
    kw.setdefault("search_paths", ["/search?q={q}"])
    kw.setdefault("parser", "generic")
    return EngineSpec(name=name, label=name.title(), tier="core", mirrors=mirrors, **kw)


@pytest.fixture(autouse=True)
def tor_is_up(monkeypatch):
    async def reachable(_settings):
        return True

    monkeypatch.setattr(aggregator_mod, "proxy_reachable", reachable)


def make(settings, specs, health=None):
    return Aggregator(settings, engines=[Engine(s) for s in specs], fetcher=HttpFetcher(settings),
                      health=health or HealthStore(None))


@respx.mock
async def test_partial_results_when_engines_fail(settings):
    respx.get(f"http://{A}/search", params={"q": "bitcoin"}).respond(200, text=page(SITE1, SITE2))
    respx.get(f"http://{B}/search").respond(503, text="down")
    respx.get(f"http://{C}/search").mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get(f"http://{D}/search").mock(side_effect=httpx.ConnectError("no route"))
    agg = make(settings, [spec("alpha", [A]), spec("bravo", [B]), spec("charlie", [C]), spec("delta", [D])])
    resp = await agg.search("bitcoin")
    status = {r.engine: r.status for r in resp.engines}
    assert status == {"alpha": "ok", "bravo": "error", "charlie": "timeout", "delta": "error"}
    assert {r.host for r in resp.results} == {SITE1, SITE2}
    assert resp.stats.engines_queried == 4 and resp.stats.engines_with_results == 1
    await agg.aclose()


@respx.mock
async def test_global_deadline_returns_partial_results(settings):
    async def slow(_request):
        await asyncio.sleep(5)
        return httpx.Response(200, text=page(SITE3))

    respx.get(f"http://{A}/search").respond(200, text=page(SITE1))
    respx.get(f"http://{B}/search").mock(side_effect=slow)
    agg = make(settings, [spec("alpha", [A]), spec("bravo", [B])])
    started = time.monotonic()
    resp = await agg.search("bitcoin", deadline=0.5)
    assert time.monotonic() - started < 3
    report = {r.engine: r for r in resp.engines}
    assert report["alpha"].status == "ok"
    assert report["bravo"].status == "timeout" and "deadline" in report["bravo"].error
    assert [r.host for r in resp.results] == [SITE1]
    await agg.aclose()


@respx.mock
async def test_mirror_failover_and_alternate_paths(settings):
    respx.get(f"http://{A}/search").mock(side_effect=httpx.ConnectError("mirror down"))
    respx.get(f"http://{B}/search").respond(404)
    respx.get(f"http://{B}/find").respond(200, text=page(SITE1))
    engine = spec("multi", [A, B], search_paths=["/search?q={q}", "/find?q={q}"])
    agg = make(settings, [engine])
    resp = await agg.search("bitcoin")
    rep = resp.engines[0]
    assert rep.status == "ok" and rep.endpoint == B
    await agg.aclose()


@respx.mock
async def test_pagination_stops_when_no_new_results(settings):
    route1 = respx.get(f"http://{A}/search", params={"q": "bitcoin", "page": "1"}).respond(200, text=page(SITE1))
    route2 = respx.get(f"http://{A}/search", params={"q": "bitcoin", "page": "2"}).respond(200, text=page(SITE2))
    route3 = respx.get(f"http://{A}/search", params={"q": "bitcoin", "page": "3"}).respond(200, text=page(SITE2))
    route4 = respx.get(f"http://{A}/search", params={"q": "bitcoin", "page": "4"}).respond(200, text=page(SITE3))
    agg = make(settings, [spec("paged", [A], search_paths=["/search?q={q}&page={page}"], max_pages=4)])
    resp = await agg.search("bitcoin", pages=4)
    assert route1.called and route2.called and route3.called and not route4.called
    assert resp.engines[0].pages == 3 and resp.engines[0].results == 2
    await agg.aclose()


@respx.mock
async def test_ahmia_token_prep_and_token_gate(settings):
    home = '<form id="searchForm" action="/search/"><input name="q"><input type="hidden" name="f00d" value="beef"></form>'
    respx.get(f"http://{A}/").respond(200, text=home)
    results = (f'<div id="ahmiaResultsPage"><ol class="searchResults"><li class="result"><h4>'
               f'<a href="/search/redirect?search_term=bitcoin&redirect_url=http://{SITE1}/">Bitcoin wiki</a></h4>'
               f'<p>Bitcoin wiki pages</p><cite>{SITE1}</cite></li></ol></div>')
    search = respx.get(f"http://{A}/search/").respond(200, text=results)
    agg = make(settings, [spec("ahmia", [A], parser="ahmia", prep="ahmia_token", search_paths=["/search/?q={q}"])])
    resp = await agg.search("bitcoin")
    query = parse_qs(urlsplit(str(search.calls.last.request.url)).query)
    assert query == {"q": ["bitcoin"], "f00d": ["beef"]}
    assert [r.host for r in resp.results] == [SITE1]

    # A rejected token bounces to the homepage: reported as blocked, not as "no results".
    search.mock(return_value=httpx.Response(302, headers={"location": "/"}))
    agg.engines[0]._prep_cache.clear()
    resp = await agg.search("bitcoin")
    assert resp.engines[0].status == "blocked" and "token gate" in resp.engines[0].error
    await agg.aclose()


@respx.mock
async def test_redirects_out_of_onion_space_are_refused(settings):
    respx.get(f"http://{A}/search").respond(302, headers={"location": "https://tracker.example/x"})
    agg = make(settings, [spec("leaky", [A])])
    resp = await agg.search("bitcoin")
    assert resp.engines[0].status == "error" and "refused redirect" in resp.engines[0].error
    await agg.aclose()


@respx.mock
async def test_circuit_breaker_benches_failing_engine(settings):
    respx.get(f"http://{A}/search").respond(500)
    health = HealthStore(None, failures_to_bench=2, cooldown=600)
    agg = make(settings, [spec("flaky", [A])], health=health)
    for _ in range(2):
        assert (await agg.search("bitcoin")).engines[0].status == "error"
    resp = await agg.search("bitcoin")
    assert resp.engines[0].status == "benched"
    # probes ignore the bench so you can see when an engine comes back
    respx.get(f"http://{A}/search").respond(200, text=page(SITE1))
    probe = await agg.probe("bitcoin")
    assert probe.engines[0].status == "ok" and not health.is_benched("flaky")
    await agg.aclose()


@respx.mock
async def test_blocked_query_contacts_no_engine(settings, monkeypatch):
    from app.services.darkweb.pipeline import safety

    monkeypatch.setattr(safety, "_TEST_EXTRA_TERMS", ["zzblockedplaceholderzz"])
    route = respx.get(f"http://{A}/search").respond(200, text=page(SITE1))
    agg = make(settings, [spec("alpha", [A])])
    resp = await agg.search("bitcoin zzblockedplaceholderzz")
    assert resp.blocked_query and resp.results == [] and not route.called
    await agg.aclose()


@respx.mock
async def test_parser_crash_is_contained(settings, monkeypatch):
    from app.services.darkweb.engines import parsers

    def boom(_html, _ctx):
        raise RuntimeError("parser bug")

    monkeypatch.setitem(parsers.PARSERS, "tor66", boom)
    respx.get(f"http://{A}/search").respond(200, text="<hr><b><a href='x'>x</a></b>")
    respx.get(f"http://{B}/search").respond(200, text=page(SITE1))
    agg = make(settings, [spec("broken", [A], parser="tor66"), spec("fine", [B])])
    resp = await agg.search("bitcoin")
    status = {r.engine: r.status for r in resp.engines}
    assert status == {"broken": "error", "fine": "ok"}
    await agg.aclose()


async def test_tor_down_gives_actionable_message(settings, monkeypatch):
    async def unreachable(_settings):
        return False

    monkeypatch.setattr(aggregator_mod, "proxy_reachable", unreachable)
    agg = make(settings, [spec("alpha", [A])])
    resp = await agg.search("bitcoin")
    assert "Tor SOCKS proxy" in resp.message
    assert resp.engines[0].status == "error"
    await agg.aclose()


def test_each_engine_gets_its_own_circuit(settings):
    fetcher = HttpFetcher(settings)
    a, b = fetcher.proxy_url("ahmia"), fetcher.proxy_url("tor66")
    assert a.startswith("socks5h://ahmia-") and b.startswith("socks5h://tor66-") and a != b
    assert a.endswith("@127.0.0.1:9050")
    shared = HttpFetcher(settings.model_copy(update={"isolate_circuits": False}))
    assert shared.proxy_url("ahmia") == "socks5h://127.0.0.1:9050"
    direct = HttpFetcher(settings.model_copy(update={"transport": "direct"}))
    assert direct.proxy_url("ahmia") is None


def test_engine_selection_tiers_and_transport(settings):
    from app.services.darkweb.engines import load_catalog

    agg = Aggregator(settings, engines=load_catalog(), health=HealthStore(None))
    default = agg.select()
    assert all(e.spec.tier != "experimental" for e in default)
    assert len(agg.select(include_experimental=True)) > len(default)
    assert [e.name for e in agg.select(["ahmia", "tor66"])] == ["ahmia", "tor66"]
    direct = Aggregator(settings.model_copy(update={"transport": "direct"}), engines=load_catalog(),
                        health=HealthStore(None))
    names = {e.name for e in direct.select()}
    assert names == {"ahmia", "onionland", "vormweb", "onionsearchengine", "onionengine"}


@respx.mock
async def test_ahmia_banlist_is_fetched_and_applied(settings):
    from app.services.darkweb.pipeline.safety import host_md5

    respx.get(f"http://{A}/banned/").respond(200, text=f"{host_md5(SITE2)}\n{'0' * 32}\n")
    respx.get(f"http://{A}/search").respond(200, text=page(SITE1, SITE2))
    agg = make(settings.model_copy(update={"use_ahmia_banlist": True}), [spec("ahmia", [A])])
    resp = await agg.search("bitcoin")
    assert [r.host for r in resp.results] == [SITE1]
    assert resp.stats.safety_blocked == 1
    assert (settings.cache_dir / "ahmia_banned.txt").is_file()
    await agg.aclose()


@respx.mock
async def test_closing_the_stream_early_cancels_engine_tasks(settings):
    started = asyncio.Event()

    async def slow(_request):
        started.set()
        await asyncio.sleep(30)
        return httpx.Response(200, text=page(SITE1))

    respx.get(f"http://{A}/search").mock(side_effect=slow)
    agg = make(settings.model_copy(update={"use_ahmia_banlist": True}), [spec("ahmia", [A])])
    stream = agg.stream("bitcoin")
    assert (await anext(stream))["type"] == "start"
    consumer = asyncio.create_task(anext(stream))
    await asyncio.wait_for(started.wait(), 5)
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    await stream.aclose()
    await asyncio.sleep(0)
    leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert leftovers == []
    await agg.aclose()

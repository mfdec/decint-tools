"""The package API the router uses: modes, Pro extras, preflight, refunds, redirect guard."""

from pathlib import Path

import httpx
import pytest
import respx

import app.services.darkweb as dw
from app.services.darkweb.config import Settings
from app.services.darkweb.extras import evidence_hash, extract_entities
from app.services.darkweb.models import EngineReport, PrunedItem, Result, SearchResponse
from app.services.darkweb.tor import FetchError, HttpFetcher, redirect_allowed

from .conftest import fake_onion


async def collect(query="bitcoin", mode="gateway", **kw):
    return [e async for e in dw.search_events(query, mode, **kw)]


# ─────────────────────────── settings per mode ───────────────────────────


def test_each_mode_gets_its_own_transport_and_cache(monkeypatch):
    monkeypatch.setattr(dw.app_settings, "tor_host", "10.0.0.5")
    monkeypatch.setattr(dw.app_settings, "tor_port", 9150)
    tor, gw = dw.settings_for("tor"), dw.settings_for("gateway")
    assert (tor.transport, gw.transport) == ("tor", "direct")
    assert tor.tor_proxy == "socks5h://10.0.0.5:9150"  # socks5h: DNS goes through Tor too
    assert tor.cache_dir != gw.cache_dir  # an engine's onion mirrors and its gateway fail separately
    assert gw.deadline < tor.deadline
    assert tor.isolate_circuits


# ─────────────────────────── modes over replayed engines ───────────────────────────


async def test_gateway_mode_queries_only_gateway_engines(replay):
    events = await collect(mode="gateway")
    assert events[0]["type"] == "start" and events[-1]["type"] == "results"
    names = {e["name"] for e in events[0]["engines"]}
    assert names == {"ahmia", "onionland", "vormweb", "onionsearchengine", "onionengine"}
    assert events[0]["transport"] == "replay"
    assert sum(e["type"] == "engine" for e in events) == 5


async def test_tor_mode_gets_entities_and_an_evidence_hash(replay):
    final = (await collect(mode="tor"))[-1]
    resp, manifest = final["response"], final["manifest"]
    assert len({r.engine for r in resp.engines}) == 11
    assert manifest["sha256"] == evidence_hash(resp.results)
    assert len(manifest["sha256"]) == 64
    assert manifest["mode"] == "tor" and manifest["keyword"] == "bitcoin"
    assert manifest["engines_responded"] == 11
    assert any(r.entities for r in resp.results), "wallet/onion/email indicators were extracted"
    assert resp.results[0].corroboration >= 5


async def test_gateway_mode_has_neither_entities_nor_hash(replay):
    """These are the Pro tier's features; fast mode must not hand them out."""
    final = (await collect(mode="gateway"))[-1]
    assert "sha256" not in final["manifest"]
    assert all(r.entities == {} for r in final["response"].results)


async def test_limit_trims_results_but_not_the_counts(replay):
    full = (await collect(mode="tor"))[-1]["response"]
    short = (await collect(mode="tor", limit=3))[-1]
    assert len(short["response"].results) == 3 == short["manifest"]["result_count"]
    assert short["response"].stats.shown == full.stats.shown  # stats describe the whole search


async def test_experimental_engines_are_tor_only_and_operator_switchable(replay, monkeypatch):
    seen = {}
    async def fake_stream(self, query, **kw):
        seen["experimental"] = kw["include_experimental"]
        yield {"type": "results", "response": SearchResponse(query=query)}
    monkeypatch.setattr(dw.Aggregator, "stream", fake_stream)
    await collect(mode="gateway", experimental=True)
    assert not seen["experimental"]
    await collect(mode="tor", experimental=True)
    assert seen["experimental"] is True
    monkeypatch.setattr(dw.app_settings, "darkweb_allow_experimental", False)
    await collect(mode="tor", experimental=True)
    assert not seen["experimental"]


async def test_pages_are_clamped_to_the_operator_cap(replay, monkeypatch):
    seen = {}
    async def fake_stream(self, query, **kw):
        seen["pages"] = kw["pages"]
        yield {"type": "results", "response": SearchResponse(query=query)}
    monkeypatch.setattr(dw.Aggregator, "stream", fake_stream)
    monkeypatch.setattr(dw.app_settings, "darkweb_max_pages", 2)
    await collect(pages=10)
    assert seen["pages"] == 2
    await collect(pages=0)
    assert seen["pages"] == 1


def test_pruned_list_is_capped_per_reason_but_counts_survive():
    pruned = [PrunedItem(url=f"u{i}", title="t", engines=["a"], reason="phrase_missing") for i in range(300)]
    pruned += [PrunedItem(url="x", title="t", engines=["a"], reason="spam")]
    kept = dw._cap_pruned(pruned)
    assert sum(p.reason == "phrase_missing" for p in kept) == 40
    assert sum(p.reason == "spam" for p in kept) == 1  # a rare reason is never crowded out


# ─────────────────────────── preflight and refunds ───────────────────────────


def test_preflight_accepts_operators_and_refuses_the_unsearchable():
    assert dw.preflight('"leaked database" -conti').engine_query == "leaked database"
    with pytest.raises(ValueError, match="at least one search word"):
        dw.preflight("-conti")


def test_preflight_refuses_blocked_queries(monkeypatch):
    from app.services.darkweb.pipeline import safety

    monkeypatch.setattr(safety, "_TEST_EXTRA_TERMS", ["zzblockedplaceholderzz"])
    with pytest.raises(ValueError, match="refuses searches for child sexual abuse material"):
        dw.preflight('fine -words "zzblockedplaceholderzz"')


def _report(status):
    return EngineReport(engine="e", status=status)


def test_upstream_failure_is_when_no_engine_answered():
    assert dw.upstream_failed(SearchResponse(query="q"))  # nothing selectable
    down = [_report(s) for s in ("error", "timeout", "benched", "blocked", "skipped")]
    assert dw.upstream_failed(SearchResponse(query="q", engines=down))
    # "empty" is a real answer: the engine worked and had nothing. Not our fault, not refunded.
    assert not dw.upstream_failed(SearchResponse(query="q", engines=[*down, _report("empty")]))
    assert not dw.upstream_failed(SearchResponse(query="q", engines=[_report("ok")]))
    assert not dw.upstream_failed(SearchResponse(query="q", blocked_query=True))


# ─────────────────────────── entities and evidence ───────────────────────────


def test_entity_extraction():
    good = fake_onion("shop")
    v2 = "3g2upl4pq6kufc4m"
    text = (
        f"Pay 1BoatSLRHtKNngkdXEeobR76b53LETtpyT or bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq, "
        f"ETH 0x52908400098527886E0F7030069857D2E4169EE7, mail ops@leaks.example, "
        f"mirror {good}, old {v2}.onion -----BEGIN PGP PUBLIC KEY BLOCK-----"
    )
    ents = extract_entities("t", text, "http://x")
    assert ents["onion_v3"] == [good]
    assert ents["onion_v2_deprecated"] == [f"{v2}.onion"]
    assert ents["emails"] == ["ops@leaks.example"]
    assert len(ents["btc"]) == 2 and len(ents["eth"]) == 1 and ents["pgp"] is True


def test_a_56_character_lookalike_with_a_bad_checksum_is_not_an_onion_entity():
    good = fake_onion("real")
    forged = good[:6] + ("a" if good[6] != "a" else "b") + good[7:]
    assert extract_entities("", f"visit {forged}", "") == {}
    assert extract_entities("", f"visit {good}", "")["onion_v3"] == [good]


def test_entities_are_capped_and_empty_kinds_dropped():
    spam = " ".join(f"user{i}@x.example" for i in range(50))
    ents = extract_entities("", spam, "")
    assert list(ents) == ["emails"] and len(ents["emails"]) == 10


def test_evidence_hash_is_stable_and_tamper_evident():
    def res(url, title, score):
        return Result(url=url, host="h", title=title, engines=["a"], score=score)

    a = [res("http://a", "A", 0.9), res("http://b", "B", 0.5)]
    assert evidence_hash(a) == evidence_hash([res("http://a", "A", 0.9), res("http://b", "B", 0.5)])
    assert len(evidence_hash(a)) == 64
    for tampered in (
        [res("http://a", "A", 0.9), res("http://b", "B", 0.6)],  # score
        [res("http://a", "A!", 0.9), res("http://b", "B", 0.5)],  # title
        [res("http://b", "B", 0.5), res("http://a", "A", 0.9)],  # order
        a[:1],  # a result removed
    ):
        assert evidence_hash(tampered) != evidence_hash(a)


# ─────────────────────────── redirect guard (gateway mode leaves this server) ───────────────────────────


def test_redirect_rules():
    onion, other = fake_onion("a"), fake_onion("b")
    # onion engine: onion -> onion only
    assert redirect_allowed(f"http://{onion}/s", f"http://{other}/s")
    assert not redirect_allowed(f"http://{onion}/s", "https://tracker.example/x")
    # clearnet gateway: same site only (www twin allowed), never elsewhere or internal
    assert redirect_allowed("https://ahmia.fi/search", "https://ahmia.fi/search/?q=x")
    assert redirect_allowed("https://vormweb.de/a", "https://www.vormweb.de/en/a")
    assert not redirect_allowed("https://ahmia.fi/search", "https://evil.example/")
    assert not redirect_allowed("https://ahmia.fi/search", "http://169.254.169.254/latest/meta-data/")
    assert not redirect_allowed("https://ahmia.fi/search", "http://127.0.0.1:8000/api/v1/admin")
    assert not redirect_allowed("https://ahmia.fi/search", f"http://{onion}/")
    assert not redirect_allowed("https://ahmia.fi/search", "file:///etc/passwd")
    assert not redirect_allowed("https://ahmia.fi/search", "gopher://x/")


@respx.mock
async def test_a_hijacked_gateway_cannot_bounce_us_to_an_internal_address():
    respx.get("https://ahmia.fi/search").respond(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    internal = respx.get("http://169.254.169.254/latest/meta-data/").respond(200, text="secret")
    fetcher = HttpFetcher(Settings(transport="direct", retries=0))
    try:
        with pytest.raises(FetchError) as exc:
            await fetcher.get("https://ahmia.fi/search", engine="ahmia")
    finally:
        await fetcher.aclose()
    assert exc.value.kind == "redirect"
    assert not internal.called, "the request to the internal address was never sent"


# ─────────────────────────── exports are safe to open in a spreadsheet ───────────────────────────


def test_csv_cells_cannot_run_as_formulas():
    from app.services.darkweb.__main__ import csv_safe

    for evil in ('=HYPERLINK("http://evil","x")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"):
        assert csv_safe(evil) == "'" + evil
    assert csv_safe("normal title") == "normal title"
    assert csv_safe(0.82) == 0.82 and csv_safe(3) == 3 and csv_safe(None) == ""


# ─────────────────────────── dead onion services (found against live Tor) ───────────────────────────


@respx.mock
async def test_a_dead_onion_service_is_a_fetch_error_not_a_crash():
    """Tor answers a dead hidden service with SOCKS codes 0xF0-0xF7, which socksio cannot parse
    and raises as ProtocolError('Malformed reply'). That must read as an unreachable service."""
    from socksio.exceptions import ProtocolError

    host = fake_onion("dead-service")
    respx.get(f"http://{host}/search").mock(side_effect=ProtocolError("Malformed reply"))
    fetcher = HttpFetcher(Settings(transport="tor", retries=0))
    try:
        with pytest.raises(FetchError) as exc:
            await fetcher.get(f"http://{host}/search", engine="x")
    finally:
        await fetcher.aclose()
    assert exc.value.kind == "proxy" and "unreachable" in str(exc.value)


@respx.mock
async def test_mirror_failover_survives_a_dead_onion_service():
    from socksio.exceptions import ProtocolError

    from app.services.darkweb.engines import load_catalog

    from .conftest import read_fixture

    tordex = next(e for e in load_catalog() if e.name == "tordex")
    dead, alive = tordex.spec.mirrors[:2]
    respx.get(f"http://{dead}/search").mock(side_effect=ProtocolError("Malformed reply"))
    respx.get(f"http://{alive}/search").respond(200, text=read_fixture("tordex.html"))
    fetcher = HttpFetcher(Settings(transport="tor", retries=0))
    try:
        outcome = await tordex.search(fetcher, "bitcoin", transport="tor")
    finally:
        await fetcher.aclose()
    assert outcome.report.status == "ok" and outcome.results
    assert outcome.report.endpoint == alive, "the second mirror answered after the first was dead"


# ─────────────────────────── what was searched is never logged ───────────────────────────


@respx.mock
async def test_the_search_text_never_reaches_the_logs_even_at_info(caplog):
    """httpx logs each request URL at INFO and our URLs carry the query. Logs persist; searches
    must not. Holds whatever level the host app sets on the root logger."""
    import logging

    caplog.set_level(logging.DEBUG)  # as permissive as a host app can make it
    respx.get("https://ahmia.fi/search/").respond(200, text="<html></html>")
    fetcher = HttpFetcher(Settings(transport="direct", retries=0))
    try:
        await fetcher.get("https://ahmia.fi/search/?q=needle-in-a-haystack-9917", engine="ahmia")
    finally:
        await fetcher.aclose()
    assert "needle-in-a-haystack-9917" not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING

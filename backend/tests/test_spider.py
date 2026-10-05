"""Spider — the correlation / pivoting tool.

What matters here:
  * the engine builds a graph and honours its three caps (nodes / lookups / depth);
  * each pivot module turns a source's answer into the right nodes, and a dead
    source degrades to nothing rather than raising;
  * a scan is paid-plans-only and costs exactly one search, refunded on a fault;
  * a scan is persisted per account, and a *later* scan flags an identifier the
    account already turned up — the whole point of the tool;
  * history is owner-scoped (no reading or deleting another account's scans),
    and deleting the account takes the scans with it.

Runs against a throwaway database; run it on its own, like tests/test_billing.py.
"""

import asyncio
import os
import tempfile

import httpx
import pytest
import respx

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "spider.db")
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"
os.environ["OPERATOR_TOKEN"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.models import LeakHit, LeakSearchResponse  # noqa: E402
from app.services import usage, users  # noqa: E402
from app.services.billing import store as billing_store  # noqa: E402
from app.services.leaks import base as leaks_base  # noqa: E402
from app.services.spider import engine, graph, modules  # noqa: E402
from app.services.spider import store as history  # noqa: E402

# Point every module that captured the settings singleton at the test one.
for _m in (usage, billing_store, engine, modules, history):
    _m.settings = cfg.settings

db._conn = None
db.get_conn()

PW = "a-long-enough-password"
_seq = [0]


def _user(tier="starter", role="user"):
    _seq[0] += 1
    return users.create(f"spider{_seq[0]}@example.test", PW, username=f"sp{_seq[0]}", tier=tier, role=role)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _collect(seed, kind, **kw):
    """Drive the engine to its `done` event and return (graph, stats)."""
    g, stats = {"nodes": [], "edges": []}, None
    async for ev in engine.run_scan_events(seed, kind, **kw):
        if ev["type"] == "done":
            g, stats = ev["graph"], ev["stats"]
    return g, stats


# ─────────────────────────── seed detection + graph basics ───────────────────────────

def test_detect_seed_kind():
    from app.services.spider import detect_seed_kind as d
    assert d("alice@example.com") == "email"
    assert d("example.com") == "domain"
    assert d("jane doe") == "name"
    assert d("alice") == "username"


def test_graph_dedupes_by_canonical_value():
    g = graph.Graph()
    a = g.make("email", "Alice@Example.COM", depth=0, source="seed")
    b = g.make("email", "alice@example.com", depth=1, source="gravatar")
    assert a is b  # same node, case-folded
    assert g.nodes[("email", "alice@example.com")].sources == {"seed", "gravatar"}


# ─────────────────────────── engine caps ───────────────────────────

def test_engine_respects_node_and_depth_caps(monkeypatch):
    """A module that always emits a fresh username must still stop at the caps,
    not run forever."""

    class Fanout(modules.Module):
        key, name, consumes = "fanout", "Fanout", ("username",)

        async def expand(self, client, node):
            return [modules.Finding("username", f"{node.value}x{i}") for i in range(5)]

    monkeypatch.setattr(engine, "enabled_modules", lambda: [Fanout()])
    monkeypatch.setattr(cfg.settings, "spider_max_nodes", 12)
    monkeypatch.setattr(cfg.settings, "spider_lookup_budget", 50)
    monkeypatch.setattr(cfg.settings, "spider_max_depth", 2)

    g, stats = run(_collect("root", "username"))
    assert stats["nodes"] <= 12
    assert stats["truncated"] is True
    # Nothing beyond the depth cap is expanded, so no depth-3 node exists.
    assert max(n["depth"] for n in g["nodes"]) <= 2


def test_engine_lookup_budget_caps_expansions(monkeypatch):
    calls = {"n": 0}

    class Counter(modules.Module):
        key, name, consumes = "counter", "Counter", ("username",)

        async def expand(self, client, node):
            calls["n"] += 1
            return [modules.Finding("username", f"{node.value}-{calls['n']}")]

    monkeypatch.setattr(engine, "enabled_modules", lambda: [Counter()])
    monkeypatch.setattr(cfg.settings, "spider_lookup_budget", 4)
    monkeypatch.setattr(cfg.settings, "spider_max_nodes", 999)
    monkeypatch.setattr(cfg.settings, "spider_max_depth", 9)

    _, stats = run(_collect("seed", "username"))
    assert stats["lookups"] == 4
    assert calls["n"] == 4


def test_module_that_raises_is_swallowed(monkeypatch):
    class Boom(modules.Module):
        key, name, consumes = "boom", "Boom", ("email",)

        async def expand(self, client, node):
            raise RuntimeError("source on fire")

    monkeypatch.setattr(engine, "enabled_modules", lambda: [Boom()])
    g, stats = run(_collect("a@b.com", "email"))
    # The seed survives; the scan did not crash.
    assert stats["nodes"] == 1 and g["nodes"][0]["type"] == "email"


# ─────────────────────────── modules ───────────────────────────

def test_leaks_module_reuses_the_aggregator(monkeypatch):
    async def fake_search(query, kind="auto", reveal=False):
        assert reveal is True  # the spider asks for the real values
        return LeakSearchResponse(
            query=query, kind="email", total=1, masked=False,
            sources=[], hits=[LeakHit(
                source="x", source_label="X", breach="BigLeak",
                email=query, username="aliashandle", password="hunter2",
                first_name="jane", last_name="doe",
            )],
        )

    import app.services.leaks as leaks_pkg
    monkeypatch.setattr(leaks_pkg, "search_leaks", fake_search)

    out = run(modules.LeaksModule().expand(None, graph.Node("email", "a@b.com")))
    types = {f.type for f in out}
    assert {"breach", "username", "domain", "name", "password"} <= types
    assert any(f.type == "username" and f.value == "aliashandle" for f in out)
    assert any(f.type == "domain" and f.value == "b.com" for f in out)


@respx.mock
def test_gravatar_module_parses_profile():
    import hashlib
    digest = hashlib.md5(b"alice@example.com").hexdigest()
    respx.get(f"https://gravatar.com/{digest}.json").mock(return_value=httpx.Response(200, json={
        "entry": [{
            "displayName": "Alice A", "preferredUsername": "aliceq",
            "name": {"formatted": "Alice Anderson"},
            "accounts": [{"shortname": "github", "username": "alicecodes", "url": "https://github.com/alicecodes"}],
        }],
    }))

    async def go():
        async with httpx.AsyncClient() as c:
            return await modules.GravatarModule().expand(c, graph.Node("email", "alice@example.com"))

    out = run(go())
    assert any(f.type == "name" and "alice anderson" in f.value.lower() for f in out)
    assert any(f.type == "username" and f.value == "aliceq" for f in out)
    assert any(f.type == "account" and "github.com/alicecodes" in f.value for f in out)


@respx.mock
def test_gravatar_404_is_empty_not_error():
    import hashlib
    digest = hashlib.md5(b"nobody@example.com").hexdigest()
    respx.get(f"https://gravatar.com/{digest}.json").mock(return_value=httpx.Response(404))

    async def go():
        async with httpx.AsyncClient() as c:
            return await modules.GravatarModule().expand(c, graph.Node("email", "nobody@example.com"))

    assert run(go()) == []


@respx.mock
def test_domain_module_dns_and_crtsh():
    respx.get("https://dns.google/resolve", params={"name": "example.com", "type": "MX"}).mock(
        return_value=httpx.Response(200, json={"Answer": [{"data": "10 mail.example.com."}]}))
    respx.get("https://dns.google/resolve", params={"name": "example.com", "type": "NS"}).mock(
        return_value=httpx.Response(200, json={"Answer": [{"data": "ns1.hoster.net."}]}))
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(200, json=[
        {"name_value": "www.example.com\n*.api.example.com"},
    ]))
    respx.get("https://rdap.org/domain/example.com").mock(return_value=httpx.Response(200, json={
        "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Acme Registrar"]]]}],
        "events": [{"eventAction": "registration", "eventDate": "2001-03-01T00:00:00Z"}],
    }))

    node = graph.Node("domain", "example.com")

    async def go():
        async with httpx.AsyncClient() as c:
            return await modules.DomainModule().expand(c, node)

    out = run(go())
    vals = {f.value for f in out}
    assert "mail.example.com" in vals     # MX target, priority stripped
    assert "ns1.hoster.net" in vals       # NS target
    assert "api.example.com" in vals      # crt.sh subdomain, wildcard stripped
    assert node.detail and "Acme Registrar" in node.detail


@respx.mock
def test_username_sites_detects_hit_and_miss():
    # Tiny two-site list, matched by status code.
    sites = {"sites": [
        {"name": "HitSite", "url": "https://hit.test/{}", "code": 200},
        {"name": "MissSite", "url": "https://miss.test/{}", "code": 200},
    ]}
    modules._SITES = sites["sites"]
    respx.get("https://hit.test/bob").mock(return_value=httpx.Response(200, text="bob"))
    respx.get("https://miss.test/bob").mock(return_value=httpx.Response(404))

    async def go():
        async with httpx.AsyncClient() as c:
            return await modules.UsernameSitesModule().expand(c, graph.Node("username", "bob"))

    out = run(go())
    modules._SITES = None  # reset the cache for other tests
    assert [f for f in out if "hit.test" in f.value]
    assert not [f for f in out if "miss.test" in f.value]


# ─────────────────────────── history + correlation ───────────────────────────

def test_seen_before_links_scans_for_the_same_account():
    u = _user()
    g1 = {"nodes": [
        {"type": "email", "value": "a@b.com", "label": "a@b.com"},
        {"type": "username", "value": "shared", "label": "shared"},
    ], "edges": []}
    first = history.save(u["id"], seed="a@b.com", seed_kind="email", modules=["leaks"], graph=g1)

    # A later scan that re-encounters "shared".
    g2 = {"nodes": [{"type": "username", "value": "shared", "label": "shared"}], "edges": []}
    seen = history.seen_before(u["id"], g2["nodes"])
    assert "username:shared" in seen
    assert seen["username:shared"][0]["scan_id"] == first
    # A brand-new value has no history.
    assert history.seen_before(u["id"], [{"type": "email", "value": "new@x.com"}]) == {}


def test_history_is_scoped_to_the_owner():
    a, b = _user(), _user()
    sid = history.save(a["id"], seed="a@b.com", seed_kind="email", modules=[], graph={"nodes": [], "edges": []})
    assert history.get(a["id"], sid) is not None
    assert history.get(b["id"], sid) is None          # not b's to read
    assert history.delete(b["id"], sid) is False      # nor to delete
    assert history.delete(a["id"], sid) is True
    assert history.get(a["id"], sid) is None


def test_history_trims_to_the_cap(monkeypatch):
    monkeypatch.setattr(cfg.settings, "spider_history_max", 3)
    u = _user()
    ids = [history.save(u["id"], seed=f"s{i}", seed_kind="username", modules=[],
                        graph={"nodes": [], "edges": []}) for i in range(5)]
    kept = {s["id"] for s in history.list_scans(u["id"])}
    assert len(kept) == 3
    assert ids[0] not in kept and ids[-1] in kept  # oldest dropped, newest kept


# ─────────────────────────── API: metering + gates ───────────────────────────

@pytest.fixture()
def client():
    # A minimal app with just the spider router (like tests/darkweb/test_router)
    # so the background scan task runs on a persistent portal loop, and real
    # cookie auth + metering still apply. The `with` form keeps that loop alive
    # between requests, which is what lets the polled job make progress.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import spider as spider_router

    app = FastAPI()
    app.include_router(spider_router.router, prefix="/api/v1")
    with TestClient(app) as c:
        yield c


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def _stub_single_leak(monkeypatch, username="pivotfriend"):
    async def fake_search(query, kind="auto", reveal=False):
        return LeakSearchResponse(
            query=query, kind=kind if kind != "auto" else "email", total=1, masked=False,
            sources=[], hits=[LeakHit(source="x", source_label="X", breach="Leak1",
                                      email="a@b.com", username=username)],
        )
    import app.services.leaks as leaks_pkg
    monkeypatch.setattr(leaks_pkg, "search_leaks", fake_search)
    # Keep the API scan network-free and deterministic: leaks only.
    monkeypatch.setattr(cfg.settings, "spider_modules", "leaks")


def test_free_tier_is_refused_before_charging(client, monkeypatch):
    _stub_single_leak(monkeypatch)
    u = _user(tier="free")
    before = usage.status(u)["used"]
    r = _signed_in(client, u).post("/api/v1/spider/scan", json={"seed": "a@b.com"})
    assert r.status_code == 403
    assert r.headers.get("X-Upgrade-Path") == "/pricing"
    assert usage.status(u)["used"] == before  # not charged


def test_paid_scan_charges_one_search_and_correlates(client, monkeypatch):
    _stub_single_leak(monkeypatch)
    u = _user(tier="starter")
    c = _signed_in(client, u)

    before = usage.status(u)["used"]
    first = _run_scan(c, "a@b.com")
    assert usage.status(u)["used"] == before + 1      # exactly one search for a whole scan
    assert first["stats"]["nodes"] >= 2               # seed + at least the pivot username
    # Nothing is flagged on the first encounter.
    assert all(not n["seen_before"] for n in first["graph"]["nodes"])

    # A second scan that re-finds the same username must flag it as seen before.
    second = _run_scan(c, "a@b.com")
    flagged = [n for n in second["graph"]["nodes"] if n["seen_before"]]
    assert any(n["value"] == "pivotfriend" for n in flagged)
    assert flagged[0]["seen_before"][0]["seed"] == "a@b.com"


def test_scan_history_endpoints_and_ownership(client, monkeypatch):
    _stub_single_leak(monkeypatch)
    owner = _user(tier="starter")
    other = _user(tier="starter")
    co = _signed_in(client, owner)
    _run_scan(co, "a@b.com")

    hist = co.get("/api/v1/spider/history").json()
    assert len(hist) >= 1
    sid = hist[0]["id"]
    assert co.get(f"/api/v1/spider/history/{sid}").status_code == 200

    # Another account cannot see or delete it. (One client, one cookie jar, so
    # signing in `other` replaces `owner`'s session — re-auth as owner after.)
    cother = _signed_in(client, other)
    assert cother.get(f"/api/v1/spider/history/{sid}").status_code == 404
    assert cother.delete(f"/api/v1/spider/history/{sid}").status_code == 404

    co = _signed_in(client, owner)
    assert co.delete(f"/api/v1/spider/history/{sid}").status_code == 200
    assert co.get(f"/api/v1/spider/history/{sid}").status_code == 404


def test_account_delete_removes_spider_rows(client, monkeypatch):
    _stub_single_leak(monkeypatch)
    u = _user(tier="starter")
    _run_scan(_signed_in(client, u), "a@b.com")
    assert db.query("SELECT 1 FROM spider_scans WHERE user_id = ?", (u["id"],))
    users.delete(u["id"])
    assert db.query("SELECT 1 FROM spider_scans WHERE user_id = ?", (u["id"],)) == []
    assert db.query("SELECT 1 FROM spider_nodes WHERE user_id = ?", (u["id"],)) == []


def _run_scan(client, seed) -> dict:
    """POST a scan and poll the job to completion."""
    import time
    r = client.post("/api/v1/spider/scan", json={"seed": seed})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/v1/spider/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            assert job["status"] == "done", job.get("error")
            return job
        time.sleep(0.05)
    raise AssertionError("scan did not finish")

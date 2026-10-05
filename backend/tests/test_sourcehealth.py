"""Source health and the admin health report.

What matters: every call to an outside source is scored, a failure keeps a
reason that never carries the query, a source reads idle → ok → degraded →
down and recovers on one success, the leak aggregator scores only the
providers it actually asked, the report and the probe are for admins only,
and the probe can't be used to hammer the sources.
"""

import asyncio
import json
import os
import tempfile

import httpx
import pytest
import respx

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "sourcehealth.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"
os.environ["IPLOOKUP_AUTO_UPDATE"] = "false"
os.environ["IPLOOKUP_ABUSEIPDB_KEY"] = ""
os.environ["LEAKS_DB"] = os.path.join(tempfile.mkdtemp(), "leak_datasets.db")
os.environ["PHONE_VRI_API_KEY"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import healthcheck, sourcehealth, users  # noqa: E402
from app.services.leaks import service as leaks_service  # noqa: E402

healthcheck.settings = cfg.settings
leaks_service.settings = cfg.settings


@pytest.fixture(autouse=True)
def clean():
    sourcehealth.reset()
    healthcheck._probe.update(at=0.0, probed=[])


def stat(tool, key):
    return {(s["tool"], s["key"]): s for s in sourcehealth.snapshot()}[(tool, key)]


# ─────────────────────────── the tally ───────────────────────────

def test_a_source_goes_idle_ok_degraded_down_and_back():
    sourcehealth.register("t", "src", "A source")
    assert stat("t", "src")["state"] == "idle"
    sourcehealth.record("t", "src", "A source", True, latency_ms=120)
    assert stat("t", "src")["state"] == "ok" and stat("t", "src")["latency_ms"] == 120
    sourcehealth.record("t", "src", "A source", False, error="HTTP 502")
    assert stat("t", "src")["state"] == "degraded"
    for _ in range(sourcehealth.DOWN_AFTER - 1):
        sourcehealth.record("t", "src", "A source", False, error="HTTP 502")
    s = stat("t", "src")
    assert s["state"] == "down" and s["failed"] == sourcehealth.DOWN_AFTER and s["last_error"] == "HTTP 502"
    sourcehealth.record("t", "src", "A source", True)
    assert stat("t", "src")["state"] == "ok"


def test_a_down_source_rests_then_gets_another_try(monkeypatch):
    for _ in range(sourcehealth.DOWN_AFTER):
        sourcehealth.record("t", "rest", "R", False)
    assert sourcehealth.resting("t", "rest", 600)
    import time as _t
    later = _t.time() + 601
    monkeypatch.setattr(sourcehealth.time, "time", lambda: later)
    assert not sourcehealth.resting("t", "rest", 600)
    assert not sourcehealth.resting("t", "never-seen", 600)


@pytest.mark.parametrize("error,leaked", [
    ("refused alice@example.com", "alice@example.com"),
    ("bad hash 5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8", "5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8"),
    ("Invalid number +1 (336) 408-6644", "408-6644"),
    ("no record for 203.0.113.9", "203.0.113.9"),
])
def test_an_error_never_keeps_the_query(error, leaked):
    sourcehealth.record("t", "scrub", "S", False, error=error)
    assert leaked not in stat("t", "scrub")["last_error"]


def test_tracked_keeps_safe_messages_and_only_the_type_of_others():
    class Mine(Exception):
        pass

    @sourcehealth.tracked("t", "dec", "Decorated", safe=(Mine,))
    async def call(exc):
        if exc:
            raise exc
        return 42

    assert asyncio.run(call(None)) == 42
    with pytest.raises(Mine):
        asyncio.run(call(Mine("RDAP: HTTP 503")))
    assert stat("t", "dec")["last_error"] == "RDAP: HTTP 503"
    url_err = httpx.ConnectError("failed to reach https://x.example/?q=secret-query")
    with pytest.raises(httpx.ConnectError):
        asyncio.run(call(url_err))
    s = stat("t", "dec")
    assert s["last_error"] == "ConnectError" and s["ok"] == 1 and s["failed"] == 2


def test_tracked_answered_counts_as_working():
    class NoRecord(Exception):
        pass

    @sourcehealth.tracked("t", "ans", "Answered", answered=lambda e: isinstance(e, NoRecord))
    async def call():
        raise NoRecord("404")

    with pytest.raises(NoRecord):
        asyncio.run(call())
    assert stat("t", "ans")["ok"] == 1 and stat("t", "ans")["failed"] == 0


@respx.mock
def test_the_leak_aggregator_scores_only_providers_it_asked():
    leaks_service._cache.clear()
    respx.get(url__startswith="https://api.xposedornot.com").mock(return_value=httpx.Response(500))
    respx.get(url__startswith="https://api.proxynova.com").mock(
        return_value=httpx.Response(200, json={"count": 0, "lines": []}))
    respx.get(url__startswith="https://leakcheck.io").mock(
        return_value=httpx.Response(200, json={"success": False, "error": "Not found"}))
    asyncio.run(leaks_service.search_leaks("someone@example.com", kind="email"))
    assert stat("leaks", "xposedornot")["failed"] == 1
    assert stat("leaks", "proxynova")["ok"] == 1
    # The HIBP catalogue only takes domains: not asked, so not scored.
    hibp = stat("leaks", "hibp_catalog")
    assert hibp["ok"] == 0 and hibp["failed"] == 0 and hibp["state"] == "idle"


# ─────────────────────────── the admin endpoints ───────────────────────────

_seq = [0]


def _user(role="user"):
    _seq[0] += 1
    db.get_conn()
    return users.create(f"health{_seq[0]}@example.test", "a-long-enough-password", role=role)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def test_health_is_for_admins_only(client):
    assert client.get("/api/v1/admin/health").status_code == 401
    assert client.post("/api/v1/admin/health/probe").status_code == 401
    c = _signed_in(client, _user())
    assert c.get("/api/v1/admin/health").status_code == 403
    assert c.post("/api/v1/admin/health/probe").status_code == 403


def test_the_report(client):
    sourcehealth.record("ip", "rdap", "RDAP (rdap.org)", False, error="RDAP: HTTP 503")
    r = _signed_in(client, _user("admin")).get("/api/v1/admin/health")
    assert r.status_code == 200, r.text
    body = r.json()
    checks = {c["key"]: c for c in body["checks"]}
    assert {"tor", "database", "disk", "jobs", "ipdb", "abuseipdb", "veriroute"} <= set(checks)
    assert checks["database"]["state"] == "ok"
    assert checks["abuseipdb"]["state"] == "off" and checks["veriroute"]["state"] == "off"
    rdap = next(s for s in body["sources"] if (s["tool"], s["key"]) == ("ip", "rdap"))
    assert rdap["state"] == "degraded" and rdap["last_error"] == "RDAP: HTTP 503"
    assert rdap["last_failure"].endswith("+00:00")
    assert body["uptime_s"] >= 0 and body["probed_at"] is None


def test_the_public_health_says_nothing_about_sources(client):
    sourcehealth.record("ip", "rdap", "RDAP (rdap.org)", False, error="RDAP: HTTP 503")
    body = client.get("/api/v1/health").json()
    assert "sources" not in body and "RDAP" not in json.dumps(body)


def test_the_probe_runs_at_most_once_a_minute(client, monkeypatch):
    ran = []

    async def canary(tool, key):
        ran.append(key)
        sourcehealth.record(tool, key, key, True, latency_ms=5)

    monkeypatch.setattr(healthcheck, "_probes",
                        lambda client: [("leaks", "xposedornot", canary("leaks", "xposedornot")),
                                        ("ip", "rdap", canary("ip", "rdap"))])
    c = _signed_in(client, _user("admin"))
    first = c.post("/api/v1/admin/health/probe").json()
    second = c.post("/api/v1/admin/health/probe").json()
    assert ran == ["xposedornot", "rdap"]  # the second asked nothing
    assert first["probed"] == ["leaks.xposedornot", "ip.rdap"] and second["probed_at"] == first["probed_at"]
    assert stat("ip", "rdap")["state"] == "ok"


def test_the_probe_uses_canaries_not_customer_queries(monkeypatch):
    monkeypatch.setattr(healthcheck.settings, "domain_wayback", True)

    async def go():
        async with httpx.AsyncClient() as c:
            probes = healthcheck._probes(c)
            for _, _, coro in probes:
                coro.close()  # built, not run
            return probes

    keys = {f"{t}.{k}" for t, k, _ in asyncio.run(go())}
    assert {"leaks.xposedornot", "passwords.leakedpassword", "passwords.hibp_range", "ip.rdap",
            "domain.doh_google", "domain.rdap", "domain.crtsh", "domain.wayback"} <= keys
    # No key, no paid source: AbuseIPDB isn't probed without one.
    assert "ip.abuseipdb" not in keys

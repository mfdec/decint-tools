"""The /darkweb HTTP surface: charging, refunds, ownership, live progress, failure messages."""

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.darkweb as dw
from app.auth import require_session
from app.routers import darkweb as router_mod
from app.services import usage
from app.services import users as users_svc
from app.services.darkweb.models import SearchResponse
from app.services.darkweb.pipeline import safety

USER = {"id": 1, "email": "a@example.test", "role": "user", "tier": "pro"}
OTHER = {"id": 2, "email": "b@example.test", "role": "user", "tier": "pro"}
ADMIN = {"id": 9, "email": "root@example.test", "role": "admin", "tier": "enterprise"}


@pytest.fixture
def ctx(monkeypatch, replay):
    """A client plus the ledgers of what was charged, refunded and audited."""
    ledger = {"taken": [], "refunded": [], "audited": [], "user": USER}
    monkeypatch.setattr(usage, "take", lambda user: ledger["taken"].append(user["id"]) or {})
    monkeypatch.setattr(usage, "refund", lambda user: ledger["refunded"].append(user["id"]))
    monkeypatch.setattr(users_svc, "audit", lambda action, **kw: ledger["audited"].append((action, kw)))
    monkeypatch.setattr(dw, "_gate", asyncio.Semaphore(3))

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[require_session] = lambda: ledger["user"]
    with TestClient(app) as client:
        ledger["client"] = client
        yield ledger


def start(ctx, **body):
    body.setdefault("query", "bitcoin")
    return ctx["client"].post("/api/v1/darkweb/search", json=body)


def settle(ctx, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = ctx["client"].get(f"/api/v1/darkweb/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job never settled: {job}")


def run(ctx, **body):
    r = start(ctx, **body)
    assert r.status_code == 200, r.text
    return settle(ctx, r.json()["job_id"])


# ─────────────────────────── the happy paths ───────────────────────────


def test_gateway_search_returns_ranked_results_with_engine_reports(ctx):
    job = run(ctx, mode="gateway")
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["transport"] == "replay"
    assert job["engines_planned"] == 5 and len(job["engines"]) == 5
    assert {e["status"] for e in job["engines"]} == {"ok"}
    assert job["stats"]["engines_with_results"] == 5
    top = job["results"][0]
    assert top["host"].endswith(".onion") and top["score"] > 0 and top["engines"]
    assert top["corroboration"] >= 2
    scores = [r["score"] for r in job["results"]]
    assert scores == sorted(scores, reverse=True)
    assert "sha256" not in job["manifest"] and all(not r["entities"] for r in job["results"])
    assert ctx["taken"] == [1] and ctx["refunded"] == []


def test_tor_search_carries_the_pro_extras(ctx):
    job = run(ctx, mode="tor")
    assert job["status"] == "done" and job["engines_planned"] == 11
    assert len(job["manifest"]["sha256"]) == 64
    assert any(r["entities"] for r in job["results"])
    assert job["stats"]["pruned_by_reason"]["sponsored"] == 4
    assert job["pruned"], "dropped results stay visible, with a reason"
    assert {"url", "title", "engines", "reason"} <= set(job["pruned"][0])


def test_operators_are_applied_and_echoed(ctx):
    job = run(ctx, mode="tor", query='"bitcoin core" -doubler')
    assert job["operators"] == {"phrases": ["bitcoin core"], "excluded": ["doubler"]}
    assert job["results"] and all("bitcoin core" in (r["title"] + r["snippet"]).lower() for r in job["results"])


def test_the_old_ahmia_mode_still_works_as_gateway(ctx):
    job = run(ctx, mode="ahmia", verify=False)  # a pre-overhaul client, extra field and all
    assert job["mode"] == "gateway" and job["status"] == "done"


def test_limit_is_honoured(ctx):
    assert len(run(ctx, mode="tor", limit=2)["results"]) == 2


def test_engine_roster_endpoint(ctx):
    r = ctx["client"].get("/api/v1/darkweb/engines?mode=gateway")
    assert r.status_code == 200
    assert {e["name"] for e in r.json()} == {"ahmia", "onionland", "vormweb", "onionsearchengine", "onionengine"}
    assert ctx["client"].get("/api/v1/darkweb/engines?mode=bogus").status_code == 422


# ─────────────────────────── validate first, then charge ───────────────────────────


@pytest.mark.parametrize("body", [
    {"query": ""}, {"query": "x" * 301}, {"mode": "bogus"}, {"limit": 0}, {"limit": 51}, {"pages": 0},
])
def test_malformed_requests_are_rejected_and_free(ctx, body):
    assert start(ctx, **body).status_code == 422
    assert ctx["taken"] == []


def test_a_query_of_only_exclusions_is_refused_and_free(ctx):
    r = start(ctx, query="-doubler -scam")
    assert r.status_code == 422 and "at least one search word" in r.json()["detail"]
    assert ctx["taken"] == [] and ctx["audited"] == []


def test_blocked_queries_are_refused_free_and_audited_without_the_text(ctx, monkeypatch):
    monkeypatch.setattr(safety, "_TEST_EXTRA_TERMS", ["zzblockedplaceholderzz"])
    r = start(ctx, query='nice -words "zzblockedplaceholderzz"')
    assert r.status_code == 422 and "child sexual abuse material" in r.json()["detail"]
    assert ctx["taken"] == [] and ctx["refunded"] == []
    assert [a for a, _ in ctx["audited"]] == ["darkweb.blocked"]
    assert "zzblockedplaceholderzz" not in repr(ctx["audited"]), "the audit trail never stores what was searched"


# ─────────────────────────── jobs belong to whoever started them ───────────────────────────


def test_a_job_is_private_to_its_owner_and_visible_to_admins(ctx):
    job_id = start(ctx).json()["job_id"]
    settle(ctx, job_id)
    ctx["user"] = OTHER
    assert ctx["client"].get(f"/api/v1/darkweb/jobs/{job_id}").status_code == 404
    ctx["user"] = ADMIN
    assert ctx["client"].get(f"/api/v1/darkweb/jobs/{job_id}").status_code == 200
    ctx["user"] = USER
    assert ctx["client"].get(f"/api/v1/darkweb/jobs/{job_id}").status_code == 200
    assert ctx["client"].get("/api/v1/darkweb/jobs/doesnotexist").status_code == 404


# ─────────────────────────── when our side fails, the search is given back ───────────────────────────


def test_no_engine_answering_refunds_and_hides_internals_from_customers(ctx, monkeypatch, tmp_path):
    monkeypatch.setattr(dw.app_settings, "darkweb_replay_dir", str(tmp_path))  # empty: no engine has a page
    job = run(ctx, mode="tor")
    assert job["status"] == "error"
    assert "Tor search is unavailable" in job["error"] and "socks" not in job["error"].lower()
    assert ctx["taken"] == [1] and ctx["refunded"] == [1]


def test_a_crash_refunds_and_staff_alone_see_the_exception(ctx, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("parser exploded at /srv/secret/path")
        yield

    monkeypatch.setattr(dw, "search_events", boom)
    job = run(ctx)
    assert job["status"] == "error" and job["error"] == "The search failed unexpectedly. Try again."
    assert "secret" not in job["error"]
    assert ctx["refunded"] == [1]

    ctx["user"] = ADMIN
    job = run(ctx)
    assert "RuntimeError: parser exploded" in job["error"]


def test_a_hung_search_is_stopped_and_refunded(ctx, monkeypatch):
    async def hang(*a, **kw):
        await asyncio.sleep(30)
        yield {}

    monkeypatch.setattr(dw, "search_events", hang)
    monkeypatch.setattr(router_mod.settings, "darkweb_deadline", -44.8)  # -> a 0.2 s cap
    job = run(ctx)
    assert job["status"] == "error" and "took too long" in job["error"]
    assert ctx["refunded"] == [1]


# ─────────────────────────── load: searches wait their turn ───────────────────────────


def test_searches_beyond_the_slot_limit_queue_instead_of_piling_on(ctx, monkeypatch):
    monkeypatch.setattr(dw, "_gate", asyncio.Semaphore(1))

    async def slow(query, mode, **kw):
        await asyncio.sleep(0.6)
        yield {"type": "start", "engines": [], "transport": "replay"}
        yield {"type": "results", "response": SearchResponse(query=query), "manifest": {}}

    monkeypatch.setattr(dw, "search_events", slow)
    first = start(ctx).json()["job_id"]
    time.sleep(0.15)
    second = start(ctx).json()["job_id"]
    time.sleep(0.15)
    j1 = ctx["client"].get(f"/api/v1/darkweb/jobs/{first}").json()
    j2 = ctx["client"].get(f"/api/v1/darkweb/jobs/{second}").json()
    assert j1["status"] == "running"
    assert j2["status"] == "queued" and "waiting for a free search slot" in j2["message"]
    assert settle(ctx, first)["status"] == settle(ctx, second)["status"] == "error"  # nothing answered -> refund path

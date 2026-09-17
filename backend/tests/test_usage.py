"""Search metering: three free searches, then the pricing page.

What matters: the fourth free search is refused with a 402 that points at
pricing, a paid plan starts a fresh monthly counter, nobody with a role is
metered, our own faults do not spend a search, and nothing about *what* was
searched for is ever written down.
"""

import os
import tempfile
from datetime import datetime, timezone

import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "usage.db")
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.models import LeakSearchResponse  # noqa: E402
from app.services import usage, users  # noqa: E402
from app.services.billing import plans, store  # noqa: E402

for mod in (store,):
    mod.settings = cfg.settings

_seq = [0]


def _user(tier="free", role="user"):
    _seq[0] += 1
    db.get_conn()
    return users.create(
        f"meter{_seq[0]}@example.test", "a-long-enough-password", tier=tier, role=role
    )


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


# ─────────────────────────── the counter ───────────────────────────

def test_free_tier_is_three_searches_ever():
    assert plans.FREE_PLAN.quota == 3
    assert plans.FREE_PLAN.quota_window == usage.LIFETIME
    u = _user("free")
    for n in (1, 2, 3):
        state = usage.consume(u)
        assert state["used"] == n and state["remaining"] == 3 - n
    with pytest.raises(usage.QuotaExceeded) as exc:
        usage.consume(u)
    assert exc.value.state["used"] == 3 and exc.value.state["remaining"] == 0
    assert exc.value.state["is_free"] is True and exc.value.state["resets_at"] is None


def test_free_searches_never_reset():
    u = _user("free")
    for _ in range(3):
        usage.consume(u)
    # Whatever month it is, "all" is the window: there is no next month.
    assert usage._window(plans.FREE_PLAN, datetime(2027, 1, 1, tzinfo=timezone.utc)) == "all"
    with pytest.raises(usage.QuotaExceeded):
        usage.consume(u)


def test_paid_tier_gets_a_fresh_monthly_counter_on_upgrade():
    u = _user("free")
    for _ in range(3):
        usage.consume(u)
    store.grant(u["id"], "starter", "manual", expires_at=None)
    u = users.get(u["id"])
    state = usage.consume(u)
    assert state["used"] == 1 and state["limit"] == 500
    assert state["window"] == "monthly" and state["resets_at"].startswith(
        usage._resets_at(plans.get("starter"))[:7]
    )


def test_lapsing_back_to_free_finds_the_trial_already_spent():
    u = _user("free")
    for _ in range(3):
        usage.consume(u)
    store.grant(u["id"], "pro", "manual", expires_at=None)
    usage.consume(users.get(u["id"]))
    store.revoke(u["id"], "manual")
    with pytest.raises(usage.QuotaExceeded):
        usage.consume(users.get(u["id"]))


def test_monthly_window_and_reset_date():
    now = datetime(2026, 12, 15, 10, 0, tzinfo=timezone.utc)
    pro = plans.get("pro")
    assert usage._window(pro, now) == "2026-12"
    assert usage._resets_at(pro, now) == "2027-01-01T00:00:00+00:00"
    assert usage._resets_at(pro, datetime(2026, 2, 3, tzinfo=timezone.utc)) == "2026-03-01T00:00:00+00:00"


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_are_never_metered(role):
    u = _user("free", role=role)
    for _ in range(10):
        assert usage.consume(u)["limit"] is None
    assert usage.status(u)["remaining"] is None


def test_unmetered_tier_is_never_metered():
    u = _user("enterprise")
    for _ in range(10):
        usage.consume(u)
    assert usage.status(u)["limit"] is None


def test_refund_gives_the_search_back():
    u = _user("free")
    usage.consume(u)
    usage.consume(u)
    usage.refund(u)
    assert usage.status(u)["used"] == 1
    usage.refund(u)
    usage.refund(u)  # never below zero
    assert usage.status(u)["used"] == 0


def test_last_slot_cannot_be_taken_twice():
    # The check and the increment are one statement; a row already at the
    # limit is refused rather than pushed past it.
    u = _user("free")
    db.execute(
        "INSERT INTO usage_counters (user_id, window, count, updated_at) VALUES (?,?,?,?)",
        (u["id"], "all", 3, "now"),
    )
    with pytest.raises(usage.QuotaExceeded):
        usage.consume(u)
    assert usage.status(u)["used"] == 3


def test_admin_reset_clears_the_meter():
    u = _user("free")
    for _ in range(3):
        usage.consume(u)
    usage.reset(u["id"])
    assert usage.status(u)["used"] == 0


def test_nothing_about_the_query_is_stored():
    cols = {r["name"] for r in db.query("PRAGMA table_info(usage_counters)")}
    assert cols == {"user_id", "window", "count", "updated_at"}


# ─────────────────────────── through the API ───────────────────────────

def _stub_leaks(monkeypatch, fail=False):
    from app.routers import leaks as leaks_router

    async def fake(query, kind="auto", reveal=False):
        if fail:
            raise RuntimeError("aggregator down")
        return LeakSearchResponse(query=query, kind="auto", total=0, masked=True, sources=[], hits=[])

    monkeypatch.setattr(leaks_router, "search_leaks", fake)


def test_fourth_free_search_is_a_402_that_points_at_pricing(client, monkeypatch):
    _stub_leaks(monkeypatch)
    u = _user("free")
    c = _signed_in(client, u)
    for _ in range(3):
        assert c.get("/api/v1/leaks/search", params={"query": "someone@example.test"}).status_code == 200
    r = c.get("/api/v1/leaks/search", params={"query": "someone@example.test"})
    assert r.status_code == 402
    assert "3 free searches" in r.json()["detail"]
    assert r.headers["X-Upgrade-Path"] == "/pricing"
    assert r.headers["X-Quota-Used"] == "3" and r.headers["X-Quota-Limit"] == "3"


def test_invalid_input_is_not_charged(client, monkeypatch):
    _stub_leaks(monkeypatch)
    u = _user("free")
    c = _signed_in(client, u)
    assert c.get("/api/v1/leaks/search", params={"query": "x"}).status_code == 422   # too short
    assert c.get("/api/v1/discord/user/notanid").status_code == 400
    assert usage.status(u)["used"] == 0


def test_our_own_failure_is_not_charged(client, monkeypatch):
    _stub_leaks(monkeypatch, fail=True)
    u = _user("free")
    c = _signed_in(client, u)
    # The test client re-raises server-side exceptions rather than rendering
    # the 500; what matters is the counter afterwards.
    with pytest.raises(RuntimeError):
        c.get("/api/v1/leaks/search", params={"query": "someone@example.test"})
    assert usage.status(u)["used"] == 0


def test_dark_web_job_is_charged_at_submission_and_refunded_on_error(client, monkeypatch):
    from app.routers import darkweb as dw_router

    def boom(*a, **k):
        raise RuntimeError("no tor")

    monkeypatch.setattr(dw_router.darkweb, "run_ahmia", boom)
    u = _user("free")
    c = _signed_in(client, u)
    r = c.post("/api/v1/darkweb/search", json={"query": "example", "mode": "ahmia"})
    assert r.status_code == 200
    # The background task ran to its error inside the test client's loop.
    job = c.get(f"/api/v1/darkweb/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "error"
    assert usage.status(u)["used"] == 0


def test_billing_summary_carries_the_meter(client, monkeypatch):
    _stub_leaks(monkeypatch)
    u = _user("free")
    c = _signed_in(client, u)
    c.get("/api/v1/leaks/search", params={"query": "someone@example.test"})
    me = c.get("/api/v1/billing/me").json()
    assert me["usage"] == {
        "used": 1, "limit": 3, "remaining": 2, "window": "lifetime",
        "resets_at": None, "tier": "free", "is_free": True,
    }
    assert me["quota_window"] == "lifetime"


def test_snowflake_decode_is_free(client):
    u = _user("free")
    c = _signed_in(client, u)
    for _ in range(5):
        assert c.get("/api/v1/discord/snowflake/175928847299117063").status_code == 200
    assert usage.status(u)["used"] == 0

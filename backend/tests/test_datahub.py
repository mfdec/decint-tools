"""Admin ▸ Data: the dataset catalogue, rollups and CSV export.

What matters: every dataset builds on an empty database and on a populated one,
the daily rollup is idempotent and never overwrites a finished past day with a
partial one, nothing credential-like reaches a payload, CSV cells can't run as
spreadsheet formulas, and only admins can read any of it.

Run this module on its own — it points ANALYTICS_DB at a temp file *before*
importing the app, which is the only thing keeping it off the live database.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "datahub.db")
os.environ["LEAKS_DB"] = os.path.join(tempfile.mkdtemp(), "leaks.db")
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_FROM"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import datahub, users  # noqa: E402
from app.services.datahub import Params  # noqa: E402

_seq = [0]


def _iso(delta_days: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=delta_days)).isoformat(timespec="seconds")


def _user(role="user", tier="free"):
    _seq[0] += 1
    db.get_conn()
    return users.create(f"data{_seq[0]}@example.test", "a-long-enough-password", role=role, tier=tier)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _as(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


# ─────────────────────────── datasets ───────────────────────────

def test_every_dataset_builds_on_an_empty_database():
    db.get_conn()
    for d in datahub.DATASETS:
        out = datahub.build(d.id, Params(days=7))
        assert out["id"] == d.id
        for key in ("kpis", "charts", "tables", "notes", "generated_at"):
            assert key in out, (d.id, key)


def test_unknown_dataset_is_none():
    assert datahub.build("nope", Params()) is None


def _seed():
    admin = _user(role="admin", tier="enterprise")
    u = _user()
    paid = _user(tier="starter")
    now = _iso()
    db.execute(
        "INSERT INTO billing_orders (user_id, provider, plan, period, months, amount_cents, status, "
        "created_at, paid_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (paid["id"], "stripe", "starter", "yearly", 12, 4950, "paid", now, now))
    db.execute(
        "INSERT INTO entitlements (user_id, tier, source, expires_at, updated_at) VALUES (?,?,?,?,?)",
        (paid["id"], "starter", "stripe", _iso(-300), now))
    db.execute(
        "INSERT INTO visits (ts, event, visitor_id, session_id, path, referrer, query, browser, os, "
        "device_type, is_bot) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
        (now, "pageview", "v1", "s1", "/", "https://www.reddit.com/r/x", "token=SECRETTOKEN&utm_source=reddit",
         "Chrome", "Linux", "desktop"))
    users.audit("usage.blocked", actor=u, target="free", detail="3/3 (all)")
    return admin, u, paid


def test_populated_numbers_are_right():
    _seed()
    rev = datahub.build("revenue", Params(days=30))
    mrr = next(k for k in rev["kpis"] if k["label"] == "Est. MRR")
    assert mrr["value"] == 412           # $49.50 a year → 4950 / 12 = 412.5 cents a month
    assert next(k for k in rev["kpis"] if k["label"] == "Collected")["value"] == 4950

    traffic = datahub.build("traffic", Params(days=30))
    camps = next(t for t in traffic["tables"] if t["key"] == "campaigns")
    assert [r["source"] for r in camps["rows"]] == ["reddit"]
    refs = next(c for c in traffic["charts"] if c["title"].startswith("Where"))
    assert refs["points"][0]["label"] == "reddit.com"      # host only: no scheme, no www, no path

    usage = datahub.build("usage", Params(days=30))
    assert next(k for k in usage["kpis"] if k["label"] == "Paywall hits")["value"] == 1


def test_no_query_string_value_ever_reaches_a_payload():
    """visits.query can hold one-time activation/reset tokens. Only utm_* are read."""
    _seed()
    import json
    blob = ""
    for d in datahub.DATASETS:
        blob += json.dumps(datahub.build(d.id, Params(days=30, bots=True)), default=str)
    assert "SECRETTOKEN" not in blob


def test_clean_path_hides_credential_looking_segments():
    assert datahub._clean_path("/reset/" + "A" * 40) == "/reset/:token"
    assert datahub._clean_path("/pricing") == "/pricing"
    assert datahub._clean_path(None) == "(none)"


def test_audit_search_is_literal_not_a_pattern():
    users.audit("x.one", actor=None, target="100%-sure")
    users.audit("x.two", actor=None, target="other")
    out = datahub.build("audit", Params(days=30, q="100%"))
    entries = next(t for t in out["tables"] if t["key"] == "entries")["rows"]
    assert [e["action"] for e in entries] == ["x.one"]


def test_csv_cells_cannot_become_formulas():
    assert datahub._csv_cell('=HYPERLINK("x")').startswith("'")
    assert datahub._csv_cell("@SUM(A1)").startswith("'")
    assert datahub._csv_cell("-2+3").startswith("'")
    assert datahub._csv_cell("plain") == "plain"
    assert datahub._csv_cell(None) == ""


# ─────────────────────────── rollups ───────────────────────────

def test_refresh_is_idempotent():
    _seed()
    datahub.refresh(full=True)
    a = db.query("SELECT day, metric, value FROM metrics_daily ORDER BY day, metric")
    datahub.refresh(full=True)
    b = db.query("SELECT day, metric, value FROM metrics_daily ORDER BY day, metric")
    assert a == b and a


def test_refresh_never_overwrites_a_finished_past_day():
    """A day whose raw rows were partly pruned must keep the complete figure."""
    old = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    db.execute("INSERT OR REPLACE INTO metrics_daily (day, metric, value) VALUES (?,?,?)",
               (old, "pageviews", 999))
    db.execute("INSERT INTO visits (ts, event, visitor_id, is_bot) VALUES (?,?,?,0)",
               (old + "T09:00:00+00:00", "pageview", "old1"))
    datahub.refresh(full=True)
    row = db.one("SELECT value FROM metrics_daily WHERE day=? AND metric='pageviews'", (old,))
    assert row["value"] == 999


def test_stock_metrics_are_written_for_today_only():
    _seed()
    datahub.refresh()
    today = datetime.now(timezone.utc).date().isoformat()
    rows = db.query("SELECT day FROM metrics_daily WHERE metric='users_total'")
    assert [r["day"] for r in rows] == [today]


# ─────────────────────────── http ───────────────────────────

def test_endpoints_require_admin(client):
    assert client.get("/api/v1/admin/data").status_code in (401, 403)
    plain = _user()
    _as(client, plain)
    assert client.get("/api/v1/admin/data").status_code == 403
    assert client.get("/api/v1/admin/data/traffic").status_code == 403
    assert client.get("/api/v1/admin/data/users/export?table=recent_signups").status_code == 403


def test_admin_can_browse_and_export(client):
    admin = _user(role="admin", tier="enterprise")
    _as(client, admin)
    cat = client.get("/api/v1/admin/data").json()["datasets"]
    assert {d["id"] for d in cat} == {d.id for d in datahub.DATASETS}
    assert client.get("/api/v1/admin/data/traffic?days=7&bots=true").status_code == 200
    assert client.get("/api/v1/admin/data/nope").status_code == 404
    assert client.get("/api/v1/admin/data/traffic?days=0").status_code == 422

    r = client.get("/api/v1/admin/data/users/export?table=recent_signups")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("Email,Username")
    assert client.get("/api/v1/admin/data/users/export").status_code == 400                  # must name one
    assert client.get("/api/v1/admin/data/users/export?table=zzz").status_code == 404
    # moving personal data off the page is itself an audited event
    assert db.one("SELECT 1 AS x FROM audit_log WHERE action='data.exported'") is not None

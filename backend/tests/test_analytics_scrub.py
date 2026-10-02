"""Visitor analytics must not keep credentials: the collector's URL hygiene and
the one-off scrub for rows stored before it existed.

What matters: an activation/reset link's token and email never reach
`visits.query`, `.referrer`, `.path` or a click's `meta.href`; campaign params
still do; the sanitisers never raise and never fall back to storing the raw
input; and the scrub rewrites old rows through the same rules, touches nothing
else, and finds nothing left to do on a second run.

Run this module on its own — it points ANALYTICS_DB at a temp file *before*
importing the app, which is the only thing keeping it off the live database.
(The module-level fixture below refuses to run if that didn't take.)
"""

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

_TEMP_DB = os.path.join(tempfile.mkdtemp(), "analytics_scrub.db")
os.environ["ANALYTICS_DB"] = _TEMP_DB
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_FROM"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from itsdangerous import URLSafeTimedSerializer  # noqa: E402

from app import db  # noqa: E402
from app.services.analytics import sanitize_query, scrub_row, strip_url  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
EMAIL = "victim@example.test"
# Shaped like the real thing: a signed itsdangerous payload carrying the uid,
# the email and `next`, which is what /activate?token= holds.
TOKEN = URLSafeTimedSerializer("not-the-real-secret").dumps(
    {"uid": 42, "email": EMAIL, "next": "/dashboard"}
)
CHROME = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@pytest.fixture(scope="module", autouse=True)
def _only_the_temp_db():
    """Fail loudly rather than write a single row anywhere but the temp file."""
    assert str(db._db_path()) == _TEMP_DB, "ANALYTICS_DB did not take effect — run this module alone"
    opened = db.get_conn().execute("PRAGMA database_list").fetchone()[2]
    assert opened == _TEMP_DB, f"app.db is already connected to {opened}"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _qs(s):
    from urllib.parse import parse_qs

    return parse_qs(s or "", keep_blank_values=True)


def _all_text(row: dict) -> str:
    return json.dumps(row, default=str)


# ─────────────────────────── query allowlist ───────────────────────────

def test_activation_link_query_is_dropped_entirely():
    assert sanitize_query(f"token={TOKEN}") is None
    assert sanitize_query(f"token={TOKEN}&email=victim%40example.test") is None


def test_campaign_params_survive():
    raw = ("utm_source=reddit&utm_medium=post&utm_campaign=launch"
           "&utm_term=osint&utm_content=a&ref=hn&next=%2Fdashboard")
    out = sanitize_query(raw)
    assert _qs(out) == _qs(raw)


def test_unknown_params_are_dropped_not_just_known_secrets():
    out = sanitize_query(f"utm_source=x&token={TOKEN}&session_id=cs_live_abc&email=a%40b.c&brandnew=1")
    assert out == "utm_source=x"


@pytest.mark.parametrize("raw", ["?utm_source=a", "utm_source=a#frag", "?utm_source=a#token=T"])
def test_leading_question_mark_and_fragment_are_tolerated(raw):
    assert sanitize_query(raw) == "utm_source=a"


def test_fragment_cannot_smuggle_a_param_in():
    assert sanitize_query(f"token=x#utm_source={TOKEN}") is None


def test_first_occurrence_of_a_name_wins():
    assert sanitize_query("ref=a&ref=b&ref=c") == "ref=a"


@pytest.mark.parametrize("raw", [
    f"ref={TOKEN}",                    # a signed token in an allowed slot
    f"utm_source={EMAIL}",             # an email in an allowed slot
    "utm_campaign=victim%40example.test",
    "utm_content=" + "x" * 101,        # longer than any campaign label
    "utm_term=",                       # blank
])
def test_allowed_names_do_not_launder_credential_shaped_values(raw):
    assert sanitize_query(raw) is None


def test_a_later_good_value_still_counts_after_a_rejected_one():
    assert sanitize_query(f"ref={TOKEN}&ref=hn") == "ref=hn"


def test_next_is_a_place_not_a_place_plus_its_params():
    assert _qs(sanitize_query(f"next=%2Factivate%3Ftoken%3D{TOKEN}")) == {"next": ["/activate"]}
    assert _qs(sanitize_query("next=https%3A%2F%2Fuser%3Apw%40evil.test%2Fp%3Fx%3D1")) == {
        "next": ["https://evil.test/p"]
    }


@pytest.mark.parametrize("raw", [
    None, "", "%", "%zz=1", "\x00", "a&&&=", 123, {"token": TOKEN}, [TOKEN],
    "a=1&" * 500,                      # past the field cap
    f"token={TOKEN}" + "&x=1" * 500,
])
def test_garbage_never_raises_and_never_comes_back_raw(raw):
    out = sanitize_query(raw)
    assert out is None
    assert TOKEN not in str(out)


@pytest.mark.parametrize("raw", [
    "utm_source=a&utm_medium=b", "ref=a%20b%26c", "next=%2Fa%2Fb", f"ref=hn&token={TOKEN}",
])
def test_sanitising_twice_changes_nothing(raw):
    once = sanitize_query(raw)
    assert sanitize_query(once) == once


# ─────────────────────────── paths and referrers ───────────────────────────

@pytest.mark.parametrize("raw,want", [
    (f"https://decint.tools/activate?token={TOKEN}", "https://decint.tools/activate"),
    ("https://decint.tools/reset?token=abc#x", "https://decint.tools/reset"),
    ("https://user:pw@host.test/x?y=1", "https://host.test/x"),
    ("https://www.google.com/", "https://www.google.com/"),
    ("https://www.google.com", "https://www.google.com"),
    ("android-app://com.google.android.gm/", "android-app://com.google.android.gm/"),
    ("https://old.reddit.com/r/osinttools/comments/abc", "https://old.reddit.com/r/osinttools/comments/abc"),
    ("/login?next=/x", "/login"),
    ("/activate?email=victim%40example.test", "/activate"),
    ("mailto:dec@decint.tools?subject=Hi", "mailto:dec@decint.tools"),
    ("/pricing", "/pricing"),
])
def test_strip_url_keeps_origin_and_path_only(raw, want):
    assert strip_url(raw) == want


@pytest.mark.parametrize("raw", [None, "", "?token=abc", "http://[::1"])
def test_strip_url_returns_none_rather_than_the_input(raw):
    assert strip_url(raw) is None


# ─────────────────────────── the collector, end to end ───────────────────────────

def _collect(client, **payload):
    r = client.post("/api/v1/analytics/collect", json=payload, headers={"User-Agent": CHROME})
    assert r.status_code == 204
    return db.query("SELECT * FROM visits ORDER BY id DESC LIMIT 1")[0]


def test_pageview_on_an_activation_link_stores_no_credentials(client):
    row = _collect(
        client,
        event="pageview",
        path="/activate",
        query=f"token={TOKEN}&email=victim%40example.test&utm_source=reddit",
        referrer=f"https://example.test/activate?token={TOKEN}",
        title="Activate your account",
    )
    assert row["query"] == "utm_source=reddit"
    assert row["path"] == "/activate"
    assert row["referrer"] == "https://example.test/activate"
    assert TOKEN not in _all_text(row) and EMAIL not in _all_text(row)


def test_reset_link_and_a_path_that_carries_its_own_query(client):
    row = _collect(client, path=f"/reset?token={TOKEN}", query=f"token={TOKEN}")
    assert row["path"] == "/reset" and row["query"] is None
    assert TOKEN not in _all_text(row)


def test_click_href_loses_its_query(client):
    row = _collect(
        client,
        event="click",
        path="/activate",
        meta={"tag": "a", "text": "Continue", "href": f"/activate?email=victim%40example.test&token={TOKEN}"},
    )
    meta = json.loads(row["meta"])
    assert meta == {"tag": "a", "text": "Continue", "href": "/activate"}
    assert TOKEN not in _all_text(row) and EMAIL not in _all_text(row)


def test_ordinary_traffic_is_unchanged(client):
    row = _collect(
        client,
        path="/pricing",
        query="utm_source=newsletter&utm_campaign=oct",
        referrer="https://www.google.com/",
        meta=None,
    )
    assert row["path"] == "/pricing"
    assert _qs(row["query"]) == {"utm_source": ["newsletter"], "utm_campaign": ["oct"]}
    assert row["referrer"] == "https://www.google.com/"
    assert row["meta"] is None


# ─────────────────────────── scrub_row ───────────────────────────

def test_scrub_row_is_empty_for_a_clean_row():
    clean = {
        "path": "/pricing", "query": "utm_source=a", "referrer": "https://www.google.com/",
        "meta": json.dumps({"tag": "a", "href": "/login"}),
    }
    assert scrub_row(clean) == {}
    assert scrub_row({"path": None, "query": None, "referrer": None, "meta": None}) == {}
    assert scrub_row({"path": "", "query": "", "referrer": "", "meta": ""}) == {}


def test_scrub_row_returns_only_the_columns_that_change():
    fix = scrub_row({
        "path": "/activate",
        "query": f"token={TOKEN}&utm_source=a",
        "referrer": "https://www.google.com/",
        "meta": None,
    })
    assert fix == {"query": "utm_source=a"}


def test_a_truncated_meta_blob_still_loses_its_href_query():
    # json.dumps(...)[:1024] can cut a blob mid-string, leaving invalid JSON.
    broken = '{"tag": "a", "href": "/activate?email=victim%40example.test&to'
    fix = scrub_row({"meta": broken})
    assert fix == {"meta": '{"tag": "a", "href": "/activate'}


# ─────────────────────────── the scrub script ───────────────────────────

def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "scrub_visit_urls", BACKEND / "migrations" / "004_scrub_visit_urls.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table():
    return db.query("SELECT * FROM visits ORDER BY id")


def _seed():
    """Rows as the old collector stored them, plus ones that were already fine."""
    conn = db.get_conn()
    conn.execute("DELETE FROM visits")
    base = {"ts": "2026-10-01T10:00:00+00:00", "event": "pageview", "visitor_id": "v1",
            "ip": "203.0.113.0", "user_agent": CHROME, "title": "t", "is_bot": 0}
    rows = [
        {"path": "/activate", "query": f"token={TOKEN}&email=victim%40example.test"},
        {"path": "/reset", "query": f"token={TOKEN}"},
        {"path": "/billing/success", "query": "session_id=cs_live_abc&utm_source=ads"},
        {"path": "/login", "query": "next=%2Fdashboard", "referrer": f"https://example.test/activate?token={TOKEN}"},
        {"path": "/activate", "event": "click",
         "meta": json.dumps({"tag": "a", "text": "Resend", "href": "/activate?email=victim%40example.test"})},
        {"path": "/activate", "event": "click",
         "meta": '{"tag": "a", "href": "/activate?email=victim%40example.test&to'},
        # already clean:
        {"path": "/pricing", "query": "utm_source=newsletter", "referrer": "https://www.google.com/"},
        {"path": "/", "query": None, "referrer": None},
    ]
    for r in rows:
        row = {**base, **r}
        conn.execute(
            f"INSERT INTO visits ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
    conn.commit()
    return len(rows)


def test_scrub_script_rewrites_old_rows_and_nothing_else(capsys):
    mig = _load_migration()
    n = _seed()
    before = _table()
    assert any(TOKEN in _all_text(r) for r in before)  # the fixture really is dirty

    # --dry-run reports and changes nothing.
    assert mig.main(["--db", _TEMP_DB, "--dry-run"]) == 0
    assert "would rewrite 6" in capsys.readouterr().out
    assert _table() == before

    # The real run, with the app's own connection still open on the file.
    assert mig.main(["--db", _TEMP_DB]) == 0
    assert "rewrote 6" in capsys.readouterr().out
    after = _table()
    assert len(after) == n
    text = _all_text(after)
    assert TOKEN not in text and EMAIL not in text and "cs_live_abc" not in text and "victim" not in text

    # Only path/query/referrer/meta may differ; every other column is untouched.
    scrubbed = {"path", "query", "referrer", "meta"}
    for old, new in zip(before, after):
        assert {k: v for k, v in old.items() if k not in scrubbed} == \
               {k: v for k, v in new.items() if k not in scrubbed}

    assert after[2]["query"] == "utm_source=ads"
    assert _qs(after[3]["query"]) == {"next": ["/dashboard"]}
    assert after[3]["referrer"] == "https://example.test/activate"
    assert json.loads(after[4]["meta"]) == {"tag": "a", "text": "Resend", "href": "/activate"}
    assert after[5]["meta"] == '{"tag": "a", "href": "/activate'
    assert after[6] == before[6] and after[7] == before[7]  # clean rows: byte-identical

    # Idempotent: the second run has nothing to do.
    assert mig.main(["--db", _TEMP_DB]) == 0
    assert "rewrote 0" in capsys.readouterr().out
    assert _table() == after


def test_scrub_script_with_no_database_is_a_no_op(tmp_path, capsys):
    mig = _load_migration()
    missing = tmp_path / "nope.db"
    assert mig.main(["--db", str(missing)]) == 0
    assert "nothing to scrub" in capsys.readouterr().out
    assert not missing.exists()  # must not create an empty database


def test_scrub_script_defaults_to_the_configured_database():
    mig = _load_migration()
    assert str(mig.db_path()) == _TEMP_DB
    assert str(mig.db_path("/somewhere/else.db")) == "/somewhere/else.db"

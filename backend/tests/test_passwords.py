"""Password checker: leakedpassword.com first, Pwned Passwords as the fallback.

What matters: only a SHA-1 ever leaves the server (and to leakedpassword.com
only as `?p=&s=<sha1>`, the one form its guard accepts), a non-zero `seen`
counts as leaked whatever `leak` says, a failed lookup is reported as failed
rather than "not leaked", the fallback sends nothing but a 5-character prefix,
and a check costs one search unless nothing could be answered.
"""

import asyncio
import hashlib
import json
import os
import tempfile

import httpx
import pytest
import respx

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "passwords.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.routers import passwords as router_mod  # noqa: E402
from app.services import passwords as svc  # noqa: E402
from app.services import usage, users  # noqa: E402

svc.settings = cfg.settings
router_mod.settings = cfg.settings

API = cfg.settings.passwords_api_url
RANGE = svc.HIBP_RANGE


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


PASSWORD = sha1("password")  # 5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8
UNSEEN = sha1("correct horse battery staple, but longer and unlisted 81c2")


def lp_answer(h: str, leak: bool, seen: int) -> httpx.Response:
    # The real API's Content-Type header is just "charset=utf-8".
    body = json.dumps({"password": {"leak": leak, "hash": h, "seen": seen}})
    return httpx.Response(200, text=body, headers={"Content-Type": "charset=utf-8"})


def lp_error(message: str) -> httpx.Response:
    return httpx.Response(200, text=json.dumps({"error": message}))


def range_body(*rows: tuple[str, int]) -> str:
    return "\r\n".join(f"{h[5:].upper()}:{n}" for h, n in rows)


def run(*hashes: str):
    return asyncio.run(svc.check(list(hashes)))


# ─────────────────────────── leakedpassword.com ───────────────────────────

@respx.mock
def test_leaked_hash_reports_how_often_it_was_seen():
    route = respx.get(API).mock(return_value=lp_answer(PASSWORD, True, 52372427))
    res = run(PASSWORD)
    assert res.total == 1 and res.leaked == 1 and res.failed == 0
    r = res.results[0]
    assert r.ok and r.leaked and r.seen == 52372427 and r.source == "leakedpassword"
    assert "Have I Been Pwned" in res.attribution
    assert route.call_count == 1


@respx.mock
def test_query_is_empty_p_plus_the_hash_and_never_a_password():
    # `?s=` alone is refused upstream ("Invalid API query"); `p` must be present
    # and must stay empty.
    route = respx.get(API).mock(return_value=lp_answer(PASSWORD, True, 1))
    run(PASSWORD)
    params = route.calls.last.request.url.params
    assert params["p"] == ""
    assert params["s"] == PASSWORD
    assert set(params.keys()) == {"p", "s"}
    assert route.calls.last.request.headers["User-Agent"].startswith("decint-tools/")


@respx.mock
def test_unseen_hash_is_clean():
    respx.get(API).mock(return_value=lp_answer(UNSEEN, False, 0))
    r = (run(UNSEEN)).results[0]
    assert r.ok and not r.leaked and r.seen == 0


@respx.mock
def test_nonzero_seen_wins_over_a_false_leak_flag():
    # PHP 7 reads a match on the range's first line as `0 != ""` → false.
    respx.get(API).mock(return_value=lp_answer(PASSWORD, False, 17))
    r = (run(PASSWORD)).results[0]
    assert r.leaked and r.seen == 17


@respx.mock
def test_answer_about_another_hash_is_not_trusted():
    cfg.settings.passwords_hibp_fallback = False
    try:
        respx.get(API).mock(return_value=lp_answer(UNSEEN, True, 9))
        r = (run(PASSWORD)).results[0]
    finally:
        cfg.settings.passwords_hibp_fallback = True
    assert not r.ok and not r.leaked and "unexpected response" in r.error


# ─────────────────────────── fallback to Pwned Passwords ───────────────────────────

@respx.mock
def test_api_error_falls_back_to_the_range_api_with_only_a_prefix():
    respx.get(API).mock(return_value=lp_error("Query from non-secure connection"))
    rng = respx.get(url__startswith=RANGE).mock(
        return_value=httpx.Response(200, text=range_body((PASSWORD, 52372427), (UNSEEN, 0)))
    )
    r = (run(PASSWORD)).results[0]
    assert r.ok and r.leaked and r.seen == 52372427 and r.source == "hibp_range"
    sent = rng.calls.last.request
    assert str(sent.url) == RANGE + PASSWORD[:5].upper()
    assert PASSWORD[5:].lower() not in str(sent.url).lower()
    assert sent.headers["Add-Padding"] == "true"


@respx.mock
def test_padding_rows_do_not_count_as_leaked():
    respx.get(API).mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.get(url__startswith=RANGE).mock(
        return_value=httpx.Response(200, text=range_body((PASSWORD, 0)))
    )
    r = (run(PASSWORD)).results[0]
    assert r.ok and not r.leaked and r.seen == 0 and r.source == "hibp_range"


@respx.mock
def test_hashes_sharing_a_prefix_share_one_range_request():
    a = PASSWORD
    b = a[:5] + "0" * 35
    respx.get(API).mock(return_value=httpx.Response(503))
    rng = respx.get(url__startswith=RANGE).mock(
        return_value=httpx.Response(200, text=range_body((a, 3), (b, 5)))
    )
    res = run(a, b)
    assert [r.seen for r in res.results] == [3, 5]
    assert rng.call_count == 1


@respx.mock
def test_both_sources_down_is_failed_not_clean():
    respx.get(API).mock(return_value=lp_error("Invalid API query"))
    respx.get(url__startswith=RANGE).mock(return_value=httpx.Response(500))
    res = run(PASSWORD)
    r = res.results[0]
    assert not r.ok and not r.leaked and res.failed == 1 and res.leaked == 0
    assert "leakedpassword.com: Invalid API query" in r.error
    assert "Pwned Passwords" in r.error


@respx.mock
def test_fallback_off_reports_the_failure():
    cfg.settings.passwords_hibp_fallback = False
    try:
        respx.get(API).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
        rng = respx.get(url__startswith=RANGE)
        r = (run(PASSWORD)).results[0]
    finally:
        cfg.settings.passwords_hibp_fallback = True
    assert not r.ok and r.error.startswith("leakedpassword.com:")
    assert rng.call_count == 0


@respx.mock
def test_results_keep_request_order_when_some_fall_back():
    def answer(request):
        h = request.url.params["s"]
        if h == PASSWORD:
            return lp_error("Invalid API query")
        return lp_answer(h, False, 0)

    respx.get(API).mock(side_effect=answer)
    respx.get(url__startswith=RANGE).mock(
        return_value=httpx.Response(200, text=range_body((PASSWORD, 2)))
    )
    res = run(UNSEEN, PASSWORD)
    assert [r.hash for r in res.results] == [UNSEEN, PASSWORD]
    assert [r.source for r in res.results] == ["leakedpassword", "hibp_range"]


def test_normalize():
    assert svc.normalize(PASSWORD.upper()) == PASSWORD
    assert svc.normalize(f"  {PASSWORD}\n") == PASSWORD
    assert svc.normalize("password") is None
    assert svc.normalize(PASSWORD[:-1]) is None
    assert svc.normalize(PASSWORD[:-1] + "g") is None


# ─────────────────────────── the endpoint ───────────────────────────

_seq = [0]


def _user(tier="free"):
    _seq[0] += 1
    db.get_conn()
    return users.create(f"pw{_seq[0]}@example.test", "a-long-enough-password", tier=tier)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def _stub(monkeypatch, *, fail_all=False, crash=False):
    seen = []

    async def fake(hashes):
        seen.append(list(hashes))
        if crash:
            raise RuntimeError("boom")
        results = [
            svc.PasswordResult(hash=h, ok=not fail_all, error="down" if fail_all else None)
            for h in hashes
        ]
        return svc.PasswordCheckResponse(
            total=len(results), leaked=0, failed=sum(not r.ok for r in results),
            results=results, attribution=svc.ATTRIBUTION,
        )

    monkeypatch.setattr(svc, "check", fake)
    return seen


def test_needs_a_session(client):
    assert client.post("/api/v1/passwords/check", json={"hashes": [PASSWORD]}).status_code == 401


def test_a_batch_is_one_search_and_duplicates_collapse(client, monkeypatch):
    seen = _stub(monkeypatch)
    u = _user()
    r = _signed_in(client, u).post(
        "/api/v1/passwords/check", json={"hashes": [PASSWORD.upper(), UNSEEN, PASSWORD]}
    )
    assert r.status_code == 200, r.text
    assert seen == [[PASSWORD, UNSEEN]]
    assert usage.status(u)["used"] == 1


def test_a_password_is_refused_uncharged_and_never_echoed(client, monkeypatch):
    seen = _stub(monkeypatch)
    u = _user()
    r = _signed_in(client, u).post(
        "/api/v1/passwords/check", json={"hashes": [PASSWORD, "hunter2-secret"]}
    )
    assert r.status_code == 400
    assert "Entry 2" in r.json()["detail"]
    assert "hunter2" not in r.text
    assert seen == [] and usage.status(u)["used"] == 0


def test_too_many_hashes_is_refused_uncharged(client, monkeypatch):
    seen = _stub(monkeypatch)
    u = _user()
    many = [PASSWORD] * (cfg.settings.passwords_batch_max + 1)
    r = _signed_in(client, u).post("/api/v1/passwords/check", json={"hashes": many})
    assert r.status_code == 400 and seen == [] and usage.status(u)["used"] == 0


def test_empty_list_is_refused(client, monkeypatch):
    _stub(monkeypatch)
    r = _signed_in(client, _user()).post("/api/v1/passwords/check", json={"hashes": []})
    assert r.status_code == 422


def test_total_outage_is_refunded(client, monkeypatch):
    _stub(monkeypatch, fail_all=True)
    u = _user()
    r = _signed_in(client, u).post("/api/v1/passwords/check", json={"hashes": [PASSWORD]})
    assert r.status_code == 200 and r.json()["failed"] == 1
    assert usage.status(u)["used"] == 0


def test_a_crash_is_refunded(client, monkeypatch):
    _stub(monkeypatch, crash=True)
    u = _user()
    c = _signed_in(client, u)
    with pytest.raises(RuntimeError):
        c.post("/api/v1/passwords/check", json={"hashes": [PASSWORD]})
    assert usage.status(u)["used"] == 0


def test_spent_trial_gets_the_402(client, monkeypatch):
    _stub(monkeypatch)
    u = _user()
    for _ in range(3):
        usage.consume(u)
    r = _signed_in(client, u).post("/api/v1/passwords/check", json={"hashes": [PASSWORD]})
    assert r.status_code == 402 and r.headers["X-Upgrade-Path"] == "/pricing"

"""Phone lookup: VeriRoute Intel's LRN answer with its add-ons.

What matters, since every lookup is paid for: a number that cannot exist is
refused before anything is sent, an answer is reused rather than bought twice,
the monthly and daily caps hold, a provider failure costs the customer
nothing, and VeriRoute's objects (lrn, enhanced_lrn, messaging, cnam, trust)
arrive under the names they were sent with.
"""

import asyncio
import json
import os
import tempfile

import httpx
import pytest
import respx

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "phonelookup.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"
os.environ["IPLOOKUP_AUTO_UPDATE"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_FROM"] = ""
# Never the real key: a test must not be able to spend money.
os.environ["PHONE_VRI_API_KEY"] = "test-key"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import phonelookup as svc  # noqa: E402
from app.services import usage, users  # noqa: E402

svc.settings = cfg.settings
URL = cfg.settings.phone_vri_url

# Recorded from a live call on VeriRoute's own documentation number.
ANSWER = {
    "cnam": "WIRELESS CALLER",
    "enhanced_lrn": {
        "carrier": "Verizon Wireless", "carrier_type": "WIRELESS", "city": "Clemmons",
        "country_code": "US", "county": "Forsyth", "lata": "424", "ocn": "6324",
        "rate_center": "WINSTN SAL", "state": "NC", "timezone": "-0500", "zip_code": "27012",
    },
    "lrn": "13364086644",
    "messaging": {
        "country": "United States", "country_code": "US", "enabled": True,
        "provider": "Verizon Wireless", "reference_id": "us_verizon",
    },
    "phone_number": "13364086644",
    "trust": {
        "is_robocall": False, "is_scam": False, "is_spam": False,
        "last_updated": "2026-10-05T21:25:30.247043+00:00", "partial_results": False,
        "reputation_score": 67, "spam_available": True, "spam_type": "NONE",
        "trust_level": "medium", "verdict_status": "clear",
    },
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    svc._cache.clear()
    svc._today.update(day="", count=0)
    monkeypatch.setattr(svc.settings, "phone_vri_api_key", "test-key")
    monkeypatch.setattr(svc.settings, "phone_monthly_limit", 100)
    monkeypatch.setattr(svc.settings, "phone_daily_limit", 500)


# ─────────────────────────── parsing ───────────────────────────

@pytest.mark.parametrize("raw", [
    "+1 (336) 408-6644", "336.408.6644", "3364086644", "13364086644",
    "1-336-408-6644", "tel:+13364086644", "(336) 408-6644 ext. 12", "336 408 6644 x9",
])
def test_formats_people_paste(raw):
    assert svc.parse_number(raw) == "13364086644"


def test_keypad_letters():
    assert svc.parse_number("1-800-FLOWERS") == "18003569377"


@pytest.mark.parametrize("raw,why", [
    ("+44 20 7946 0958", "US and Canadian"),
    ("011 44 20 7946 0958", "US and Canadian"),
    ("408-6644", "10 digits"),
    ("123-456-7890", "never start with 0 or 1"),
    ("911-555-1234", "service code"),
    ("336-111-2222", "exchanges never start"),
    ("336-411-2222", "service code"),
    ("212-555-0123", "films and TV"),
    ("alice@example.com", "doesn't look like"),
    ("", "Enter a phone number"),
])
def test_impossible_numbers_are_refused(raw, why):
    with pytest.raises(ValueError, match=why):
        svc.parse_number(raw)


def test_formatting():
    assert svc.e164("13364086644") == "+13364086644"
    assert svc.national("13364086644") == "(336) 408-6644"


@pytest.mark.parametrize("number,ctype,want", [
    ("13364086644", "WIRELESS", "mobile"),
    ("13364086644", "WRS", "mobile"),
    ("13364086644", "ILEC", "landline"),
    ("13364086644", "CLEC", "landline"),
    ("13364086644", "VOIP", "voip"),
    ("13364086644", None, "unknown"),
    ("18005551234", "WIRELESS", "toll_free"),
])
def test_line_type(number, ctype, want):
    assert svc.line_type(number, ctype) == want


# ─────────────────────────── the answer ───────────────────────────

def test_answer_keeps_veriroutes_objects():
    r = svc.parse_answer("13364086644", "336-408-6644", ANSWER)
    assert r.cnam == "WIRELESS CALLER" and r.lrn == "13364086644"
    assert r.enhanced_lrn.carrier == "Verizon Wireless" and r.enhanced_lrn.zip_code == "27012"
    assert r.messaging.provider == "Verizon Wireless" and r.messaging.enabled is True
    assert r.trust.reputation_score == 67 and r.trust.trust_level == "medium"
    assert r.trust.verdict_status == "clear"
    assert r.line_type == "mobile" and r.lrn_activated_at is None
    assert r.raw == ANSWER
    assert r.requested == ["lrn", "enhanced_lrn", "messaging", "cnam", "trust"]


def test_partial_answer():
    r = svc.parse_answer("13364086644", "q", {"lrn": "13365550000", "cnam": "", "trust": {}})
    assert r.cnam is None and r.trust is None and r.enhanced_lrn is None and r.line_type == "unknown"


def test_cnam_as_object():
    r = svc.parse_answer("13364086644", "q", {"lrn": "1", "cnam": {"caller_name": "ACME CORP"}})
    assert r.cnam == "ACME CORP"


@respx.mock
def test_request_asks_for_every_addon_with_the_key():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=ANSWER))
    asyncio.run(svc.lookup("13364086644", "q"))
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer test-key"
    assert json.loads(req.content) == {
        "phone_number": "13364086644", "include_enhanced_lrn": True,
        "messaging_lookup": True, "include_cnam": True, "include_trust": True,
    }


@respx.mock
def test_addons_follow_settings(monkeypatch):
    monkeypatch.setattr(svc.settings, "phone_include_cnam", False)
    monkeypatch.setattr(svc.settings, "phone_include_trust", False)
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"lrn": "13364086644"}))
    r = asyncio.run(svc.lookup("13364086644", "q"))
    body = json.loads(route.calls.last.request.content)
    assert body["include_cnam"] is False and body["include_trust"] is False
    assert r.requested == ["lrn", "enhanced_lrn", "messaging"]


@respx.mock
def test_second_lookup_comes_from_cache():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=ANSWER))
    first = asyncio.run(svc.lookup("13364086644", "a"))
    again = asyncio.run(svc.lookup("13364086644", "b"))
    assert route.call_count == 1
    assert not first.cached and again.cached and again.query == "b"
    assert svc._today["count"] == 1  # the cached one cost nothing


@respx.mock
def test_daily_cap(monkeypatch):
    monkeypatch.setattr(svc.settings, "phone_daily_limit", 1)
    respx.post(URL).mock(return_value=httpx.Response(200, json=ANSWER))
    asyncio.run(svc.lookup("13364086644", "q"))
    with pytest.raises(svc.CapReached, match="paused until midnight"):
        asyncio.run(svc.lookup("12125550100", "q"))


@pytest.mark.parametrize("status,want_status,text", [
    (400, 400, "refused the number"),
    (401, 503, "misconfigured"),
    (402, 503, "credit has run out"),
    (429, 503, "rate-limiting"),
    (500, 502, "HTTP 500"),
])
@respx.mock
def test_upstream_errors(status, want_status, text):
    respx.post(URL).mock(return_value=httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(svc.LookupFailed, match=text) as e:
        asyncio.run(svc.lookup("13364086644", "q"))
    assert e.value.status == want_status
    assert svc._today["count"] == 0  # a failure is not counted against the day


@respx.mock
def test_timeout():
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(svc.LookupFailed, match="timed out"):
        asyncio.run(svc.lookup("13364086644", "q"))


# ─────────────────────────── the endpoint ───────────────────────────

_seq = [0]


def _user(tier="free", role="user"):
    _seq[0] += 1
    db.get_conn()
    return users.create(f"phone{_seq[0]}@example.test", "a-long-enough-password", tier=tier, role=role)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def test_needs_a_session(client):
    assert client.post("/api/v1/phone/lookup", json={"number": "3364086644"}).status_code == 401


@respx.mock
def test_a_lookup_is_one_search_and_one_phone_lookup(client):
    respx.post(URL).mock(return_value=httpx.Response(200, json=ANSWER))
    u = _user()
    r = _signed_in(client, u).post("/api/v1/phone/lookup", json={"number": "(336) 408-6644"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["cnam"] == "WIRELESS CALLER" and j["national"] == "(336) 408-6644"
    assert j["enhanced_lrn"]["rate_center"] == "WINSTN SAL"
    assert usage.status(u)["used"] == 1 and svc.monthly_used(u) == 1


def test_bad_number_is_refused_uncharged(client):
    u = _user()
    r = _signed_in(client, u).post("/api/v1/phone/lookup", json={"number": "+44 20 7946 0958"})
    assert r.status_code == 400 and "US and Canadian" in r.json()["detail"]
    assert usage.status(u)["used"] == 0 and svc.monthly_used(u) == 0


def test_no_key_is_503_uncharged(client, monkeypatch):
    monkeypatch.setattr(svc.settings, "phone_vri_api_key", "")
    u = _user()
    r = _signed_in(client, u).post("/api/v1/phone/lookup", json={"number": "3364086644"})
    assert r.status_code == 503 and usage.status(u)["used"] == 0


@respx.mock
def test_provider_failure_is_refunded(client):
    respx.post(URL).mock(return_value=httpx.Response(402, json={"error": "Insufficient balance"}))
    u = _user()
    r = _signed_in(client, u).post("/api/v1/phone/lookup", json={"number": "3364086644"})
    assert r.status_code == 503 and "credit" in r.json()["detail"]
    assert usage.status(u)["used"] == 0 and svc.monthly_used(u) == 0


@respx.mock
def test_monthly_cap(client, monkeypatch):
    monkeypatch.setattr(svc.settings, "phone_monthly_limit", 1)
    respx.post(URL).mock(return_value=httpx.Response(200, json=ANSWER))
    u = _user(tier="starter")
    c = _signed_in(client, u)
    assert c.post("/api/v1/phone/lookup", json={"number": "3364086644"}).status_code == 200
    r = c.post("/api/v1/phone/lookup", json={"number": "3364086644"})
    assert r.status_code == 429 and "1 phone lookup" in r.json()["detail"]
    # The refused one did not cost a search either.
    assert usage.status(u)["used"] == 1 and svc.monthly_used(u) == 1


@respx.mock
def test_staff_are_not_capped(client, monkeypatch):
    monkeypatch.setattr(svc.settings, "phone_monthly_limit", 1)
    respx.post(URL).mock(return_value=httpx.Response(200, json=ANSWER))
    u = _user(role="admin")
    c = _signed_in(client, u)
    for _ in range(3):
        assert c.post("/api/v1/phone/lookup", json={"number": "3364086644"}).status_code == 200
    assert svc.monthly_used(u) == 0


def test_free_trial_spent_is_402_before_the_phone_cap(client):
    u = _user()
    for _ in range(3):
        usage.consume(u)
    r = _signed_in(client, u).post("/api/v1/phone/lookup", json={"number": "3364086644"})
    assert r.status_code == 402 and svc.monthly_used(u) == 0


def test_a_crash_is_refunded(client, monkeypatch):
    async def boom(number, query):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "lookup", boom)
    u = _user()
    with pytest.raises(RuntimeError):
        _signed_in(client, u).post("/api/v1/phone/lookup", json={"number": "3364086644"})
    assert usage.status(u)["used"] == 0 and svc.monthly_used(u) == 0


def test_deleting_an_account_clears_its_phone_count():
    u = _user(tier="starter")
    svc.take_monthly(u)
    users.delete(u["id"])
    assert db.one("SELECT COUNT(*) AS n FROM phone_counters WHERE user_id = ?", (u["id"],))["n"] == 0

"""IP lookup: local location/ASN databases, RDAP, reverse DNS, Tor exits.

What matters: a target is parsed the way people paste it, a private or
reserved address is classified and never sent anywhere, RDAP's holder and
abuse contact are read the same from ARIN- and RIPE-shaped answers, a source
that fails is reported as failed rather than as "nothing found", a lookup that
learned nothing is not charged, and the DB-IP updater never replaces a good
file with a bad one.
"""

import asyncio
import gzip
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "iplookup.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"
os.environ["IPLOOKUP_AUTO_UPDATE"] = "false"
# Reputation is switched on per test: no test may reach the real AbuseIPDB.
os.environ["IPLOOKUP_ABUSEIPDB_KEY"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import iplookup as svc  # noqa: E402
from app.services import usage, users  # noqa: E402

svc.settings = cfg.settings

RDAP = cfg.settings.iplookup_rdap_url
TOR = cfg.settings.iplookup_tor_list_url


# ─────────────────────────── fixtures ───────────────────────────

class FakeReader:
    def __init__(self, dbtype: str, records: dict, plen: int = 24):
        self.dbtype, self.records, self.plen = dbtype, records, plen

    def metadata(self):
        return SimpleNamespace(database_type=self.dbtype)

    def get(self, ip):
        return self.records.get(ip)

    def get_with_prefix_len(self, ip):
        rec = self.records.get(ip)
        return (rec, self.plen) if rec else (None, 0)


CITY = {
    "8.8.8.8": {
        "city": {"names": {"en": "Mountain View"}},
        "continent": {"code": "NA", "names": {"en": "North America"}},
        "country": {"iso_code": "US", "is_in_european_union": False,
                    "names": {"en": "United States"}},
        "location": {"latitude": 37.422, "longitude": -122.085},
        "subdivisions": [{"names": {"en": "California"}}],
    },
}
ASN = {"8.8.8.8": {"autonomous_system_number": 15169,
                   "autonomous_system_organization": "Google LLC"}}


def vcard(*props):
    return ["vcard", [["version", {}, "text", "4.0"], *props]]


ARIN = {
    "objectClassName": "ip network",
    "handle": "NET-8-8-8-0-2", "name": "GOGL", "type": "DIRECT ALLOCATION",
    "startAddress": "8.8.8.0", "endAddress": "8.8.8.255",
    "cidr0_cidrs": [{"v4prefix": "8.8.8.0", "length": 24}],
    "port43": "whois.arin.net",
    "events": [{"eventAction": "last changed", "eventDate": "2023-12-28T17:24:56-05:00"},
               {"eventAction": "registration", "eventDate": "2023-12-28T17:24:33-05:00"}],
    "entities": [{
        "handle": "GOGL", "roles": ["registrant"],
        "vcardArray": vcard(["fn", {}, "text", "Google LLC"],
                            ["adr", {"label": "1600 Amphitheatre Parkway\nMountain View\nCA"},
                             "text", ["", "", "", "", "", "", ""]]),
        # ARIN nests the abuse contact inside the organisation.
        "entities": [{
            "handle": "ABUSE5250-ARIN", "roles": ["abuse"],
            "vcardArray": vcard(["fn", {}, "text", "Abuse"],
                                ["email", {}, "text", "network-abuse@google.com"]),
        }],
    }],
}

RIPE = {
    "objectClassName": "ip network",
    "handle": "82.22.36.0 - 82.22.36.255", "name": "NET-82-22-36-0-24",
    "type": "ASSIGNED PA", "country": "CH", "port43": "whois.ripe.net",
    "startAddress": "82.22.36.0", "endAddress": "82.22.36.255",
    "entities": [
        # The maintainer is listed as a registrant too; it is not the holder.
        {"handle": "netutils-mnt", "roles": ["registrant"],
         "vcardArray": vcard(["fn", {}, "text", "netutils-mnt"], ["kind", {}, "text", "individual"])},
        {"handle": "ORG-HB183-RIPE", "roles": ["registrant"],
         "vcardArray": vcard(["fn", {}, "text", "HOSTKEY B.V."], ["kind", {}, "text", "org"])},
        {"handle": "HB8279-RIPE", "roles": ["administrative", "technical"],
         "vcardArray": vcard(["fn", {}, "text", "HOSTKEY B.V."],
                             ["email", {"type": "abuse"}, "text", "report@abuseradar.com"])},
    ],
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    svc._rdap_cache.clear()
    svc._tor.update(ips=None, fetched=0.0, tried=0.0)
    monkeypatch.setattr(svc._CITY, "get", lambda: FakeReader("DBIP-City-Lite", CITY))
    monkeypatch.setattr(svc._ASN, "get", lambda: FakeReader("DBIP-ASN-Lite (compat=GeoLite2-ASN)", ASN))

    async def ptr(ip):
        return ("dns.google", True) if ip == "8.8.8.8" else (None, None)

    monkeypatch.setattr(svc, "_ptr", ptr)


def run(target: str):
    async def go():
        return await svc.lookup(await svc.resolve_target(target))
    return asyncio.run(go())


# ─────────────────────────── parsing ───────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("8.8.8.8", ("ip", "8.8.8.8")),
    ("  8.8.8.8:53 ", ("ip", "8.8.8.8")),
    ("2001:4860:4860::8888", ("ip", "2001:4860:4860::8888")),
    ("[2001:4860:4860::8888]:443", ("ip", "2001:4860:4860::8888")),
    ("::ffff:8.8.8.8", ("ip", "8.8.8.8")),
    ("https://Example.com:8443/path?q=1", ("hostname", "example.com")),
    ("example.com.", ("hostname", "example.com")),
    ("example.com/login", ("hostname", "example.com")),
    ("mail.example.co.uk:25", ("hostname", "mail.example.co.uk")),
    ("bücher.de", ("hostname", "xn--bcher-kva.de")),
])
def test_targets_are_read_the_way_people_paste_them(raw, want):
    assert svc.parse_target(raw) == want


@pytest.mark.parametrize("raw", [
    "", "   ", "localhost", "not a host", "-bad.example", "1.2.3.4/24", "999.1.1.1.1",
    "http://", "a" * 64 + ".com",
])
def test_nonsense_is_refused(raw):
    with pytest.raises(ValueError):
        svc.parse_target(raw)


@pytest.mark.parametrize("ip,scope", [
    ("8.8.8.8", "public"), ("10.1.2.3", "private"), ("192.168.0.1", "private"),
    ("127.0.0.1", "loopback"), ("::1", "loopback"), ("169.254.1.1", "link_local"),
    ("224.0.0.1", "multicast"), ("100.64.0.1", "shared"), ("192.0.2.5", "documentation"),
    ("2001:db8::1", "documentation"), ("0.0.0.0", "unspecified"), ("240.0.0.1", "reserved"),
])
def test_scope(ip, scope):
    import ipaddress

    assert svc.scope_of(ipaddress.ip_address(ip)) == scope


def test_arin_holder_and_nested_abuse_contact():
    reg = svc.parse_rdap(ARIN, "rdap.arin.net")
    assert reg.registry == "ARIN" and reg.name == "GOGL"
    assert reg.org == "Google LLC"
    assert reg.org_address == "1600 Amphitheatre Parkway, Mountain View, CA"
    assert reg.abuse_email == "network-abuse@google.com"
    assert reg.cidrs == ["8.8.8.0/24"] and reg.range == "8.8.8.0 - 8.8.8.255"
    assert reg.registered.startswith("2023-12-28")


def test_ripe_holder_is_the_org_not_the_maintainer():
    reg = svc.parse_rdap(RIPE, "rdap.db.ripe.net")
    assert reg.registry == "RIPE NCC"
    assert reg.org == "HOSTKEY B.V."
    assert reg.abuse_email == "report@abuseradar.com"  # typed abuse email, no abuse role
    assert reg.country == "CH"


# ─────────────────────────── the lookup ───────────────────────────

@respx.mock
def test_public_address_gets_everything():
    respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx.get(TOR).mock(return_value=httpx.Response(200, text="185.220.101.1\n8.8.4.4\n"))
    res = run("8.8.8.8")
    r = res.results[0]
    assert r.scope == "public" and not r.errors
    assert r.location.city == "Mountain View" and r.location.country_code == "US"
    assert r.network.asn == 15169 and r.network.prefix == "8.8.8.0/24"
    assert r.registration.org == "Google LLC"
    assert (r.ptr, r.ptr_confirmed) == ("dns.google", True)
    assert r.tor_exit is False
    assert svc.answered(res)
    assert any("DB-IP" in a for a in res.attribution)


@respx.mock
def test_tor_exit_is_flagged():
    respx.get(RDAP + "185.220.101.1").mock(return_value=httpx.Response(404))
    respx.get(TOR).mock(return_value=httpx.Response(200, text="185.220.101.1\n"))
    r = run("185.220.101.1").results[0]
    assert r.tor_exit is True
    assert "no registry record" in r.errors["rdap"]


@respx.mock(assert_all_called=False)
def test_private_address_is_never_sent_anywhere(respx_mock):
    rdap = respx_mock.get(url__startswith=RDAP)
    respx_mock.get(TOR).mock(return_value=httpx.Response(200, text=""))
    res = run("192.168.1.10")
    r = res.results[0]
    assert r.scope == "private"
    assert r.location is None and r.registration is None and r.ptr is None
    assert not rdap.called
    assert not svc.answered(res)


@respx.mock
def test_a_failed_source_is_reported_not_silently_empty():
    respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(429))
    respx.get(TOR).mock(side_effect=httpx.ConnectTimeout("slow"))
    res = run("8.8.8.8")
    r = res.results[0]
    assert "rate-limiting" in r.errors["rdap"]
    assert r.location.city == "Mountain View"  # the rest still answers
    assert r.tor_exit is None  # unknown, not "no"
    src = {s.key: s for s in res.sources}
    assert not src["rdap"].ok and not src["tor"].ok and src["location"].ok
    assert svc.answered(res)


@respx.mock
def test_no_databases_installed_says_so(monkeypatch):
    monkeypatch.setattr(svc._CITY, "get", lambda: None)
    monkeypatch.setattr(svc._ASN, "get", lambda: None)
    respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))
    r = run("8.8.8.8").results[0]
    assert r.errors["location"] == "no location database installed"
    assert r.errors["network"] == "no ASN database installed"
    assert r.registration.org == "Google LLC"


@respx.mock
def test_everything_down_learns_nothing(monkeypatch):
    monkeypatch.setattr(svc._CITY, "get", lambda: None)
    monkeypatch.setattr(svc._ASN, "get", lambda: None)
    respx.get(RDAP + "8.8.8.8").mock(side_effect=httpx.ConnectError("down"))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))
    assert not svc.answered(run("8.8.8.8"))


@respx.mock
def test_rdap_answers_are_reused():
    route = respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))
    run("8.8.8.8")
    run("8.8.8.8")
    assert route.call_count == 1


# ─────────────────────────── reputation (AbuseIPDB) ───────────────────────────

ABUSE = cfg.settings.iplookup_abuseipdb_url
ABUSE_ANSWER = {"data": {
    "ipAddress": "8.8.8.8", "isPublic": True, "ipVersion": 4, "isWhitelisted": True,
    "abuseConfidenceScore": 0, "countryCode": "US", "usageType": "Content Delivery Network",
    "isp": "Google LLC", "domain": "google.com", "hostnames": ["dns.google"], "isTor": False,
    "totalReports": 157, "numDistinctUsers": 41, "lastReportedAt": "2026-10-05T18:36:46+00:00",
}}


@pytest.fixture()
def abuse(monkeypatch):
    from app.services import sourcehealth

    monkeypatch.setattr(svc.settings, "iplookup_abuseipdb_key", "test-key")
    svc._abuse_cache.clear()
    svc._abuse_pause["until"] = 0.0
    sourcehealth.reset()
    yield
    svc._abuse_cache.clear()
    svc._abuse_pause["until"] = 0.0


def _base_routes():
    respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))


@respx.mock(assert_all_called=False)
def test_reputation_is_off_without_a_key(respx_mock):
    rep = respx_mock.get(url__startswith=ABUSE)
    respx_mock.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx_mock.get(TOR).mock(return_value=httpx.Response(200, text=""))
    res = run("8.8.8.8")
    assert not rep.called
    assert res.results[0].reputation is None and "reputation" not in res.results[0].errors
    assert "reputation" not in {s.key for s in res.sources}
    assert not any("AbuseIPDB" in a for a in res.attribution)


@respx.mock
def test_reputation_comes_back_with_the_key(abuse):
    _base_routes()
    route = respx.get(url__startswith=ABUSE).mock(return_value=httpx.Response(200, json=ABUSE_ANSWER))
    res = run("8.8.8.8")
    req = route.calls.last.request
    assert req.headers["Key"] == "test-key"
    assert req.url.params["ipAddress"] == "8.8.8.8" and req.url.params["maxAgeInDays"] == "90"
    rep = res.results[0].reputation
    assert rep.abuse_score == 0 and rep.total_reports == 157 and rep.distinct_reporters == 41
    assert rep.usage_type == "Content Delivery Network" and rep.whitelisted is True
    assert rep.max_age_days == 90
    assert {s.key: s.ok for s in res.sources}["reputation"] is True
    assert any("AbuseIPDB" in a for a in res.attribution)


@respx.mock
def test_a_spent_quota_stands_down_until_the_reset(abuse):
    import time

    _base_routes()
    route = respx.get(url__startswith=ABUSE).mock(return_value=httpx.Response(
        429, headers={"X-RateLimit-Reset": str(int(time.time()) + 3600)}))
    first = run("8.8.8.8").results[0]
    assert "used up" in first.errors["reputation"]
    svc._rdap_cache.clear()
    second = run("8.8.8.8").results[0]
    assert "used up" in second.errors["reputation"]
    assert route.call_count == 1  # the second lookup didn't spend a call to hear "no" again
    assert second.registration.org == "Google LLC"  # and the rest still answered


@respx.mock
def test_a_refused_key_is_reported_and_scored(abuse):
    from app.services import sourcehealth

    _base_routes()
    respx.get(url__startswith=ABUSE).mock(return_value=httpx.Response(401))
    res = run("8.8.8.8")
    assert "key was refused" in res.results[0].errors["reputation"]
    assert {s.key: s.ok for s in res.sources}["reputation"] is False
    stat = {(s["tool"], s["key"]): s for s in sourcehealth.snapshot()}[("ip", "abuseipdb")]
    assert stat["failed"] == 1 and "refused" in stat["last_error"]


@respx.mock
def test_reputation_answers_are_reused(abuse):
    _base_routes()
    route = respx.get(url__startswith=ABUSE).mock(return_value=httpx.Response(200, json=ABUSE_ANSWER))
    run("8.8.8.8")
    run("8.8.8.8")
    assert route.call_count == 1


@respx.mock
def test_reputation_alone_is_an_answer(abuse, monkeypatch):
    monkeypatch.setattr(svc._CITY, "get", lambda: None)
    monkeypatch.setattr(svc._ASN, "get", lambda: None)
    respx.get(RDAP + "8.8.8.8").mock(side_effect=httpx.ConnectError("down"))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))
    respx.get(url__startswith=ABUSE).mock(return_value=httpx.Response(200, json=ABUSE_ANSWER))
    assert svc.answered(run("8.8.8.8"))


@respx.mock
def test_a_hostname_looks_up_its_addresses_v4_first(monkeypatch):
    monkeypatch.setattr(svc.settings, "iplookup_max_addresses", 2)

    async def fake_getaddrinfo(self, host, port, **kw):
        assert host == "example.com"
        return [(10, 1, 6, "", ("2606:2800:21f::1", 0, 0, 0)),
                (2, 1, 6, "", ("8.8.8.8", 0)),
                (2, 1, 6, "", ("8.8.8.8", 0)),
                (2, 1, 6, "", ("10.0.0.1", 0))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)
    respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))
    res = run("https://example.com/")
    assert res.kind == "hostname" and res.hostname == "example.com"
    assert [r.ip for r in res.results] == ["8.8.8.8", "10.0.0.1"]
    assert res.more_addresses == ["2606:2800:21f::1"]


# ─────────────────────────── the endpoint ───────────────────────────

_seq = [0]


def _user(tier="free"):
    _seq[0] += 1
    db.get_conn()
    return users.create(f"ip{_seq[0]}@example.test", "a-long-enough-password", tier=tier)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def test_needs_a_session(client):
    assert client.post("/api/v1/ip/lookup", json={"target": "8.8.8.8"}).status_code == 401


@respx.mock
def test_a_lookup_is_one_search(client):
    respx.get(RDAP + "8.8.8.8").mock(return_value=httpx.Response(200, json=ARIN))
    respx.get(TOR).mock(return_value=httpx.Response(200, text=""))
    u = _user()
    r = _signed_in(client, u).post("/api/v1/ip/lookup", json={"target": "8.8.8.8"})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["network"]["as_org"] == "Google LLC"
    assert usage.status(u)["used"] == 1


def test_bad_target_is_refused_uncharged(client):
    u = _user()
    r = _signed_in(client, u).post("/api/v1/ip/lookup", json={"target": "not an ip"})
    assert r.status_code == 400 and usage.status(u)["used"] == 0


def test_unresolvable_hostname_is_refused_uncharged(client, monkeypatch):
    import socket

    async def nx(self, host, port, **kw):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", nx)
    u = _user()
    r = _signed_in(client, u).post("/api/v1/ip/lookup", json={"target": "nothing.example"})
    assert r.status_code == 400 and "does not resolve" in r.json()["detail"]
    assert usage.status(u)["used"] == 0


@respx.mock(assert_all_called=False)
def test_private_only_is_refunded(client, respx_mock):
    respx_mock.get(TOR).mock(return_value=httpx.Response(200, text=""))
    u = _user()
    r = _signed_in(client, u).post("/api/v1/ip/lookup", json={"target": "10.0.0.1"})
    assert r.status_code == 200 and r.json()["results"][0]["scope"] == "private"
    assert usage.status(u)["used"] == 0


def test_a_crash_is_refunded(client, monkeypatch):
    async def boom(target):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "lookup", boom)
    u = _user()
    with pytest.raises(RuntimeError):
        _signed_in(client, u).post("/api/v1/ip/lookup", json={"target": "8.8.8.8"})
    assert usage.status(u)["used"] == 0


# ─────────────────────────── DB-IP updates ───────────────────────────

class _FakeDb:
    def __init__(self, dbtype):
        self.dbtype = dbtype

    def metadata(self):
        return SimpleNamespace(database_type=self.dbtype, build_epoch=0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def geo(tmp_path, monkeypatch):
    # svc.settings, not cfg.settings: other test modules rebind cfg.settings,
    # and a patch that misses would point the updater at the real data/geoip.
    monkeypatch.setattr(svc.settings, "geoip_dir", str(tmp_path))
    assert svc.geo_dir() == tmp_path
    monkeypatch.setattr(svc, "_now", lambda: datetime(2026, 10, 5, tzinfo=timezone.utc))

    def fake_open(path):
        # The "database" is a text file naming its own type.
        with open(path) as f:
            return _FakeDb(f.read().strip())

    import maxminddb

    monkeypatch.setattr(maxminddb, "open_database", fake_open)
    return tmp_path


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode())


def _url(kind, y, m):
    return svc.DBIP_URL.format(kind=kind, year=y, month=m)


@respx.mock
def test_first_install_falls_back_to_last_month(geo):
    respx.get(_url("city", 2026, 10)).mock(return_value=httpx.Response(404))
    respx.get(_url("city", 2026, 9)).mock(return_value=httpx.Response(200, content=_gz("DBIP-City-Lite")))
    respx.get(_url("asn", 2026, 10)).mock(return_value=httpx.Response(200, content=_gz("DBIP-ASN-Lite")))
    report = asyncio.run(svc.update_databases())
    assert report == {"city": "installed 2026-09", "asn": "installed 2026-10"}
    assert (geo / "dbip-city-lite.mmdb").read_text() == "DBIP-City-Lite"
    assert (geo / "dbip-city-lite.mmdb.edition").read_text().strip() == "2026-09"
    assert not list(geo.glob("*.part"))


@respx.mock(assert_all_called=False)
def test_current_edition_is_not_downloaded_again(geo, respx_mock):
    for kind in ("city", "asn"):
        (geo / svc.DBIP_FILES[kind]).write_text("x")
        (geo / (svc.DBIP_FILES[kind] + ".edition")).write_text("2026-10\n")
    route = respx_mock.get(url__startswith="https://download.db-ip.com/")
    report = asyncio.run(svc.update_databases())
    assert report["city"] == "current (2026-10)" and not route.called


@respx.mock
def test_a_bad_download_keeps_the_old_file(geo):
    for kind in ("city", "asn"):
        (geo / svc.DBIP_FILES[kind]).write_text(f"old {kind}")
        (geo / (svc.DBIP_FILES[kind] + ".edition")).write_text("2026-09\n")
    respx.get(_url("city", 2026, 10)).mock(return_value=httpx.Response(200, content=_gz("GeoLite2-Country")))
    respx.get(_url("asn", 2026, 10)).mock(return_value=httpx.Response(200, content=b"not gzip"))
    report = asyncio.run(svc.update_databases())
    assert report["city"].startswith("failed") and report["asn"].startswith("failed")
    assert (geo / "dbip-city-lite.mmdb").read_text() == "old city"
    assert (geo / "dbip-asn-lite.mmdb").read_text() == "old asn"
    assert not list(geo.glob("*.part"))


@respx.mock(assert_all_called=False)
def test_geolite2_present_means_no_download(geo, respx_mock):
    (geo / "GeoLite2-City.mmdb").write_text("GeoLite2-City")
    (geo / "GeoLite2-ASN.mmdb").write_text("GeoLite2-ASN")
    route = respx_mock.get(url__startswith="https://download.db-ip.com/")
    report = asyncio.run(svc.update_databases())
    assert all(v.startswith("skipped") for v in report.values()) and not route.called

"""Domain / website lookup: DNS, mail protection, RDAP, certificate logs, the
website itself, hosting and the archive.

What matters: a target is read the way people paste it (domain, URL, email),
DNS answers from either resolver read the same, the registered domain is found
even behind a CNAME, mail protection is graded honestly, a source that fails
is reported rather than passed off as "nothing found", crt.sh falling over
hands off to Cert Spotter, the website fetch never connects to a private
address (by the name or by a redirect), and a lookup that learned nothing is
not charged.
"""

import asyncio
import os
import tempfile
import time

import httpx
import pytest
import respx

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "domainlookup.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["SIGNUP_DEFAULT_STATUS"] = "active"
os.environ["IPLOOKUP_AUTO_UPDATE"] = "false"
os.environ["IPLOOKUP_ABUSEIPDB_KEY"] = ""
os.environ["LEAKS_DB"] = os.path.join(tempfile.mkdtemp(), "leak_datasets.db")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.models import DomainTls  # noqa: E402
from app.services import domainlookup as svc  # noqa: E402
from app.services import iplookup, sourcehealth, usage, users  # noqa: E402

svc.settings = cfg.settings
iplookup.settings = cfg.settings

GOOGLE = "https://dns.google/resolve"
CLOUDFLARE = "https://cloudflare-dns.com/dns-query"
RDAP = cfg.settings.domain_rdap_url
CRTSH = "https://crt.sh/"
CERTSPOTTER = "https://api.certspotter.com/v1/issuances"
WAYBACK = "https://web.archive.org/cdx/search/cdx"


# ─────────────────────────── a fake DNS ───────────────────────────

SOA = "ns1.example.com. hostmaster.example.com. 2026100601 7200 3600 1209600 300"

ZONE: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
NXDOMAIN: set[str] = set()
APEX: dict[str, str] = {}


def base_zone():
    ZONE.clear()
    NXDOMAIN.clear()
    APEX.clear()
    ZONE.update({
        ("example.com", "A"): [("example.com.", 1, "93.184.216.34")],
        ("example.com", "MX"): [("example.com.", 15, "20 mx2.example.net."),
                                ("example.com.", 15, "10 mx1.example.net.")],
        ("example.com", "NS"): [("example.com.", 2, "ns1.example.com.")],
        ("example.com", "TXT"): [("example.com.", 16, "v=spf1 include:_spf.example.net -all"),
                                 ("example.com.", 16, "google-site-verification=abc")],
        ("example.com", "CAA"): [("example.com.", 257, '0 issue "letsencrypt.org"')],
        ("example.com", "SOA"): [("example.com.", 6, SOA)],
        ("_dmarc.example.com", "TXT"): [
            ("_dmarc.example.com.", 16, "v=DMARC1; p=reject; rua=mailto:dmarc@example.com")],
    })
    APEX.update({"example.com": "example.com", "www.example.com": "example.com"})


def doh(request: httpx.Request) -> httpx.Response:
    name, rtype = request.url.params["name"], request.url.params["type"]
    if name in NXDOMAIN:
        return httpx.Response(200, json={
            "Status": 3, "AD": False,
            "Authority": [{"name": "com.", "type": 6, "TTL": 900, "data": "a.gtld-servers.net. x 1 2 3 4 5"}]})
    rows = ZONE.get((name, rtype), [])
    # A CNAME answers every type, the way a resolver follows it.
    if not rows and rtype != "CNAME" and ZONE.get((name, "CNAME")):
        cname = ZONE[(name, "CNAME")]
        target = cname[-1][2].rstrip(".")
        rows = cname + ZONE.get((target, rtype), [])
    out = {"Status": 0, "AD": True,
           "Answer": [{"name": n, "type": t, "TTL": 300, "data": d} for n, t, d in rows]}
    if not any(t == RTYPE[rtype] for _, t, _ in rows):
        apex = APEX.get(name)
        if apex:
            out["Authority"] = [{"name": apex + ".", "type": 6, "TTL": 300, "data": SOA}]
    return httpx.Response(200, json=out)


RTYPE = svc.RTYPES

RDAP_EXAMPLE = {
    "objectClassName": "domain", "ldhName": "EXAMPLE.COM",
    "status": ["client delete prohibited", "client transfer prohibited"],
    "events": [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
               {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
               {"eventAction": "last changed", "eventDate": "2026-08-14T08:01:43Z"}],
    "nameservers": [{"ldhName": "NS1.EXAMPLE.COM"}, {"ldhName": "ns2.example.com"}],
    "secureDNS": {"delegationSigned": True},
    "entities": [
        {"roles": ["registrar"], "publicIds": [{"type": "IANA Registrar ID", "identifier": "1068"}],
         "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "NameCheap, Inc."]]],
         "entities": [{"roles": ["abuse"], "vcardArray": ["vcard", [
             ["version", {}, "text", "4.0"], ["fn", {}, "text", "Abuse"],
             ["email", {}, "text", "abuse@namecheap.com"]]]}]},
        {"roles": ["registrant"], "vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"], ["fn", {}, "text", "REDACTED FOR PRIVACY"]]]},
    ],
}

CRT_ROWS = [
    {"common_name": "example.com", "name_value": "example.com\nwww.example.com"},
    {"common_name": "*.example.com", "name_value": "*.example.com\nmail.example.com"},
    {"common_name": "evil.com", "name_value": "example.com.evil.com"},  # not ours
]

HTML = b"""<!doctype html><html><head><title>Example &amp; Co</title>
<meta content="WordPress 6.6" name="generator"></head><body>hi</body></html>"""


def site_routes(ip="93.184.216.34", headers=None):
    h = {"content-type": "text/html; charset=utf-8", "server": "nginx",
         "strict-transport-security": "max-age=63072000", "x-content-type-options": "nosniff"}
    h.update(headers or {})
    respx.get(f"https://{ip}/").mock(return_value=httpx.Response(200, headers=h, content=HTML))
    respx.get(f"http://{ip}/").mock(return_value=httpx.Response(
        301, headers={"location": "https://example.com/"}))


def dns_routes():
    respx.get(url__startswith=GOOGLE).mock(side_effect=doh)
    respx.get(url__startswith=CLOUDFLARE).mock(side_effect=doh)


def all_routes(**kw):
    dns_routes()
    respx.get(RDAP + "example.com").mock(return_value=httpx.Response(200, json=RDAP_EXAMPLE))
    respx.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=CRT_ROWS))
    respx.get(url__startswith=WAYBACK).mock(side_effect=wayback)
    site_routes(**kw)


def wayback(request: httpx.Request) -> httpx.Response:
    if request.url.params["limit"] == "1":
        return httpx.Response(200, json=[["timestamp", "original"], ["20020120142510", "http://example.com:80/"]])
    return httpx.Response(200, json=[["timestamp", "original"], ["20261005041359", "https://example.com/"]])


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    base_zone()
    svc.clear_caches()
    sourcehealth.reset()
    monkeypatch.setattr(iplookup._CITY, "get", lambda: None)
    monkeypatch.setattr(iplookup._ASN, "get", lambda: None)

    async def tls(host, ip):
        return DomainTls(valid=True, version="TLSv1.3", subject=host, issuer="Let's Encrypt · E5",
                         days_left=60, names=[host])

    monkeypatch.setattr(svc, "_tls", tls)


def run(target: str):
    return asyncio.run(svc.lookup(svc.parse_target(target)))


# ─────────────────────────── parsing ───────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("example.com", "example.com"),
    ("  Example.COM. ", "example.com"),
    ("https://www.example.com/login?next=/", "www.example.com"),
    ("example.com/some/path", "example.com"),
    ("example.com:8443", "example.com"),
    ("alice@Example.com", "example.com"),
    ("bücher.de", "xn--bcher-kva.de"),
])
def test_targets_are_read_the_way_people_paste_them(raw, want):
    assert svc.parse_target(raw) == want


@pytest.mark.parametrize("raw", ["", "localhost", "foo..com", "-bad.com", "http://", "a b.com", "example.123"])
def test_nonsense_is_refused(raw):
    with pytest.raises(ValueError):
        svc.parse_target(raw)


@pytest.mark.parametrize("raw", ["8.8.8.8", "2606:4700::1111", "https://1.1.1.1/"])
def test_an_ip_address_points_at_the_ip_tool(raw):
    with pytest.raises(ValueError, match="IP tool"):
        svc.parse_target(raw)


# ─────────────────────────── DNS parsing ───────────────────────────

def test_txt_reads_the_same_from_both_resolvers():
    assert svc._txt("v=spf1 -all") == "v=spf1 -all"  # Google
    assert svc._txt('"v=spf1 -all"') == "v=spf1 -all"  # Cloudflare
    assert svc._txt('"v=spf1 include:a.example " "include:b.example -all"') == \
        "v=spf1 include:a.example include:b.example -all"
    assert svc._txt(r'"say \"hi\""') == 'say "hi"'


def test_caa_generic_form_is_decoded():
    raw = r"\# 22 00 05 69 73 73 75 65 6c 65 74 73 65 6e 63 72 79 70 74 2e 6f 72 67"
    assert svc._caa(raw) == '0 issue "letsencrypt.org"'
    assert svc._caa('0 issue "letsencrypt.org"') == '0 issue "letsencrypt.org"'


def test_zone_apex_is_read_from_soa_but_not_through_a_cname():
    own = {"Answer": [{"name": "example.com.", "type": 6, "data": SOA}]}
    assert svc._apex_from("example.com", own) == "example.com"
    nodata = {"Authority": [{"name": "example.com.", "type": 6, "data": SOA}]}
    assert svc._apex_from("www.example.com", nodata) == "example.com"
    via_cname = {"Answer": [{"name": "www.example.com.", "type": 5, "data": "x.cdn.net."}],
                 "Authority": [{"name": "cdn.net.", "type": 6, "data": SOA}]}
    assert svc._apex_from("www.example.com", via_cname) is None
    nx = {"Authority": [{"name": "com.", "type": 6, "data": SOA}]}
    assert svc._apex_from("nope.com", nx) is None


# ─────────────────────────── mail protection ───────────────────────────

def rec(t, v, name="example.com"):
    from app.models import DnsRecord
    return DnsRecord(type=t, name=name, value=v)


@pytest.mark.parametrize("spf,dmarc,want", [
    ("v=spf1 mx -all", "v=DMARC1; p=reject", "strong"),
    ("v=spf1 mx ~all", "v=DMARC1; p=quarantine", "strong"),
    ("v=spf1 mx ~all", "v=DMARC1; p=none", "partial"),
    ("v=spf1 mx -all", None, "partial"),
    (None, "v=DMARC1; p=reject", "partial"),
    ("v=spf1 mx ?all", None, "weak"),
    ("v=spf1 +all", None, "weak"),
    (None, None, "none"),
])
def test_mail_protection_is_graded(spf, dmarc, want):
    e = svc.email_security(
        [rec("MX", "10 mx.example.com")],
        [rec("TXT", spf)] if spf else [],
        [rec("TXT", dmarc, "_dmarc.example.com")] if dmarc else [], [],
    )
    assert e.protection == want


def test_mail_details_are_read():
    e = svc.email_security(
        [rec("MX", "20 b.example.net"), rec("MX", "10 a.example.net")],
        [rec("TXT", "v=spf1 include:x ~all")],
        [rec("TXT", "v=DMARC1; p=quarantine; sp=reject; pct=50; rua=mailto:a@x.com,mailto:b@x.com",
             "_dmarc.example.com")],
        [rec("TXT", "v=STSv1; id=1", "_mta-sts.example.com")],
    )
    assert e.mx == ["10 a.example.net", "20 b.example.net"]
    assert e.spf_all == "~all" and e.dmarc_policy == "quarantine" and e.dmarc_subdomain_policy == "reject"
    assert e.dmarc_pct == 50 and e.dmarc_reports == ["mailto:a@x.com", "mailto:b@x.com"]
    assert e.mta_sts is True
    assert e.protection == "partial"  # pct=50 isn't enforcement
    assert any("50%" in n for n in e.notes)


def test_two_spf_records_are_an_error():
    e = svc.email_security([], [rec("TXT", "v=spf1 -all"), rec("TXT", "v=spf1 mx -all")], [], [])
    assert e.spf_count == 2 and any("permerror" in n for n in e.notes)


def test_a_domain_that_takes_no_mail():
    e = svc.email_security([rec("MX", "0 .")], [rec("TXT", "v=spf1 -all")],
                           [rec("TXT", "v=DMARC1; p=reject", "_dmarc.example.com")], [])
    assert e.null_mx and e.protection == "strong"


# ─────────────────────────── RDAP ───────────────────────────

def test_rdap_registration_is_read():
    reg = svc.parse_rdap(RDAP_EXAMPLE, "rdap.verisign.com")
    assert reg.domain == "example.com" and reg.registry == "rdap.verisign.com"
    assert reg.registrar == "NameCheap, Inc." and reg.registrar_iana_id == "1068"
    assert reg.registrar_abuse_email == "abuse@namecheap.com"
    assert reg.registrant is None and reg.registrant_redacted is True
    assert reg.created.startswith("1995") and reg.expires.startswith("2027") and reg.updated.startswith("2026")
    assert reg.nameservers == ["ns1.example.com", "ns2.example.com"]
    assert reg.dnssec is True and "client transfer prohibited" in reg.status


def test_a_published_registrant_is_kept():
    data = dict(RDAP_EXAMPLE, entities=[{"roles": ["registrant"], "vcardArray": ["vcard", [
        ["version", {}, "text", "4.0"], ["fn", {}, "text", ""], ["org", {}, "text", "Example Org"]]]}])
    reg = svc.parse_rdap(data)
    assert reg.registrant == "Example Org" and not reg.registrant_redacted


# ─────────────────────────── the lookup ───────────────────────────

@respx.mock
def test_a_domain_gets_everything():
    all_routes()
    res = run("example.com")
    assert res.exists is True and res.dnssec_validated is True
    assert res.registered_domain == "example.com"
    assert not res.errors, res.errors
    types = {r.type for r in res.dns}
    assert {"A", "MX", "NS", "TXT", "CAA", "SOA"} <= types

    assert res.email.protection == "strong" and res.email.mx[0] == "10 mx1.example.net"
    assert res.email.dmarc_policy == "reject" and res.email.dmarc_inherited_from is None

    assert res.registration.registrar == "NameCheap, Inc."
    assert res.certificates.source == "crt.sh"
    assert res.certificates.subdomains == ["mail.example.com", "www.example.com"]

    w = res.website
    assert w.status == 200 and w.ip == "93.184.216.34" and w.final_url == "https://example.com/"
    assert w.title == "Example & Co" and w.generator == "WordPress 6.6" and w.server == "nginx"
    assert w.security_headers["strict-transport-security"] == "max-age=63072000"
    assert w.security_headers["content-security-policy"] is None
    assert w.https_redirect is True
    assert w.tls.valid and w.tls.issuer.startswith("Let's Encrypt")
    req = respx.calls[[c.request.url.host for c in respx.calls].index("93.184.216.34")].request
    assert req.headers["host"] == "example.com"  # pinned to the address, named for the site

    assert res.archive.first.startswith("2002-01-20") and res.archive.last.startswith("2026-10-05")
    assert [a.ip for a in res.addresses] == ["93.184.216.34"]
    assert all(s.ok for s in res.sources)
    assert svc.answered(res)


@respx.mock
def test_a_subdomain_behind_a_cname_finds_its_registered_domain():
    all_routes(ip="151.101.0.81", headers={})
    ZONE[("www.example.com", "CNAME")] = [("www.example.com.", 5, "example.map.fastly.net.")]
    ZONE[("example.map.fastly.net", "A")] = [("example.map.fastly.net.", 1, "151.101.0.81")]
    respx.get(RDAP + "www.example.com").mock(return_value=httpx.Response(404))
    res = run("www.example.com")
    assert res.registered_domain == "example.com"
    assert res.registration and res.registration.domain == "example.com"
    assert not any(c.request.url == RDAP + "www.example.com" for c in respx.calls)
    assert res.email.dmarc_inherited_from == "example.com"  # _dmarc.www has none
    assert res.addresses[0].ip == "151.101.0.81"


@respx.mock
def test_a_name_that_does_not_exist_learns_nothing():
    dns_routes()
    NXDOMAIN.add("nope-nope.com")
    respx.get(RDAP + "nope-nope.com").mock(return_value=httpx.Response(404))
    respx.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(200, text=""))
    res = run("nope-nope.com")
    assert res.exists is False and res.registration is None and res.email is None
    assert res.website.note and "nothing to fetch" in res.website.note
    assert not res.errors
    assert not svc.answered(res)


@respx.mock
def test_dns_falls_back_to_the_second_resolver():
    respx.get(url__startswith=GOOGLE).mock(return_value=httpx.Response(503))
    respx.get(url__startswith=CLOUDFLARE).mock(side_effect=doh)
    respx.get(RDAP + "example.com").mock(return_value=httpx.Response(200, json=RDAP_EXAMPLE))
    respx.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=CRT_ROWS))
    respx.get(url__startswith=WAYBACK).mock(side_effect=wayback)
    site_routes()
    res = run("example.com")
    assert res.exists is True and "dns" not in res.errors
    stats = {(s["tool"], s["key"]): s for s in sourcehealth.snapshot()}
    assert stats[("domain", "doh_google")]["failed"] > 0
    assert stats[("domain", "doh_cloudflare")]["ok"] > 0


@respx.mock(assert_all_called=False)
def test_every_source_down_is_reported_and_learns_nothing(respx_mock):
    respx_mock.get(url__startswith=GOOGLE).mock(side_effect=httpx.ConnectError("down"))
    respx_mock.get(url__startswith=CLOUDFLARE).mock(side_effect=httpx.ConnectError("down"))
    respx_mock.get(url__startswith=RDAP).mock(return_value=httpx.Response(503))
    respx_mock.get(url__startswith=CRTSH).mock(return_value=httpx.Response(502))
    respx_mock.get(url__startswith=CERTSPOTTER).mock(return_value=httpx.Response(500))
    respx_mock.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(503))
    res = run("example.com")
    assert res.exists is None
    assert {"dns", "rdap", "certificates", "archive"} <= set(res.errors)
    assert not {s.key: s.ok for s in res.sources}["dns"]
    assert not svc.answered(res)


@respx.mock
def test_crtsh_down_hands_off_to_cert_spotter_and_then_rests():
    all_routes()
    crt = respx.get(url__startswith=CRTSH).mock(return_value=httpx.Response(502))
    spotter = respx.get(url__startswith=CERTSPOTTER).mock(return_value=httpx.Response(
        200, json=[{"dns_names": ["example.com", "api.example.com"]}],
        headers={"link": '<https://api.certspotter.com/issuances?after=1>; rel="next"'}))
    res = run("example.com")
    assert res.certificates.source == "Cert Spotter" and res.certificates.subdomains == ["api.example.com"]
    assert res.certificates.partial is True
    runs = 1
    for _ in range(sourcehealth.DOWN_AFTER + 2):  # crt.sh keeps failing...
        svc.clear_caches()
        run("example.com")
        runs += 1
    # ...so after DOWN_AFTER failures in a row it is left alone for a while,
    # and Cert Spotter answers every lookup.
    assert crt.call_count == sourcehealth.DOWN_AFTER
    assert spotter.call_count == runs


@respx.mock
def test_both_certificate_logs_down_says_so():
    all_routes()
    respx.get(url__startswith=CRTSH).mock(return_value=httpx.Response(502))
    respx.get(url__startswith=CERTSPOTTER).mock(return_value=httpx.Response(429))
    res = run("example.com")
    assert res.certificates is None
    assert "crt.sh" in res.errors["certificates"] and "Cert Spotter" in res.errors["certificates"]
    assert svc.answered(res)  # DNS and the registry still answered


@respx.mock
def test_answers_are_reused():
    all_routes()
    run("example.com")
    run("example.com")
    assert respx.routes  # noqa
    rdap_calls = sum(1 for c in respx.calls if c.request.url.host == "rdap.org")
    crt_calls = sum(1 for c in respx.calls if c.request.url.host == "crt.sh")
    assert rdap_calls == 1 and crt_calls == 1


@respx.mock
def test_a_half_read_archive_says_so():
    all_routes()

    def flaky(request):
        if request.url.params["limit"] == "1":
            raise httpx.ReadTimeout("slow")
        return wayback(request)

    respx.get(url__startswith=WAYBACK).mock(side_effect=flaky)
    res = run("example.com")
    assert res.archive.first is None and res.archive.last.startswith("2026")
    assert "first capture" in res.archive.note


# ─────────────────────────── the website stays on the internet ───────────────────────────

@respx.mock(assert_all_called=False)
def test_a_name_on_a_private_address_is_never_fetched(respx_mock):
    respx_mock.get(url__startswith=GOOGLE).mock(side_effect=doh)
    ZONE[("example.com", "A")] = [("example.com.", 1, "127.0.0.1")]
    ZONE[("example.com", "AAAA")] = [("example.com.", 28, "::1")]
    respx_mock.get(RDAP + "example.com").mock(return_value=httpx.Response(200, json=RDAP_EXAMPLE))
    respx_mock.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=[]))
    respx_mock.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(200, text=""))
    local = respx_mock.get(url__regex=r"https?://(127\.0\.0\.1|\[::1\])")
    res = run("example.com")
    assert not local.called
    assert res.website.status is None and "private" in res.website.note
    assert [a.scope for a in res.addresses] == ["loopback", "loopback"]


@respx.mock(assert_all_called=False)
def test_a_redirect_to_a_private_address_is_not_followed(respx_mock):
    respx_mock.get(url__startswith=GOOGLE).mock(side_effect=doh)
    ZONE[("internal.example.com", "A")] = [("internal.example.com.", 1, "10.0.0.5")]
    respx_mock.get(RDAP + "example.com").mock(return_value=httpx.Response(200, json=RDAP_EXAMPLE))
    respx_mock.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=[]))
    respx_mock.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(200, text=""))
    respx_mock.get("https://93.184.216.34/").mock(return_value=httpx.Response(
        302, headers={"location": "http://internal.example.com/admin"}))
    respx_mock.get("http://93.184.216.34/").mock(return_value=httpx.Response(200))
    inside = respx_mock.get(url__startswith="http://10.0.0.5")
    res = run("example.com")
    assert not inside.called
    assert res.website.redirects[0].status == 302
    assert "no public address" in res.website.note


@pytest.mark.parametrize("location", ["http://127.0.0.1/", "http://[::1]:8080/", "http://169.254.169.254/latest/"])
@respx.mock(assert_all_called=False)
def test_a_redirect_to_an_address_literal_inside_is_not_followed(location, respx_mock):
    respx_mock.get(url__startswith=GOOGLE).mock(side_effect=doh)
    respx_mock.get(RDAP + "example.com").mock(return_value=httpx.Response(200, json=RDAP_EXAMPLE))
    respx_mock.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=[]))
    respx_mock.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(200, text=""))
    respx_mock.get("https://93.184.216.34/").mock(return_value=httpx.Response(301, headers={"location": location}))
    respx_mock.get("http://93.184.216.34/").mock(return_value=httpx.Response(200))
    inside = respx_mock.get(url__regex=r"https?://(127\.|\[::1\]|169\.254\.)")
    res = run("example.com")
    assert not inside.called and "no public address" in res.website.note


@respx.mock(assert_all_called=False)
def test_a_redirect_to_an_odd_port_is_not_followed(respx_mock):
    respx_mock.get(url__startswith=GOOGLE).mock(side_effect=doh)
    respx_mock.get(RDAP + "example.com").mock(return_value=httpx.Response(200, json=RDAP_EXAMPLE))
    respx_mock.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=[]))
    respx_mock.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(200, text=""))
    respx_mock.get("https://93.184.216.34/").mock(return_value=httpx.Response(
        301, headers={"location": "http://example.com:6379/"}))
    respx_mock.get("http://93.184.216.34/").mock(return_value=httpx.Response(200))
    odd = respx_mock.get(url__startswith="http://93.184.216.34:6379")
    res = run("example.com")
    assert not odd.called and "stopped at a redirect" in res.website.note


@respx.mock
def test_a_redirect_chain_is_followed_and_recorded():
    all_routes()
    ZONE[("www.example.com", "A")] = [("www.example.com.", 1, "93.184.216.35")]
    respx.get("https://93.184.216.34/").mock(return_value=httpx.Response(
        301, headers={"location": "https://www.example.com/"}))
    respx.get("https://93.184.216.35/").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/html"}, content=HTML))
    res = run("example.com")
    w = res.website
    assert [h.status for h in w.redirects] == [301]
    assert w.final_url == "https://www.example.com/" and w.status == 200 and w.title == "Example & Co"


# ─────────────────────────── the endpoint ───────────────────────────

_seq = [0]


def _user(tier="free"):
    _seq[0] += 1
    db.get_conn()
    return users.create(f"dom{_seq[0]}@example.test", "a-long-enough-password", tier=tier)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _signed_in(client, user):
    client.cookies.set(cfg.settings.session_cookie, users.create_session(user["id"]))
    return client


def test_needs_a_session(client):
    assert client.post("/api/v1/domain/lookup", json={"target": "example.com"}).status_code == 401


@respx.mock
def test_a_lookup_is_one_search(client):
    all_routes()
    u = _user()
    r = _signed_in(client, u).post("/api/v1/domain/lookup", json={"target": "https://example.com/x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domain"] == "example.com" and body["query"] == "https://example.com/x"
    assert body["registration"]["registrar"] == "NameCheap, Inc."
    assert usage.status(u)["used"] == 1


def test_bad_target_is_refused_uncharged(client):
    u = _user()
    r = _signed_in(client, u).post("/api/v1/domain/lookup", json={"target": "not a domain"})
    assert r.status_code == 400 and usage.status(u)["used"] == 0
    r = _signed_in(client, u).post("/api/v1/domain/lookup", json={"target": "8.8.8.8"})
    assert r.status_code == 400 and "IP tool" in r.json()["detail"]
    assert usage.status(u)["used"] == 0


@respx.mock
def test_nothing_learned_is_refunded(client):
    dns_routes()
    NXDOMAIN.add("nope-nope.com")
    respx.get(RDAP + "nope-nope.com").mock(return_value=httpx.Response(404))
    respx.get(url__startswith=CRTSH).mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__startswith=WAYBACK).mock(return_value=httpx.Response(200, text=""))
    u = _user()
    r = _signed_in(client, u).post("/api/v1/domain/lookup", json={"target": "nope-nope.com"})
    assert r.status_code == 200 and r.json()["exists"] is False
    assert usage.status(u)["used"] == 0


def test_a_crash_is_refunded(client, monkeypatch):
    async def boom(host):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "lookup", boom)
    u = _user()
    with pytest.raises(RuntimeError):
        _signed_in(client, u).post("/api/v1/domain/lookup", json={"target": "example.com"})
    assert usage.status(u)["used"] == 0


def test_the_slow_sources_have_their_own_deadline(monkeypatch):
    async def forever(*a, **kw):
        await asyncio.sleep(60)

    async def go():
        t = time.monotonic()
        with pytest.raises(svc.LookupFailed, match="timed out"):
            await svc._deadline(forever(), 0.05, "certificate logs")
        return time.monotonic() - t

    assert asyncio.run(go()) < 1

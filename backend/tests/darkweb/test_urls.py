from urllib.parse import quote

import pytest

from app.services.darkweb.urls import canonicalize, display_url, extract_embedded_url, unwrap

from .conftest import fake_onion

SITE = fake_onion("site")
AHMIA = "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"


def test_ahmia_unencoded_redirect_tail_keeps_ampersands():
    href = f"/search/redirect?search_term=bitcoin&redirect_url=http://{SITE}/page.php?a=1&b=2"
    assert unwrap(href, "https://ahmia.fi/search/?q=bitcoin", {"ahmia.fi"}) == f"http://{SITE}/page.php?a=1&b=2"


def test_ahmia_redirect_on_onion_mirror_is_unwrapped():
    href = f"http://{AHMIA}/search/redirect?search_term=x&redirect_url=http://{SITE}/"
    assert unwrap(href, None, {AHMIA}) == f"http://{SITE}/"


def test_haystak_url_param():
    href = "/redirect.php?url=" + quote(f"http://{SITE}/a/b?c=d", safe="")
    assert unwrap(href, "http://haystak.onion/?q=x") == f"http://{SITE}/a/b?c=d"


def test_onionengine_open_wrapper():
    href = "https://onionengine.com/open?u=" + quote(f"http://{SITE}/x", safe="")
    assert unwrap(href, None, {"onionengine.com"}) == f"http://{SITE}/x"


def test_bare_onion_value_gets_scheme():
    assert extract_embedded_url(f"https://engine.example/r?l={SITE}/x") == f"http://{SITE}/x"


def test_direct_links_to_other_sites_are_not_rewritten():
    url = f"http://{SITE}/out?url=http://{fake_onion('other')}/"
    assert unwrap(url, None, {"engine.onion"}) == url


def test_undecodable_wrapper_is_returned_as_is():
    href = "https://onionlandsearchengine.net/r?s=eyJpdiI6IjhlSWFpQk1CbD"
    assert unwrap(href) == href


@pytest.mark.parametrize(
    "variant",
    [
        f"http://{SITE}",
        f"http://{SITE}/",
        f"HTTP://{SITE.upper()}/",
        f"https://{SITE}/",
        f"http://www.{SITE}/",
        f"http://{SITE}:80/",
        f"http://{SITE}/index.html",
        f"http://{SITE}/#top",
        f"http://{SITE}/?utm_source=tordex&utm_medium=x",
        f"http://{SITE}/?PHPSESSID=abc",
        f"{SITE}/",
    ],
)
def test_canonical_forms_collapse(variant):
    assert canonicalize(variant) == f"http://{SITE}"


def test_canonical_keeps_meaningful_parts():
    assert canonicalize(f"http://{SITE}/a/b/?y=2&x=1") == f"http://{SITE}/a/b?x=1&y=2"
    # index.php with a query is a real script path, not the directory index
    assert canonicalize(f"http://{SITE}/index.php?topic=1") == f"http://{SITE}/index.php?topic=1"
    assert canonicalize("https://example.com:8443/x") == "https://example.com:8443/x"


def test_canonicalize_is_idempotent():
    url = f"http://www.{SITE}//a//b/?b=2&a=%7E1&utm_x=1#frag"
    once = canonicalize(url)
    assert canonicalize(once) == once


def test_display_url_strips_only_tracking():
    assert display_url(f"http://{SITE}/p?id=5&utm_source=x#f") == f"http://{SITE}/p?id=5"
    assert display_url(f"http://{SITE}/p?b=2&a=1") == f"http://{SITE}/p?b=2&a=1"

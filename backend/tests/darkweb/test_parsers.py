from app.services.darkweb.engines.parsers import (
    ParseContext,
    looks_blocked,
    parse_generic,
    parse_json,
    parse_page,
)
from app.services.darkweb.onion import is_valid_v3, onion_host

from .conftest import fake_onion, read_fixture

A, B, C = fake_onion("a"), fake_onion("b"), fake_onion("c")


def ctx(base: str, *own: str) -> ParseContext:
    return ParseContext(base_url=base, own_hosts=set(own))


# --- real captures ------------------------------------------------------------------------


def test_vormweb_real_capture():
    items, used = parse_page("vormweb", read_fixture("vormweb.html"), ctx("https://vormweb.de/en/search?q=bitcoin", "vormweb.de"))
    assert used == "vormweb"
    assert len(items) == 20
    first = items[0]
    assert first.url.startswith("http://wizardswgtu2ovor7r2esg3cxdpt7tv4nrugi32lldv53zmtonbz6sid.onion")
    assert first.badge == "Verified"
    assert "Exchange BTC" in first.snippet
    assert {i.badge for i in items} >= {"Verified", "Warning"}
    # the #report/#verify anchors (which embed the URL again) are not separate results
    assert len({i.url for i in items}) == len(items)
    assert all("vormweb.de" not in i.url for i in items)


def test_onionland_real_capture_marks_ads_and_reads_link_text():
    items, used = parse_page("onionland", read_fixture("onionland.html"),
                             ctx("https://onionlandsearchengine.net/search?q=bitcoin", "onionlandsearchengine.net"))
    assert used == "onionland"
    assert len(items) == 16
    ads = [i for i in items if i.sponsored]
    assert len(ads) == 4
    assert any("PRIME MARKET" in a.title for a in ads)
    organic = [i for i in items if not i.sponsored]
    assert organic[0].title == "Bitcoin Core :: Bitcoin"
    # hrefs are encrypted /r?s= blobs; URLs must come from the .link text instead
    assert all(onion_host(i.url) for i in items)
    assert not any("onionlandsearchengine.net" in i.url for i in items)


def test_onionsearchengine_real_capture():
    items, _ = parse_page("onionsearchengine", read_fixture("onionsearchengine.html"),
                          ctx("https://onionsearchengine.com/search.php?q=bitcoin", "onionsearchengine.com"))
    assert len(items) == 8
    assert items[2].url == "http://mixtumjzn2tsiusfkdhutntamspsg43kgt764qbdaxjebce4h6fcfiad.onion/"
    assert "Bitcoin Mixer" in items[2].title
    assert items[-1].url == "http://blockchair.com/tokens/iexec-rlc"  # clearnet result kept for the pipeline


def test_onionengine_real_capture_unwraps_open_links():
    items, _ = parse_page("onionengine", read_fixture("onionengine.html"), ctx("https://onionengine.com/?q=bitcoin", "onionengine.com"))
    assert len(items) == 6
    assert items[1].url == "http://mixtumjzn2tsiusfkdhutntamspsg43kgt764qbdaxjebce4h6fcfiad.onion"
    assert not any("onionengine.com" in i.url for i in items)


# --- synthetic fixtures modelled on documented markup --------------------------------------


def test_ahmia_results_and_redirect_unwrapping():
    items, used = parse_page("ahmia", read_fixture("ahmia.html"), ctx("https://ahmia.fi/search/?q=bitcoin", "ahmia.fi"))
    assert used == "ahmia"
    assert len(items) == 7
    assert items[2].url.endswith("/guide.php?lang=en&section=cold-storage")  # '&' in unencoded tail kept
    assert items[0].last_seen is not None
    assert items[-1].snippet == ""  # "No description provided"


def test_ahmia_homepage_is_not_a_results_page():
    items, used = parse_page("ahmia", read_fixture("ahmia.prep.html"),
                             ctx("https://ahmia.fi/", "ahmia.fi", "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"))
    # falls back to generic, which finds nothing but Ahmia's own address
    assert used == "generic"
    assert items == []


def test_tor66():
    items, _ = parse_page("tor66", read_fixture("tor66.html"), ctx("http://tor66.onion/search?q=bitcoin",
                                                                   "tor66sewebgixwhcqfnp5inzp5x5uohhdy3kvtnyfxc2e5mxiuh34iid.onion"))
    assert len(items) == 7  # own /fresh link excluded
    assert items[0].title.startswith("Trocador.app")
    assert "Swap coins privately" in items[0].snippet


def test_tordex_torch_haystak():
    tordex, _ = parse_page("tordex", read_fixture("tordex.html"), ctx("http://tordex.onion/search?query=bitcoin"))
    assert len(tordex) == 5 and tordex[1].title.startswith("Bitcoin Mixer")
    torch, _ = parse_page("torch", read_fixture("torch.html"), ctx("http://torch.onion/search?query=bitcoin"))
    assert len(torch) == 5 and "Hire a hacker" in torch[-1].title
    hay, _ = parse_page("haystak", read_fixture("haystak.html"), ctx("http://haystak.onion/?q=bitcoin"))
    assert len(hay) == 4
    assert all(onion_host(i.url) and "redirect.php" not in i.url for i in hay)


def test_torch_omega_table_layout_and_zero_results():
    html = f"""<table><tr><td>1</td><td><b>Omega title</b><br><a href="http://{A}/x">http://{A}/x</a>
               <small>omega snippet</small></td></tr></table>"""
    items, used = parse_page("torch", html, ctx("http://torch.onion/"))
    assert used == "torch" and items[0].title == "Omega title" and items[0].snippet == "omega snippet"
    items, used = parse_page("torch", "<p>Your search returned <b>0</b> results</p>", ctx("http://torch.onion/"))
    assert (items, used) == ([], "torch")


def test_oss_submarine_phobos_evo():
    oss = f'<div class="ossnumfound">2</div><div class="osscmnrdr ossfieldrdr1"><a href="http://{A}/">OSS hit</a></div>'
    assert parse_page("oss", oss, ctx("http://oss.onion/"))[0][0].url == f"http://{A}/"
    sub = f'<ul id="page"><li><a href="http://{B}/">Sub title</a></li><li><a href="http://{B}/">http://{B}/</a></li></ul>'
    items, _ = parse_page("submarine", sub, ctx("http://sub.onion/"))
    assert items[0].title == "Sub title" and items[0].url == f"http://{B}/"
    pho = f'<div class="serp"><a class="titles" href="http://{C}/">Phobos hit</a><p>desc</p></div>'
    assert parse_page("phobos", pho, ctx("http://phobos.onion/"))[0][0].title == "Phobos hit"
    evo = f'<div id="results"><div class="title"><a href="/r.php?url=http%3A%2F%2F{A}%2Fe">Evo hit</a></div></div>'
    assert parse_page("evo", evo, ctx("http://evo.onion/"))[0][0].url == f"http://{A}/e"


def test_json_parser_tolerates_shapes():
    items = parse_json(read_fixture("danwin.json"), ctx("http://danwin.onion/"))
    assert len(items) == 3
    assert all(is_valid_v3(onion_host(i.url)) for i in items)
    assert parse_json("not json", ctx("http://x/")) is None


# --- generic fallback ------------------------------------------------------------------------


def test_generic_parser_handles_unknown_layouts():
    own = fake_onion("engine")
    html = f"""
    <nav><a href="http://{own}/">Home</a></nav>
    <div class="hit"><a href="http://{A}/page">Some useful page</a><p>Useful description of the page here.</p></div>
    <div class="hit"><a href="/go?url=http%3A%2F%2F{B}%2F">Wrapped link title</a> text about B</div>
    <div class="hit"><a href="http://{A}/page">cached</a></div>
    <div class="hit"><a href="http://{C}/"><img src="x.png"></a><h4>Heading from block</h4></div>
    <a href="https://clearnet.example/">clearnet</a>
    """
    items = parse_generic(html, ctx(f"http://{own}/search?q=x", own))
    urls = [i.url for i in items]
    assert urls == [f"http://{A}/page", f"http://{B}/", f"http://{C}/"]
    assert items[0].title == "Some useful page" and "Useful description" in items[0].snippet
    assert items[2].title == "Heading from block"


def test_generic_regex_fallback_when_no_anchors():
    items = parse_generic(f"<pre>found: http://{A}/x and {B}</pre>", ctx("http://e.onion/"))
    assert [i.url for i in items] == [f"http://{A}/x"]


def test_specific_parser_falls_back_to_generic_on_layout_drift():
    html = f'<main><section><a href="http://{A}/">Redesigned result title</a></section></main>'
    items, used = parse_page("tordex", html, ctx("http://tordex.onion/"))
    assert used == "generic" and items[0].url == f"http://{A}/"


def test_looks_blocked():
    assert looks_blocked("<title>Just a moment...</title><div id='cf-chl-widget'></div>")
    assert looks_blocked("<p>Please solve the CAPTCHA</p>")
    assert not looks_blocked("<ul><li>result</li></ul>")

from app.services.darkweb.engines import load_catalog
from app.services.darkweb.onion import find_onion_urls, is_v2, is_valid_v3, onion_host, service_id

from .conftest import fake_onion

AHMIA = "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"


def test_valid_v3_addresses():
    assert is_valid_v3(AHMIA)
    assert is_valid_v3(AHMIA.removesuffix(".onion"))
    assert is_valid_v3("www." + AHMIA)
    assert is_valid_v3(fake_onion("anything"))


def test_every_catalog_address_passes_checksum():
    engines = load_catalog()
    assert len(engines) >= 60
    for engine in engines:
        assert not engine.spec.rejected_mirrors, engine.name
        for mirror in engine.spec.mirrors:
            assert is_valid_v3(mirror), (engine.name, mirror)


def test_rejects_typosquats_and_garbage():
    # 54-char "Dark Search" link circulating on Torzle (typo of Haystak's address)
    assert not is_valid_v3("hystak5njsmn2hqkewecpaxetatwhsbsa64jom2k22z5afxhnpxfid.onion")
    # one character changed -> checksum mismatch
    tampered = AHMIA[:10] + ("a" if AHMIA[10] != "a" else "b") + AHMIA[11:]
    assert not is_valid_v3(tampered)
    assert not is_valid_v3("3g2upl4pq6kufc4m.onion")  # v2
    assert not is_valid_v3("example.com")
    assert not is_valid_v3("")


def test_v2_detection_and_host_helpers():
    assert is_v2("3g2upl4pq6kufc4m.onion")
    assert not is_v2(AHMIA)
    assert service_id("sub.domain." + AHMIA) == AHMIA.removesuffix(".onion")
    assert onion_host(f"http://{AHMIA}/search/?q=x") == AHMIA
    assert onion_host("https://ahmia.fi/") is None


def test_find_onion_urls_in_text():
    text = f"mirror at http://{AHMIA}/path. Also see (http://{AHMIA}/path)."
    assert find_onion_urls(text) == [f"http://{AHMIA}/path"]

import json
import time

import pytest

from decint_exe_maker import vendor
from decint_exe_maker.runtime import licensing, rsa_lite


def test_rsa_sign_verify_roundtrip(keypair):
    n, e, d = keypair["n"], keypair["e"], keypair["d"]
    msg = b"hello decint"
    sig = rsa_lite.sign(msg, n, d)
    assert rsa_lite.verify(msg, sig, n, e)
    assert not rsa_lite.verify(b"hello decinT", sig, n, e)
    assert not rsa_lite.verify(msg, sig[:-1] + bytes([sig[-1] ^ 1]), n, e)
    assert not rsa_lite.verify(msg, b"short", n, e)
    assert len(rsa_lite.fingerprint(n, e)) == 24


def _issue(keypair, **over):
    payload = {"v": 1, "pid": "my-app", "lid": "L-1", "cust": "Jane", "email": "", "iat": int(time.time()),
               "exp": int(time.time()) + 86400 * 30, "mid": None, "feat": ["pro"], "note": ""}
    payload.update(over)
    return licensing.encode_license(payload, keypair["n"], keypair["d"]), payload


def test_license_roundtrip_and_whitespace(keypair):
    text, payload = _issue(keypair)
    assert text.startswith("DECINT1.")
    got = licensing.verify_license(text, keypair["n"], keypair["e"], product_id="my-app")
    assert got == payload
    messy = "-----BEGIN DECINT LICENSE-----\n" + "\n".join(text[i:i + 40] for i in range(0, len(text), 40)) + \
            "\n-----END DECINT LICENSE-----\n"
    assert licensing.verify_license(messy, keypair["n"], keypair["e"], product_id="my-app") == payload


@pytest.mark.parametrize("mutate, code", [
    (lambda t: t[:-3] + "AAA", "bad_signature"),
    (lambda t: "garbage", "malformed"),
    (lambda t: "", "missing"),
])
def test_license_rejections(keypair, mutate, code):
    text, _ = _issue(keypair)
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.verify_license(mutate(text), keypair["n"], keypair["e"], product_id="my-app")
    assert ex.value.code == code


def test_tampered_payload_is_rejected(keypair):
    text, payload = _issue(keypair)
    head, body, sig = text.split(".")
    payload["exp"] += 10 ** 8
    forged = licensing._b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.verify_license(f"{head}.{forged}.{sig}", keypair["n"], keypair["e"], product_id="my-app")
    assert ex.value.code == "bad_signature"


def test_expiry_product_and_machine(keypair):
    text, _ = _issue(keypair, exp=int(time.time()) - 10)
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.verify_license(text, keypair["n"], keypair["e"], product_id="my-app")
    assert ex.value.code == "expired"

    text, _ = _issue(keypair)
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.verify_license(text, keypair["n"], keypair["e"], product_id="other-app")
    assert ex.value.code == "wrong_product"

    text, _ = _issue(keypair, mid="AAAA-BBBB-CCCC-DDDD")
    assert licensing.verify_license(text, keypair["n"], keypair["e"], product_id="my-app", machine="AAAA-BBBB-CCCC-DDDD")
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.verify_license(text, keypair["n"], keypair["e"], product_id="my-app", machine="0000-0000-0000-0000")
    assert ex.value.code == "machine_mismatch"
    # unbound license runs anywhere
    text, _ = _issue(keypair, mid=None)
    assert licensing.verify_license(text, keypair["n"], keypair["e"], product_id="my-app", machine="0000-0000-0000-0000")


def test_other_vendor_key_is_rejected(keypair):
    other = rsa_lite.generate_keypair(1024)
    text, _ = _issue(keypair)
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.verify_license(text, other["n"], other["e"], product_id="my-app")
    assert ex.value.code == "bad_signature"


def test_machine_id_is_stable_and_formatted():
    a, b = licensing.machine_id(), licensing.machine_id()
    assert a == b and len(a) == 19 and a.count("-") == 3


def test_find_and_save_license(tmp_path, keypair, monkeypatch):
    text, _ = _issue(keypair)
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    assert licensing.find_license("my-app", exe_dir) is None
    (exe_dir / "my-app.lic").write_text(text)
    assert licensing.clean_license_text(licensing.find_license("my-app", exe_dir)) == text
    (exe_dir / "my-app.lic").unlink()
    p = licensing.save_license("my-app", text)
    assert p.is_file()
    assert licensing.clean_license_text(licensing.find_license("my-app", exe_dir)) == text
    monkeypatch.setenv("DECINT_LICENSE", "DECINT1.env.override")
    assert licensing.find_license("my-app", exe_dir) == "DECINT1.env.override"


def test_clock_rollback_detection():
    now = time.time()
    licensing.check_clock("my-app", now)
    licensing.check_clock("my-app", now - 3600)           # small drift tolerated
    with pytest.raises(licensing.LicenseError) as ex:
        licensing.check_clock("my-app", now - 5 * 86400)  # 5 days back = tampering
    assert ex.value.code == "clock_rollback"


def test_trial_state():
    assert licensing.trial_state("my-app", 0) == (False, 0)
    active, left = licensing.trial_state("my-app", 14)
    assert active and left in (13, 14)
    active, left = licensing.trial_state("my-app", 14, now=time.time() + 20 * 86400)
    assert not active and left == 0


def test_vendor_issue_and_ledger(monkeypatch):
    monkeypatch.setattr(rsa_lite, "generate_keypair", lambda bits=2048, e=65537: rsa_lite._orig_generate(1024))
    return _vendor_body()


def _vendor_body():
    k = vendor.load_or_create_key("my-app", "My App")
    assert vendor.load_key("my-app")["n"] == k["n"]
    text, payload = vendor.issue_license("my-app", customer="Jane Doe", email="j@x.io", months=6, features=["pro"])
    assert 180 <= (payload["exp"] - payload["iat"]) / 86400 <= 185
    assert licensing.verify_license(text, k["n"], k["e"], product_id="my-app")["cust"] == "Jane Doe"
    rows = vendor.ledger("my-app")
    assert len(rows) == 1 and rows[0]["key"] == text
    assert vendor.list_products()[0]["product_id"] == "my-app"
    r = vendor.inspect_license(text)
    assert r["verified"] is True
    assert vendor.slugify("My Cool App v2!") == "my-cool-app-v2"


rsa_lite._orig_generate = rsa_lite.generate_keypair

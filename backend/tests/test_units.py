"""Fast, offline unit tests for the pure logic (no network)."""

from app.services.discord import decode_snowflake
from app.services.leaks.base import detect_kind, mask_line, mask_secret


def test_snowflake_decode_known_value():
    # Discord's documented example ID → 2016-04-30.
    info = decode_snowflake("175928847299117063")
    assert info.created_at.startswith("2016-04-30")
    assert info.worker_id == 1
    assert info.process_id == 0
    assert info.increment == 7


def test_snowflake_epoch_floor():
    # ID 0 is exactly the Discord epoch (2015-01-01).
    assert decode_snowflake("0").created_at.startswith("2015-01-01")


def test_detect_kind():
    assert detect_kind("a@b.com") == "email"
    assert detect_kind("acme.com") == "domain"
    assert detect_kind("sub.acme.co.uk") == "domain"
    assert detect_kind("kestrel_ops") == "username"
    assert detect_kind("kestrel") == "username"
    assert detect_kind("not a domain") == "name"


def test_mask_secret():
    assert mask_secret(None) is None
    assert mask_secret("") == ""
    assert mask_secret("ab") == "••"
    assert mask_secret("abcd") == "a•••"
    got = mask_secret("hunter2")
    assert got[0] == "h" and got[-1] == "2" and set(got[1:-1]) == {"•"}


def test_mask_line():
    assert mask_line("user@x.com:hunter2").startswith("user@x.com:")
    assert mask_line("user@x.com:hunter2").endswith("2")
    assert "•" in mask_line("bob:password123")
    assert mask_line("no-separator-here") == "no-separator-here"

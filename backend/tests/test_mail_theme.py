"""Every message the site sends wears the DECINT lockup — and still carries
the plain text it was written as, so nothing depends on HTML rendering."""

import os
import tempfile

import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "mail.db")
os.environ["PUBLIC_BASE_URL"] = "https://example.test"
os.environ["SMTP_HOST"] = "smtp.example.test"
os.environ["SMTP_FROM"] = "noreply@example.test"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app.services import mail  # noqa: E402

mail.settings = cfg.settings


class _FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


@pytest.fixture()
def smtp(monkeypatch):
    import smtplib

    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_every_message_is_text_plus_themed_html(smtp):
    subject, body = mail.access_lapsed("Pro")
    assert mail.send("someone@example.test", subject, body) is True
    msg = smtp.sent[0]
    assert msg.get_content_type() == "multipart/alternative"
    parts = {p.get_content_type(): p.get_content() for p in msg.iter_parts()}
    assert parts["text/plain"].strip() == body.strip()
    html = parts["text/html"]
    assert "DECINT" in html and "OSINT &amp; NETWORK INTELLIGENCE" in html
    assert "#8d5bf6" in html and "#0b0c13" in html            # accent + plate
    assert 'src="https://example.test/icon-192.png"' in html   # the shield
    assert "Pick a plan again" in html


def test_html_escapes_content_and_links_urls():
    html = mail.render_html("<b>subject</b>", "Visit https://example.test/pricing now.\n\nA <script> tag.")
    assert "&lt;b&gt;subject&lt;/b&gt;" in html and "<b>subject</b>" not in html
    assert '<a href="https://example.test/pricing"' in html
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_indented_blocks_keep_their_layout():
    subject, body = mail.signup_alert("a@example.test", "alice", "pending", "203.0.113.9")
    html = mail.render_html(subject, body)
    assert "<pre" in html and "email     a@example.test" in html


def test_no_base_url_means_no_broken_image(monkeypatch):
    monkeypatch.setattr(cfg.settings, "public_base_url", "")
    html = mail.render_html("s", "b")
    assert "<img" not in html and "DECINT" in html

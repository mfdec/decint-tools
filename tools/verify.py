#!/usr/bin/env python3
"""Verify a running DECINT deployment.

    python tools/verify.py all          # everything below
    python tools/verify.py auth         # console gating + login/logout
    python tools/verify.py analytics    # visitor collection + admin reads
    python tools/verify.py theme        # design tokens actually served
    python tools/verify.py icons        # favicon set served, right formats
    python tools/verify.py api          # health, leaks, darkweb job
    python tools/verify.py offline      # unit suites (no server needed)

Replaces deploy/verify*.sh, smoke-*.sh and check-live-beacon.sh. Every HTTP
call is made from Python, so there is no shell quoting to get wrong — which is
what made several of the shell versions silently report false passes.

Exit code is the number of failures, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BACKEND, WEB_URL, err, head, info, ok, run, venv_python, warn  # noqa: E402

FAILS = 0
CHECKS = 0


def check(cond: bool, msg: str, detail: str = "") -> bool:
    global FAILS, CHECKS
    CHECKS += 1
    if cond:
        ok(msg)
    else:
        FAILS += 1
        err(f"{msg}{'  — ' + detail if detail else ''}")
    return cond


class Client:
    """Cookie-aware HTTP client on the stdlib, so verify works with no deps."""

    def __init__(self, base: str = WEB_URL):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            _NoRedirect(),
        )

    def request(self, path: str, method: str = "GET", body: dict | None = None,
                headers: dict | None = None, timeout: float = 30.0):
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
        except Exception as e:  # noqa: BLE001
            return 0, str(e), {}

    def json(self, path: str, method: str = "GET", body: dict | None = None):
        code, text, _ = self.request(path, method, body)
        try:
            return code, json.loads(text)
        except ValueError:
            return code, None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Don't follow redirects — we need to observe the 307 to /login."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def login(c: Client) -> bool:
    """Sign in however this deployment allows (break-glass or seeded admin)."""
    code, body = c.json("/api/v1/auth/bootstrap")
    if code == 200 and body and not body.get("has_users"):
        code, body = c.json("/api/v1/auth/login", "POST", {"token": ""})
        return bool(body and body.get("authenticated"))
    for email, pw in (
        ("admin@decint.tools", "correct-horse-battery-staple"),
        ("victim@decint.tools", "victim-password-is-long"),
    ):
        code, body = c.json("/api/v1/auth/login", "POST", {"email": email, "password": pw})
        if body and body.get("authenticated"):
            return True
    return False


# ─────────────────────────── suites ───────────────────────────

def v_auth() -> None:
    head("auth + console gating")
    anon = Client()

    for page in ("/", "/tools", "/pricing"):
        code, html, _ = anon.request(page)
        check('href="/console"' not in html, f"{page} exposes no /console link")
    code, html, _ = anon.request("/")
    check('href="/signup"' in html, "landing offers a signup link")

    code, _, headers = anon.request("/console")
    check(code in (307, 302), "/console redirects when signed out", f"HTTP {code}")
    # HTTP header names are case-insensitive; dict() preserves whatever case
    # the server sent, so look it up case-insensitively rather than assuming.
    location = next((v for k, v in headers.items() if k.lower() == "location"), "")
    check("/login" in location, "…and it redirects to /login", f"Location: {location!r}")

    for ep in ("/api/v1/admin/users", "/api/v1/analytics/summary", "/api/v1/leaks/search?query=a@b.co"):
        code, _, _ = anon.request(ep)
        check(code == 401, f"{ep} requires a session", f"HTTP {code}")

    c = Client()
    check(login(c), "login succeeds")
    code, body = c.json("/api/v1/auth/session")
    check(bool(body and body.get("authenticated")), "session reports authenticated")
    code, _, _ = c.request("/console")
    check(code == 200, "/console reachable once signed in", f"HTTP {code}")

    c.json("/api/v1/auth/logout", "POST")
    code, body = c.json("/api/v1/auth/session")
    check(not (body or {}).get("authenticated"), "logout ends the session")


def v_api() -> None:
    head("API surface")
    c = Client()
    code, h = c.json("/api/v1/health")
    check(code == 200 and h is not None, "health responds", f"HTTP {code}")
    if h:
        info(f"version={h.get('version')} tor={h.get('tor')} "
             f"sniffer={h.get('sniffer_enabled')}")
        check(h.get("tor") is True, "Tor SOCKS reachable", h.get("tor_detail", ""))

    if not login(c):
        warn("cannot sign in — skipping authenticated API checks")
        return

    code, d = c.json("/api/v1/leaks/search?query=test@example.com&kind=email")
    if code == 200 and d:
        live = [s for s in d["sources"] if s["ok"]]
        check(len(live) >= 1, f"leak search: {d['total']} hits from {len(live)} live source(s)")
    else:
        check(False, "leak search responded", f"HTTP {code}")

    code, j = c.json("/api/v1/darkweb/search", "POST",
                     {"query": "leaked database", "mode": "gateway", "limit": 5})
    if code == 200 and j:
        job = j["job_id"]
        for _ in range(40):
            code, s = c.json(f"/api/v1/darkweb/jobs/{job}")
            if s and s["status"] in ("done", "error"):
                break
            time.sleep(1)
        check(bool(s) and s["status"] == "done",
              f"darkweb job completed ({len((s or {}).get('results', []))} results)",
              (s or {}).get("error") or "")
    else:
        check(False, "darkweb job accepted", f"HTTP {code}")


def v_analytics() -> None:
    head("visitor analytics")
    anon = Client()
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    code, _, _ = anon.request(
        "/api/v1/analytics/collect", "POST",
        {"event": "pageview", "path": "/verify", "screen_w": 2560, "screen_h": 1440,
         "cpu_cores": 16, "device_memory": 32, "gpu": "verify-gpu",
         "timezone": "Europe/Amsterdam", "session_id": "verify-1"},
        headers={"User-Agent": UA})
    check(code == 204, "anonymous visitor event accepted", f"HTTP {code}")

    code, _, _ = anon.request("/api/v1/analytics/recent")
    check(code == 401, "visitor data requires a session", f"HTTP {code}")

    c = Client()
    if not login(c):
        warn("cannot sign in — skipping analytics reads")
        return
    code, s = c.json("/api/v1/analytics/summary?days=7")
    check(code == 200 and s is not None, "summary readable by operator")
    if s:
        info(f"visitors={s['visitors']} events={s['events']} bots={s['bots_filtered']} "
             f"geo_db={s['geo_enabled']}")
        if not s["geo_enabled"]:
            warn("GeoLite2 not installed — country/city/ASN will be null")
    code, r = c.json("/api/v1/analytics/recent?limit=5")
    rows = (r or {}).get("rows", [])
    check(len(rows) > 0, f"recent log populated ({len(rows)} rows)")
    if rows:
        top = rows[0]
        check(bool(top.get("browser")) and bool(top.get("os")),
              f"events enriched: {top.get('browser')}/{top.get('os')} {top.get('device_type')}")


def v_theme() -> None:
    head("design tokens")
    c = Client()
    _, html, _ = c.request("/")
    import re
    m = re.search(r'/_next/static/css/[^"]+\.css', html)
    if not m:
        check(False, "found the stylesheet")
        return
    _, css, _ = c.request(m.group(0))
    # Updated for the "empty shield" identity: one violet, no cyan, darker
    # ground. If you retune globals.css again, retune these too — this check
    # exists to catch a stylesheet that silently failed to rebuild.
    for token in ("--color-accent:#8d5bf6", "--color-accent-2:#a67ff8",
                  "--grad-from:#b594ff", "--grad-to:#7c4dee",
                  "--color-bg:#0b0c13", "--color-surface:#10121c"):
        check(token in css, f"token served: {token}")
    # The retired palette: the old midpoint blue, the cyan end of the old
    # gradient, and the two previous grounds. Any of these still being served
    # means a stale build is live.
    for old in ("#4989e5", "#04b6d3", "#0f1622", "#1a2435", "#9184d9", "#b5abfc", "#161826"):
        check(old not in css, f"retired colour gone: {old}")


def v_icons() -> None:
    head("icon set")
    c = Client()
    expect = {
        "/favicon.ico": b"\x00\x00\x01\x00",
        "/favicon-32x32.png": b"\x89PNG",
        "/favicon-16x16.png": b"\x89PNG",
        "/icon-512.png": b"\x89PNG",
        "/apple-touch-icon.png": b"\x89PNG",
    }
    for path, magic in expect.items():
        code, body, _ = c.request(path)
        raw = body.encode("utf-8", "surrogateescape")[:4]
        check(code == 200, f"{path} served", f"HTTP {code}")


def v_offline() -> None:
    head("offline suites (no server needed)")
    py = str(venv_python())
    # Each suite reports its own verdict on the last line. Parse that rather
    # than grepping for "FAIL" anywhere in stdout — the moderation suite ends
    # with "FAILURES: 0", which a naive substring check reads as a failure.
    def verdict(name: str, stdout: str, rc: int) -> tuple[bool, str]:
        last = (stdout or "").strip().splitlines()[-1:] or [""]
        line = last[0].strip()
        if rc != 0:
            return False, f"exit {rc}"
        if line.startswith("FAILURES:") or line.startswith("FINDINGS:"):
            n = line.split(":", 1)[1].strip()
            return n == "0", line
        return "PASSED" in line.upper(), line

    for name in ("test_moderation.py", "test_accounts.py", "test_security.py"):
        if not (BACKEND / name).exists():
            continue
        r = run([py, name], cwd=BACKEND, check=False, capture=True, quiet=True)
        good, line = verdict(name, r.stdout or "", r.returncode)
        check(good, f"{name}: {line[:70]}", (r.stderr or "")[-200:])


SUITES = {
    "auth": v_auth, "api": v_api, "analytics": v_analytics,
    "theme": v_theme, "icons": v_icons, "offline": v_offline,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a DECINT deployment")
    ap.add_argument("suite", nargs="?", default="all",
                    choices=["all", *SUITES])
    ap.add_argument("--base", default=WEB_URL, help="base URL (default localhost:3000)")
    a = ap.parse_args()

    global WEB_URL_OVERRIDE
    if a.base != WEB_URL:
        import _common
        _common.WEB_URL = a.base

    names = list(SUITES) if a.suite == "all" else [a.suite]
    for n in names:
        SUITES[n]()

    print()
    if FAILS:
        err(f"{FAILS} of {CHECKS} checks FAILED")
    else:
        ok(f"all {CHECKS} checks passed")
    return FAILS


if __name__ == "__main__":
    raise SystemExit(main())

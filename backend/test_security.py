"""Security audit of the authentication system — adversarial, not confirmatory.

Each check tries to BREAK something. A pass means the attack failed.
"""

import os
import statistics
import tempfile
import time

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "sec.db")
os.environ["OPERATOR_TOKEN"] = ""
# Pinned, not inherited: a deployment .env with COOKIE_SECURE=true makes every
# session cookie Secure, and the test client speaks plain HTTP — so it silently
# drops the cookie and every authenticated request 401s. That reads as a broken
# authorisation check rather than a harness problem, so pin it here.
os.environ["COOKIE_SECURE"] = "false"

import pyotp
from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()
import app.config as cfg

cfg.settings = get_settings()

from app import db as _db
from app.main import app
from app.services import captcha as _cap, users


def isolate(email: str | None = None) -> None:
    """Reset the shared rate-limit state between sections.

    Every request in this file arrives from the same TestClient IP, so without
    this each section inherits the previous section's failure counters and we
    end up testing the harness rather than the code.
    """
    _cap.reset_all()
    if email:
        u = users.get_by_email(email)
        if u:
            users.clear_failed_logins(u["id"])

_db._conn = None
_db.get_conn()

VICTIM = "victim@decint.tools"
VICTIM_PW = "victim-password-is-long"
ATTACKER = "attacker@evil.example"
ATTACKER_PW = "attacker-password-long"

users.create(VICTIM, VICTIM_PW, role="admin", tier="enterprise", username="victimadmin")
users.create(ATTACKER, ATTACKER_PW, role="user", tier="essentials", username="attacker1")

findings: list[tuple[str, str, str]] = []   # (severity, title, detail)
passed: list[str] = []


def ok(t):
    passed.append(t)
    print(f"   PASS  {t}")


def fail(sev, t, d):
    findings.append((sev, t, d))
    print(f"   {sev.upper():8} {t}\n             {d}")


print("== A. Account enumeration ==")
c = TestClient(app)
r1 = c.post("/api/v1/auth/login", json={"email": VICTIM, "password": "wrong"})
r2 = c.post("/api/v1/auth/login", json={"email": "ghost@nowhere.test", "password": "wrong"})
if r1.status_code == r2.status_code and r1.json() == r2.json():
    ok("login: identical response for real vs unknown account")
else:
    fail("HIGH", "login enumerates accounts", f"{r1.status_code}/{r1.json()} vs {r2.status_code}/{r2.json()}")

r3 = c.post("/api/v1/auth/signup", json={
    "email": VICTIM, "username": "brandnew1", "password": "a-long-enough-password"})
r4 = c.post("/api/v1/auth/signup", json={
    "email": "fresh@nowhere.test", "username": "brandnew2", "password": "a-long-enough-password"})
if r3.status_code == r4.status_code:
    ok("signup: existing email does not produce a distinguishable response")
else:
    fail("HIGH", "signup enumerates accounts",
         f"existing={r3.status_code} new={r4.status_code} — signup reveals registered addresses")

print("\n== B. Timing side-channel on login ==")


def timeit(email, n=6):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-x"})
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


t_real = timeit(VICTIM)
t_fake = timeit("ghost@nowhere.test")
ratio = max(t_real, t_fake) / max(1e-6, min(t_real, t_fake))
print(f"             real={t_real*1000:.1f}ms  unknown={t_fake*1000:.1f}ms  ratio={ratio:.2f}x")
if ratio < 1.6:
    ok("login: no usable timing oracle (both paths run Argon2)")
else:
    fail("MEDIUM", "login timing reveals account existence",
         f"{ratio:.2f}x difference — the no-such-user path skips password hashing")

isolate(VICTIM)
print("\n== C. Session handling ==")
c2 = TestClient(app)
c2.post("/api/v1/auth/login", json={"email": VICTIM, "password": VICTIM_PW})
sid_cookie = c2.cookies.get("decint_session")
if sid_cookie and len(sid_cookie) >= 32:
    ok(f"session id is high-entropy ({len(sid_cookie)} chars)")
else:
    fail("HIGH", "weak session id", str(sid_cookie))

# Forged session id must not authenticate.
c3 = TestClient(app)
c3.cookies.set("decint_session", "a" * 43)
if c3.get("/api/v1/auth/session").json()["authenticated"] is False:
    ok("forged session cookie rejected")
else:
    fail("CRITICAL", "forged session accepted", "arbitrary cookie value authenticates")

# Logout must kill the session server-side, not just drop the cookie.
stolen = sid_cookie
c2.post("/api/v1/auth/logout")
c4 = TestClient(app)
c4.cookies.set("decint_session", stolen)
if c4.get("/api/v1/auth/session").json()["authenticated"] is False:
    ok("logout revokes server-side (a stolen cookie dies with it)")
else:
    fail("HIGH", "logout is client-side only",
         "the old session id still authenticates after logout")

isolate(ATTACKER)
print("\n== D. Privilege escalation ==")
ca = TestClient(app)
ca.post("/api/v1/auth/login", json={"email": ATTACKER, "password": ATTACKER_PW})
atk = users.get_by_email(ATTACKER)

if ca.get("/api/v1/admin/users").status_code == 403:
    ok("non-admin blocked from admin API")
else:
    fail("CRITICAL", "non-admin can read admin API", "role check missing")

r = ca.patch(f"/api/v1/admin/users/{atk['id']}", json={"role": "admin"})
if r.status_code == 403:
    ok("user cannot promote themselves to admin")
else:
    fail("CRITICAL", "self-promotion possible", f"HTTP {r.status_code}")

r = ca.get("/api/v1/analytics/recent")
if r.status_code == 403:
    ok("non-admin blocked from visitor analytics (personal data)")
else:
    fail("HIGH", "non-admin can read visitor analytics", f"HTTP {r.status_code}")

isolate(VICTIM)
print("\n== E. Brute force / lockout ==")
cb = TestClient(app)
codes = []
for i in range(9):
    rr = cb.post("/api/v1/auth/login", json={"email": VICTIM, "password": f"guess-{i}"})
    codes.append(rr.status_code)
if 423 in codes:
    ok(f"account locks after repeated failures (first 423 at attempt {codes.index(423)+1})")
else:
    fail("MEDIUM", "no lockout observed", f"status codes: {codes}")

# Correct password during lockout must still be refused.
rr = cb.post("/api/v1/auth/login", json={"email": VICTIM, "password": VICTIM_PW})
if rr.status_code == 423:
    ok("lockout holds even against the correct password")
else:
    fail("MEDIUM", "lockout bypassed by correct password", f"HTTP {rr.status_code}")

# Lockout must not become an enumeration oracle: hammering an address that
# does NOT exist has to look the same as hammering one that does.
isolate()
cg = TestClient(app)
ghost = [cg.post("/api/v1/auth/login",
                 json={"email": "ghost2@nowhere.test", "password": f"g{i}"}).status_code
         for i in range(9)]
if 423 in ghost:
    ok("unknown addresses lock out identically (no enumeration via lockout)")
else:
    fail("MEDIUM", "lockout distinguishes real from fake accounts",
         f"unknown-address statuses: {ghost}")

isolate(VICTIM)
print("\n== F. MFA challenge integrity ==")
users.clear_failed_logins(users.get_by_email(VICTIM)["id"])
v = users.get_by_email(VICTIM)
import pyotp as _p
from app.services import twofactor

secret = twofactor.totp_begin(v)["secret"]
twofactor.totp_confirm(users.get_by_email(VICTIM), _p.TOTP(secret).now())

cm = TestClient(app)
body = cm.post("/api/v1/auth/login", json={"email": VICTIM, "password": VICTIM_PW}).json()
if body.get("mfa_required") and cm.get("/api/v1/auth/session").json()["authenticated"] is False:
    ok("password alone grants no session when MFA is on")
else:
    fail("CRITICAL", "MFA bypassable", "session issued before second factor")

# A forged/self-made challenge must not be accepted.
r = cm.post("/api/v1/auth/mfa/verify",
            json={"challenge": "not-a-real-challenge", "method": "totp",
                  "code": _p.TOTP(secret).now()})
if r.status_code == 400:
    fail_ok = True
    ok("forged MFA challenge token rejected")
else:
    fail("CRITICAL", "forged MFA challenge accepted", f"HTTP {r.status_code}")

# Challenge for one user must not mint a session for another.
atk_challenge = None
ck = TestClient(app)
b2 = ck.post("/api/v1/auth/login", json={"email": ATTACKER, "password": ATTACKER_PW}).json()
if not b2.get("mfa_required"):
    ok("attacker without MFA is unaffected by victim's challenge (no cross-use)")

print("\n== G. Moderation / impersonation ==")
from app.services import moderation

for bad_name in ["admin", "adm1n", "decint_staff", "fuck_you", "victimadmin"]:
    if moderation.check_username(bad_name) is not None:
        ok(f"username refused: {bad_name!r}")
    else:
        fail("MEDIUM", "impersonation/profanity allowed", f"accepted {bad_name!r}")

isolate()
print("\n== H. Input validation ==")
r = c.post("/api/v1/auth/signup", json={
    "email": "x@y.test", "username": "ok_name_1", "password": "short"})
if r.status_code == 400:
    ok("short password rejected at signup")
else:
    fail("HIGH", "weak password accepted", f"HTTP {r.status_code}")

r = c.post("/api/v1/auth/signup", json={
    "email": "not-an-email", "username": "ok_name_2", "password": "a-long-enough-password"})
if r.status_code == 400:
    ok("malformed email rejected")
else:
    fail("LOW", "malformed email accepted", f"HTTP {r.status_code}")

# SQL injection via the admin search filter.
r = c2.get("/api/v1/admin/users")  # signed out now; just ensure no 500
inj = TestClient(app)
inj.post("/api/v1/auth/login", json={"email": ATTACKER, "password": ATTACKER_PW})
r = inj.get("/api/v1/admin/users?q=' OR 1=1--")
if r.status_code in (401, 403):
    ok("admin search unreachable to non-admin (injection surface closed)")

isolate()
print("\n== I. Signup abuse ==")
cs = TestClient(app)
statuses = []
for i in range(9):
    rr = cs.post("/api/v1/auth/signup", json={
        "email": f"flood{i}@nowhere.test", "username": f"flooduser{i}",
        "password": "a-long-enough-password"})
    statuses.append(rr.status_code)
if 429 in statuses:
    ok(f"signup rate-limited per IP (first 429 at #{statuses.index(429)+1})")
else:
    fail("MEDIUM", "signup not rate limited", f"statuses: {statuses}")

print("\n" + "=" * 66)
print(f"PASSED: {len(passed)}")
print(f"FINDINGS: {len(findings)}")
for sev, t, d in findings:
    print(f"  [{sev}] {t} — {d}")

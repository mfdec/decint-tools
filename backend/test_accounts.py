"""End-to-end exercise of accounts, TOTP second factor, and admin controls.

Runs against a throwaway database so it never touches real data.
"""

import os
import tempfile

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
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
from app.services import users

_db._conn = None
_db.get_conn()

ADMIN = "admin@decint.tools"
ADMIN_PW = "correct-horse-battery-staple"
USER = "client@example.com"
USER_PW = "another-long-password-99"

ok = lambda m: print(f"   ok    {m}")
bad = lambda m: print(f"   FAIL  {m}")


def check(cond, msg):
    (ok if cond else bad)(msg)
    assert cond, msg


print("== 1. bootstrap: no accounts yet ==")
c = TestClient(app)
r = c.get("/api/v1/auth/bootstrap")
check(r.json()["has_users"] is False, "reports no users")

print("\n== 2. create accounts ==")
admin = users.create(ADMIN, ADMIN_PW, role="admin", tier="enterprise")
client_u = users.create(USER, USER_PW, role="user", tier="starter")
check(admin["role"] == "admin", f"admin created (#{admin['id']}, tier={admin['tier']})")
check(client_u["tier"] == "starter", f"user created (#{client_u['id']}, tier=starter)")

print("\n== 3. password rules ==")
try:
    users.create("weak@x.com", "short")
    bad("weak password rejected")
except ValueError as e:
    ok(f"weak password rejected: {e}")
try:
    users.create(ADMIN, ADMIN_PW)
    bad("duplicate email rejected")
except ValueError as e:
    ok(f"duplicate email rejected: {e}")

print("\n== 4. login: wrong password is indistinguishable from unknown user ==")
r1 = c.post("/api/v1/auth/login", json={"email": ADMIN, "password": "nope"})
r2 = c.post("/api/v1/auth/login", json={"email": "ghost@x.com", "password": "nope"})
check(r1.status_code == 401 and r1.json() == r2.json(), "identical 401 for both")

print("\n== 5. login without MFA ==")
c = TestClient(app)
r = c.post("/api/v1/auth/login", json={"email": ADMIN, "password": ADMIN_PW})
check(r.json()["authenticated"] is True, f"signed in as {r.json()['user']['email']}")
check(r.json()["user"]["role"] == "admin", "role=admin in session")

print("\n== 6. enrol TOTP ==")
r = c.post("/api/v1/auth/mfa/totp/begin")
secret = r.json()["secret"]
check(bool(secret), "secret issued")
check(r.json()["otpauth_uri"].startswith("otpauth://totp/"), "otpauth URI issued")
check("<svg" in r.json()["qr_svg"], "QR rendered server-side as SVG")

r = c.post("/api/v1/auth/mfa/enable",
           json={"method": "totp", "code": pyotp.TOTP(secret).now()})
codes = r.json()["recovery_codes"]
check(r.json()["enabled"] is True, "TOTP enabled")
check(len(codes) == 10, f"{len(codes)} recovery codes issued")

r = c.post("/api/v1/auth/mfa/enable", json={"method": "totp", "code": "000000"})
check(r.status_code == 400, "wrong TOTP code rejected")

print("\n== 7. login now demands the second factor ==")
c2 = TestClient(app)
r = c2.post("/api/v1/auth/login", json={"email": ADMIN, "password": ADMIN_PW})
body = r.json()
check(body["authenticated"] is False, "password alone does NOT sign in")
check(body["mfa_required"] is True and body["methods"] == ["totp"], "TOTP challenge issued")
challenge = body["challenge"]

r = c2.get("/api/v1/auth/session")
check(r.json()["authenticated"] is False, "no session mid-challenge")

r = c2.post("/api/v1/auth/mfa/verify",
            json={"challenge": challenge, "method": "totp", "code": "000000"})
check(r.status_code == 401, "wrong code rejected at verify")

r = c2.post("/api/v1/auth/mfa/verify",
            json={"challenge": challenge, "method": "totp",
                  "code": pyotp.TOTP(secret).now()})
check(r.json()["authenticated"] is True, "correct code completes login")

print("\n== 8. recovery code works once ==")
c3 = TestClient(app)
ch = c3.post("/api/v1/auth/login",
             json={"email": ADMIN, "password": ADMIN_PW}).json()["challenge"]
r = c3.post("/api/v1/auth/mfa/verify",
            json={"challenge": ch, "method": "recovery", "code": codes[0]})
check(r.json()["authenticated"] is True, "recovery code accepted")
ch = c3.post("/api/v1/auth/login",
             json={"email": ADMIN, "password": ADMIN_PW}).json()["challenge"]
r = c3.post("/api/v1/auth/mfa/verify",
            json={"challenge": ch, "method": "recovery", "code": codes[0]})
check(r.status_code == 401, "same recovery code refused the second time")

print("\n== 9. admin endpoints require admin ==")
cu = TestClient(app)
cu.post("/api/v1/auth/login", json={"email": USER, "password": USER_PW})
check(cu.get("/api/v1/admin/users").status_code == 403, "normal user gets 403")
check(c2.get("/api/v1/admin/users").status_code == 200, "admin gets 200")

print("\n== 10. grouping / filtering ==")
s = c2.get("/api/v1/admin/stats").json()
check(s["total"] == 2, f"total={s['total']}")
print(f"          by_role={s['by_role']}  by_tier={s['by_tier']}  by_status={s['by_status']}")
check(s["with_mfa"] == 1, f"with_mfa={s['with_mfa']}")
r = c2.get("/api/v1/admin/users?tier=starter").json()
check(r["total"] == 1 and r["users"][0]["email"] == USER, "filter by tier works")
r = c2.get("/api/v1/admin/users?q=client").json()
check(r["total"] == 1, "search by email works")

print("\n== 11. admin changes a tier (upgrade) ==")
r = c2.patch(f"/api/v1/admin/users/{client_u['id']}", json={"tier": "pro"})
check(r.json()["tier"] == "pro", "starter -> pro")

print("\n== 12. suspending kicks the user out immediately ==")
check(cu.get("/api/v1/auth/session").json()["authenticated"] is True, "user session live")
c2.patch(f"/api/v1/admin/users/{client_u['id']}", json={"status": "suspended"})
check(cu.get("/api/v1/auth/session").json()["authenticated"] is False,
      "existing session dies the moment the account is suspended")
r = cu.post("/api/v1/auth/login", json={"email": USER, "password": USER_PW})
check(r.status_code == 403, "suspended account cannot sign back in")

print("\n== 13. last-admin protections ==")
r = c2.patch(f"/api/v1/admin/users/{admin['id']}", json={"role": "user"})
check(r.status_code == 400, f"cannot self-demote: {r.json()['detail']}")
r = c2.delete(f"/api/v1/admin/users/{admin['id']}")
check(r.status_code == 400, f"cannot self-delete: {r.json()['detail']}")

print("\n== 14. admin resets a user's MFA ==")
c2.post(f"/api/v1/admin/users/{admin['id']}/reset-mfa")
fresh = users.get(admin["id"])
check(not fresh["totp_enabled"], "TOTP cleared by admin reset")

print("\n== 15. audit trail ==")
entries = c2.get("/api/v1/admin/audit").json()["entries"]
actions = [e["action"] for e in entries]
check("user.mfa_reset" in actions and "user.updated" in actions, "admin actions recorded")
for e in entries[:6]:
    print(f"          {e['ts'][11:]}  {e['action']:22} {str(e['actor']):24} {e['target']}")

print("\nALL ACCOUNT TESTS PASSED")

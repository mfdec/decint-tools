"""End-to-end exercise of the profile page's two security forms.

Password change: the current password is required and counted against the
login lockout, the session making the request survives while every other one
dies, and the account holder is told by email.

Email change: the address moves only when a link mailed to the NEW address is
opened — never on request — and asking for an address somebody else already
holds is indistinguishable from asking for a free one.

No real SMTP anywhere — `mail.send` is swapped for a capture list, and
`send_soon` is made synchronous so assertions don't race a worker thread.
Runs against a throwaway database.
"""

import os
import tempfile

from fastapi.testclient import TestClient

import app.config as cfg

# Mutate the settings singleton in place (see test_email_flows.py for why).
cfg.settings.analytics_db = os.path.join(tempfile.mkdtemp(), "test.db")
cfg.settings.operator_token = ""
cfg.settings.cookie_secure = False
cfg.settings.hcaptcha_site_key = ""
cfg.settings.hcaptcha_secret = ""
cfg.settings.smtp_host = "smtp.invalid"
cfg.settings.smtp_from = "noreply@decint.tools"
cfg.settings.smtp_from_name = "DECINT"
cfg.settings.admin_alert_email = ""
cfg.settings.public_base_url = "https://decint.tools"
cfg.settings.signup_enabled = True
cfg.settings.signup_default_status = "active"
cfg.settings.signup_max_per_ip_per_hour = 50
cfg.settings.password_reset_max_per_ip_per_hour = 200
cfg.settings.email_change_ttl_minutes = 60

from app import auth as _auth
from app import db as _db
from app.main import app
from app.routers import auth as auth_router
from app.services import email_change, mail, users

_db._conn = None
_db.get_conn()

PW = "correct-horse-battery-staple"
NEW_PW = "a-brand-new-long-password"
ALICE = "alice@example.com"
BOB = "bob@example.com"

ok = lambda m: print(f"   ok    {m}")
bad = lambda m: print(f"   FAIL  {m}")


def check(cond, msg):
    (ok if cond else bad)(msg)
    assert cond, msg


# ─── capture outgoing mail ────────────────────────────────────────────
SENT: list[dict] = []


def _capture(to, subject, body):
    SENT.append({"to": to, "subject": subject, "body": body})
    return True


mail.send = _capture
mail.send_soon = lambda to, subject, body: _capture(to, subject, body)


def sent_to(addr):
    return [m for m in SENT if m["to"] == addr]


def last_to(addr):
    msgs = sent_to(addr)
    return msgs[-1] if msgs else None


def token_in(msg):
    return msg["body"].split("token=")[1].split()[0]


def login(email, password):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.json().get("authenticated") is True, r.text
    return c


def signed_in(c):
    return c.get("/api/v1/auth/session").json()["authenticated"]


def change_email(c, new, pw=PW):
    return c.post("/api/v1/auth/email/change",
                  json={"new_email": new, "current_password": pw})


alice = users.create(ALICE, PW, username="alice")
bob = users.create(BOB, PW, username="bob")

print("\n== 1. both forms refuse an anonymous caller ==")
anon = TestClient(app)
r = anon.post("/api/v1/auth/password",
              json={"current_password": PW, "new_password": NEW_PW})
check(r.status_code == 401, "password change needs a session")
r = change_email(anon, "x@example.com")
check(r.status_code == 401, "email change needs a session")

# ═════════════════════════ password ═════════════════════════

print("\n== 2. password change: validation ==")
a1 = login(ALICE, PW)      # the browser she is changing it from
a2 = login(ALICE, PW)      # another device
SENT.clear()

r = a1.post("/api/v1/auth/password",
            json={"current_password": "not-the-password-1", "new_password": NEW_PW})
check(r.status_code == 401, "wrong current password refused")
check(r.json()["detail"] == "Current password is incorrect.", "…with a plain message")
r = a1.post("/api/v1/auth/password",
            json={"current_password": PW, "new_password": "short"})
check(r.status_code == 400, f"weak new password refused: {r.json()['detail']}")
check(users.verify_password(users.get(alice["id"])["password_hash"], PW),
      "nothing changed after either refusal")
check(len(SENT) == 0, "no email for a refused change")

print("\n== 3. password change: success ==")
r = a1.post("/api/v1/auth/password",
            json={"current_password": PW, "new_password": NEW_PW})
check(r.status_code == 200 and r.json()["changed"] is True, "password changed")
check(signed_in(a1), "the session that made the change is STILL signed in")
check(not signed_in(a2), "every other session was signed out")
check(login(ALICE, NEW_PW) is not None, "new password works")
r = TestClient(app).post("/api/v1/auth/login", json={"email": ALICE, "password": PW})
check(r.status_code == 401, "old password does not")
note = last_to(ALICE)
check(note is not None and "changed" in note["subject"].lower(),
      "account holder told by email")
check("every other session" in note["body"], "…and told what happened to the sessions")
PW_NOW = NEW_PW

print("\n== 4. wrong guesses from a live session feed the login lockout ==")
users.create("locky@example.com", PW)
lk = login("locky@example.com", PW)
for _ in range(cfg.settings.login_max_attempts):
    r = lk.post("/api/v1/auth/password",
                json={"current_password": "wrong-guess-here", "new_password": NEW_PW})
    check(r.status_code == 401, "wrong guess refused")
r = lk.post("/api/v1/auth/password",
            json={"current_password": PW, "new_password": NEW_PW})
check(r.status_code == 423, "the CORRECT password is refused once locked")
r = change_email(lk, "locky2@example.com")
check(r.status_code == 423, "the email form is behind the same lock")
r = TestClient(app).post("/api/v1/auth/login",
                         json={"email": "locky@example.com", "password": PW})
check(r.status_code == 423, "and the login itself is locked too")

# ═════════════════════════ email ═════════════════════════

print("\n== 5. email change: refusals send nothing ==")
SENT.clear()
r = change_email(a1, "fresh@example.com", pw="not-the-password-1")
check(r.status_code == 401, "wrong password refused")
r = change_email(a1, "not-an-address", pw=PW_NOW)
check(r.status_code == 400, f"invalid address refused: {r.json()['detail']}")
r = change_email(a1, ALICE.upper(), pw=PW_NOW)
check(r.status_code == 400, "your own address (any case) refused")
check(len(SENT) == 0, "no email for any refusal")
check(users.get(alice["id"])["email"] == ALICE, "address untouched")

print("\n== 6. asking for a TAKEN address looks exactly like asking for a free one ==")
SENT.clear()
free = change_email(a1, "free@example.com", pw=PW_NOW)
free_mail = last_to("free@example.com")
SENT.clear()
taken = change_email(a1, BOB, pw=PW_NOW)
check(free.status_code == taken.status_code == 200, "same status for both")
check(free.json() == taken.json(), "byte-identical body — no account enumeration")
check(len(sent_to(BOB)) == 0, "nothing is sent to the address that is taken")
check(free_mail is not None, "…while the free one did get its link")

print("\n== 7. the link goes to the NEW address, and nothing changes until it's opened ==")
SENT.clear()
NEW = "alice.new@example.com"
r = change_email(a1, NEW, pw=PW_NOW)
check(r.status_code == 200 and r.json()["sent"] is True, "request accepted")
m = last_to(NEW)
check(m is not None, "confirmation link mailed to the new address")
check(len(sent_to(ALICE)) == 0, "…and NOT to the old one yet")
check("https://decint.tools/confirm-email?token=" in m["body"],
      "link is built from PUBLIC_BASE_URL")
check(users.get(alice["id"])["email"] == ALICE, "address is still the old one")
token = token_in(m)

print("\n== 8. opening the link swaps the address ==")
SENT.clear()
opener = TestClient(app)          # a different browser, no session — that's normal
r = opener.post("/api/v1/auth/email/confirm", json={"token": token})
check(r.status_code == 200 and r.json()["email"] == NEW, "address confirmed")
row = users.get(alice["id"])
check(row["email"] == NEW and row["email_verified"] == 1, "stored, and marked verified")
old_note = last_to(ALICE)
check(old_note is not None and NEW in old_note["body"],
      "the OLD address is told, and told the new one")
check(a1.get("/api/v1/auth/session").json()["user"]["email"] == NEW,
      "her live session now reports the new address")
check(login(NEW, PW_NOW) is not None, "signing in with the new address works")
r = TestClient(app).post("/api/v1/auth/login", json={"email": ALICE, "password": PW_NOW})
check(r.status_code == 401, "the old address no longer signs in")

print("\n== 9. the link can't be replayed, forged, or outlive its window ==")
r = opener.post("/api/v1/auth/email/confirm", json={"token": token})
check(r.status_code == 400, "same link twice is refused")
r = change_email(a1, "twice@example.com", pw=PW_NOW)
check(r.status_code == 200, f"second request accepted ({r.status_code} {r.text})")
good = token_in(last_to("twice@example.com"))
r = opener.post("/api/v1/auth/email/confirm", json={"token": good[:-2] + "xx"})
check(r.status_code == 400, "a tampered token is refused")
real_ttl = email_change._ttl_seconds
email_change._ttl_seconds = lambda: -1
r = opener.post("/api/v1/auth/email/confirm", json={"token": good})
check(r.status_code == 400, "an expired token is refused")
email_change._ttl_seconds = real_ttl
r = opener.post("/api/v1/auth/email/confirm", json={"token": ""})
check(r.status_code == 400, "an empty token is refused")
check(users.get(alice["id"])["email"] == NEW, "none of that moved the address")

print("\n== 10. a link dies if the account's address moved on another way ==")
users.update(alice["id"], email="moved@example.com")
r = opener.post("/api/v1/auth/email/confirm", json={"token": good})
check(r.status_code == 400, "stale link (names an address the account no longer has)")
users.update(alice["id"], email=NEW)

print("\n== 11. the address is re-checked when the link is opened ==")
r = change_email(a1, "disputed@example.com", pw=PW_NOW)
disputed = token_in(last_to("disputed@example.com"))
users.update(bob["id"], email="disputed@example.com")      # somebody else got there first
r = opener.post("/api/v1/auth/email/confirm", json={"token": disputed})
check(r.status_code == 400, "refused — the address was taken in the meantime")
check(users.get(alice["id"])["email"] == NEW, "her address is unchanged")
users.update(bob["id"], email=BOB)

print("\n== 12. a suspended account can't use an old link ==")
r = change_email(a1, "suspended@example.com", pw=PW_NOW)
susp = token_in(last_to("suspended@example.com"))
users.update(alice["id"], status="suspended")
r = opener.post("/api/v1/auth/email/confirm", json={"token": susp})
check(r.status_code == 400, "refused — a suspension is not a thing an old email undoes")
users.update(alice["id"], status="active")

print("\n== 13. a reset link mailed to the old address dies with the swap ==")
SENT.clear()
anon.post("/api/v1/auth/password/forgot", json={"email": NEW})
reset_token = token_in(last_to(NEW))
check(anon.get("/api/v1/auth/password/reset-check",
               params={"token": reset_token}).json()["valid"] is True,
      "reset link is live before the change")
r = change_email(a1, "afterreset@example.com", pw=PW_NOW)
r = opener.post("/api/v1/auth/email/confirm",
                json={"token": token_in(last_to("afterreset@example.com"))})
check(r.status_code == 200, "address changed")
check(anon.get("/api/v1/auth/password/reset-check",
               params={"token": reset_token}).json()["valid"] is False,
      "…and the reset link mailed to the old address no longer works")

print("\n== 14. guard rails ==")
# Break-glass has no row to change; fake the dependency, since the real one
# can't exist alongside the accounts this script created.
app.dependency_overrides[_auth.require_session] = lambda: {
    "id": 0, "email": "operator@localhost", "role": "admin", "break_glass": True,
}
bg = TestClient(app)
r = bg.post("/api/v1/auth/password",
            json={"current_password": PW, "new_password": NEW_PW})
check(r.status_code == 400, "break-glass can't change a password")
r = change_email(bg, "x@example.com")
check(r.status_code == 400, "break-glass can't change an email")
app.dependency_overrides.clear()

cfg.settings.smtp_host = ""
check(not mail.available(), "relay switched off")
r = change_email(a1, "norelay@example.com", pw=PW_NOW)
check(r.status_code == 503, "email change says so instead of failing silently")
check("email delivery" in r.json()["detail"], f"…plainly: {r.json()['detail']}")
cfg.settings.smtp_host = "smtp.invalid"

cfg.settings.password_reset_max_per_ip_per_hour = 2
auth_router._reset_requests.clear()
codes = [change_email(a1, f"burst{i}@example.com", pw=PW_NOW).status_code for i in range(3)]
check(codes == [200, 200, 429], f"requests are throttled per address: {codes}")
cfg.settings.password_reset_max_per_ip_per_hour = 200

print("\n== 15. signup still validates the address the same way ==")
r = TestClient(app).post("/api/v1/auth/signup",
                         json={"email": "nope", "username": "newguy", "password": PW})
check(r.status_code == 400 and "valid email" in r.json()["detail"],
      "signup rejects a malformed address")

print("\nAll profile checks passed.\n")

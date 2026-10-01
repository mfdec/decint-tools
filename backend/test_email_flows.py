"""End-to-end exercise of the email hooks on the sign-in path.

Covers what the SMTP settings actually buy you: a signup that tells somebody,
an approval that reaches the person waiting for it, and a password reset that
works without an operator at a shell prompt.

No real SMTP anywhere — `mail.send` is swapped for a capture list, and
`send_soon` is made synchronous so assertions don't race a worker thread.
Runs against a throwaway database.
"""

import os
import tempfile

from fastapi.testclient import TestClient

import app.config as cfg

# Mutate the settings singleton in place rather than rebuilding it. Every
# module does `from ..config import settings`, so they all hold a reference to
# THIS object — reassigning cfg.settings would leave them pointing at the old
# one, which is exactly what happens when the whole suite runs and another test
# module has already imported them.
cfg.settings.analytics_db = os.path.join(tempfile.mkdtemp(), "test.db")
cfg.settings.operator_token = ""
# TestClient speaks http://testserver, so a Secure cookie would never come back.
cfg.settings.cookie_secure = False
cfg.settings.hcaptcha_site_key = ""
cfg.settings.hcaptcha_secret = ""
# The point of the exercise: mail configured, signups held for approval.
cfg.settings.smtp_host = "smtp.invalid"
cfg.settings.smtp_from = "noreply@decint.tools"
cfg.settings.smtp_from_name = "DECINT"
cfg.settings.admin_alert_email = ""
cfg.settings.public_base_url = "https://decint.tools"
cfg.settings.signup_enabled = True
cfg.settings.signup_default_status = "pending"
# Pin the operator-approval flow this script exercises. Without this, a
# deployment that turns self-activation on via its env would flip the branch
# and the applicant would get an activation link instead of the approval note.
cfg.settings.signup_email_activation = False
cfg.settings.signup_max_per_ip_per_hour = 50
cfg.settings.password_reset_max_per_ip_per_hour = 50

from app import db as _db
from app.main import app
from app.services import mail, users

_db._conn = None
_db.get_conn()

ADMIN = "ops@decint.tools"
ADMIN_PW = "correct-horse-battery-staple"
NEWBIE = "newbie@example.com"
NEWBIE_PW = "another-long-password-99"

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


print("\n== 0. the relay is considered configured ==")
check(mail.available(), "mail.available() is True with SMTP_HOST + SMTP_FROM")
check(cfg.settings.smtp_from == "noreply@decint.tools", "sending as noreply@decint.tools")

users.create(ADMIN, ADMIN_PW, username="ops", role="admin", tier="enterprise")
admin = TestClient(app)
r = admin.post("/api/v1/auth/login", json={"email": ADMIN, "password": ADMIN_PW})
check(r.json()["authenticated"] is True, "admin signed in")

print("\n== 1. login page advertises the reset flow ==")
r = admin.get("/api/v1/auth/signup-info")
check(r.json()["password_reset_enabled"] is True, "password_reset_enabled reported to the UI")

print("\n== 2. signup emails the applicant, but no longer the admins ==")
SENT.clear()
c = TestClient(app)
r = c.post("/api/v1/auth/signup",
           json={"email": NEWBIE, "username": "newbie", "password": NEWBIE_PW})
check(r.status_code == 201, "signup accepted")
applicant = last_to(NEWBIE)
check(applicant is not None, "applicant was emailed")
check("approval" in applicant["subject"].lower(), f"subject: {applicant['subject']}")
check(last_to(ADMIN) is None,
      "admins are NOT alerted on a free signup — that noise was removed")

print("\n== 3. a pending account is told WHY it can't sign in ==")
r = c.post("/api/v1/auth/login", json={"email": NEWBIE, "password": NEWBIE_PW})
check(r.status_code == 403, "403, not 401 — the password was right")
check("approve" in r.json()["detail"].lower(),
      f"actionable message: {r.json()['detail']}")

print("\n== 4. approval reaches the person waiting for it ==")
SENT.clear()
uid = users.get_by_email(NEWBIE)["id"]
r = admin.patch(f"/api/v1/admin/users/{uid}", json={"status": "active"})
check(r.status_code == 200, "admin approved the account")
approved = last_to(NEWBIE)
check(approved is not None and "approved" in approved["subject"].lower(),
      "approval email sent")
r = c.post("/api/v1/auth/login", json={"email": NEWBIE, "password": NEWBIE_PW})
check(r.json().get("authenticated") is True, "the account can now sign in")

print("\n== 4b. a PAID plan is what alerts the operators now ==")
from app.services.billing import store as billing_store

SENT.clear()
billing_store.grant(uid, "pro", "stripe",
                    expires_at="2027-01-01T00:00:00+00:00", reason="test")
paid = last_to(ADMIN)
check(paid is not None, "admin alerted when an account reaches a paid plan")
check("pro" in paid["body"].lower() and "newbie" in paid["body"],
      "alert names the plan and the account")

SENT.clear()
billing_store.grant(uid, "pro", "stripe",
                    expires_at="2027-02-01T00:00:00+00:00", reason="renewal")
check(last_to(ADMIN) is None, "a renewal of the SAME plan stays quiet — no new alert")

SENT.clear()
billing_store.grant(uid, "starter", "manual", expires_at=None, reason="comp")
check(last_to(ADMIN) is None, "an admin comp (source=manual) raises no alert")

print("\n== 5. forgotten password: identical answer for real and unknown ==")
SENT.clear()
anon = TestClient(app)
real = anon.post("/api/v1/auth/password/forgot", json={"email": NEWBIE})
ghost = anon.post("/api/v1/auth/password/forgot", json={"email": "ghost@nowhere.example"})
check(real.status_code == ghost.status_code == 200, "same status for both")
check(real.json() == ghost.json(), "byte-identical body — no account enumeration")
check(len(sent_to("ghost@nowhere.example")) == 0, "no email to the unknown address")

link_mail = last_to(NEWBIE)
check(link_mail is not None, "reset link emailed to the real account")
token = link_mail["body"].split("token=")[1].split()[0]
check(link_mail["body"].startswith("Someone asked"), "body explains what happened")
check("https://decint.tools/reset?token=" in link_mail["body"],
      "link is built from PUBLIC_BASE_URL")

print("\n== 6. the link is checkable, single-use, and survives a weak attempt ==")
r = anon.get("/api/v1/auth/password/reset-check", params={"token": token})
check(r.json()["valid"] is True, "reset-check says the link is good")

r = anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "short"})
check(r.status_code == 400, f"weak password refused: {r.json()['detail']}")
r = anon.get("/api/v1/auth/password/reset-check", params={"token": token})
check(r.json()["valid"] is True, "…and the link was NOT burned by that attempt")

SENT.clear()
NEW_PW = "a-brand-new-long-password"
r = anon.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW_PW})
check(r.status_code == 200, "password changed")
warn = last_to(NEWBIE)
check(warn is not None and "changed" in warn["subject"].lower(),
      "account holder warned out-of-band")

r = anon.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW_PW})
check(r.status_code == 400, "the same link cannot be used twice")

print("\n== 7. the reset actually took, and evicted the old session ==")
r = c.get("/api/v1/auth/session")
check(r.json()["authenticated"] is False, "the pre-reset session was signed out")
fresh = TestClient(app)
r = fresh.post("/api/v1/auth/login", json={"email": NEWBIE, "password": NEW_PW})
check(r.json().get("authenticated") is True, "new password works")
r = TestClient(app).post("/api/v1/auth/login", json={"email": NEWBIE, "password": NEWBIE_PW})
check(r.status_code == 401, "old password does not")

print("\n== 8. a suspended account gets no reset link ==")
SENT.clear()
users.update(uid, status="suspended")
r = anon.post("/api/v1/auth/password/forgot", json={"email": NEWBIE})
check(r.status_code == 200, "same 200 as always")
check(len(sent_to(NEWBIE)) == 0, "but no link — reset is not a way around a suspension")
users.update(uid, status="active")

print("\n== 9. email second factor is offered and delivers a code ==")
SENT.clear()
r = fresh.post("/api/v1/auth/mfa/enable", json={"method": "email"})
check(r.json()["enabled"] is True, "email 2FA enabled")
mfa_client = TestClient(app)
r = mfa_client.post("/api/v1/auth/login", json={"email": NEWBIE, "password": NEW_PW})
check(r.json()["mfa_required"] is True, "login now stops for a second factor")
check(r.json()["sent"] == "email", "…and reports that a code went by email")
code_mail = last_to(NEWBIE)
check(code_mail is not None and "sign-in code" in code_mail["subject"].lower(),
      "the code email was sent")
code = [w for w in code_mail["body"].split() if w.isdigit() and len(w) == 6][0]
r = mfa_client.post("/api/v1/auth/mfa/verify",
                    json={"challenge": r.json()["challenge"], "method": "email", "code": code})
check(r.json()["authenticated"] is True, "the emailed code completes the sign-in")

print("\nAll email-flow checks passed.\n")

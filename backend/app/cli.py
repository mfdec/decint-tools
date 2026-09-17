"""Account management from the shell — for bootstrapping a server.

    python -m app.cli create-admin --email you@example.com
    python -m app.cli list
    python -m app.cli set-password --email you@example.com
    python -m app.cli set-role --email someone@example.com --role admin
    python -m app.cli set-status --email them@example.com --status active
    python -m app.cli reset-mfa --email you@example.com
    python -m app.cli mail-test --to you@example.com

Run from the backend/ directory with the venv active. The password is read
from a prompt, never from argv, so it doesn't land in your shell history.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import db
from .config import settings
from .services import mail, tokens, twofactor, users


def _password(confirm: bool = True) -> str:
    pw = getpass.getpass("Password: ")
    if confirm and pw != getpass.getpass("Confirm: "):
        sys.exit("Passwords did not match.")
    problem = users.password_problem(pw)
    if problem:
        sys.exit(problem)
    return pw


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.cli", description="DECINT accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-admin", help="create an administrator")
    p.add_argument("--email", required=True)
    p.add_argument("--tier", default="enterprise", choices=users.TIERS)

    p = sub.add_parser("create-user", help="create a normal account")
    p.add_argument("--email", required=True)
    p.add_argument("--role", default="user", choices=users.ROLES)
    p.add_argument("--tier", default="free", choices=users.TIERS)

    sub.add_parser("list", help="list accounts")

    p = sub.add_parser("set-password", help="reset an account password")
    p.add_argument("--email", required=True)

    p = sub.add_parser("set-role", help="change an account's role")
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True, choices=users.ROLES)

    p = sub.add_parser("set-status", help="active | pending | suspended")
    p.add_argument("--email", required=True)
    p.add_argument("--status", required=True, choices=users.STATUSES)
    p.add_argument("--quiet", action="store_true",
                   help="don't email the account holder about an approval")

    p = sub.add_parser("mail-test", help="send a test email through the configured relay")
    p.add_argument("--to", required=True, help="where to send it")

    p = sub.add_parser("set-tier", help="change an account's tier")
    p.add_argument("--email", required=True)
    p.add_argument("--tier", required=True, choices=users.TIERS)

    p = sub.add_parser("reset-mfa", help="clear a user's second factor")
    p.add_argument("--email", required=True)

    p = sub.add_parser("token-create",
                       help="issue a login token (creates a token-only account if new)")
    p.add_argument("--email", required=True)
    p.add_argument("--label", default=None, help="a note, e.g. who it's for")
    p.add_argument("--tier", default="free", choices=users.TIERS,
                   help="tier for a newly-created account")

    p = sub.add_parser("token-list", help="list an account's login tokens")
    p.add_argument("--email", required=True)

    p = sub.add_parser("token-revoke", help="revoke one login token by id")
    p.add_argument("--id", required=True, type=int)

    p = sub.add_parser("token-revoke-all", help="revoke all of an account's tokens")
    p.add_argument("--email", required=True)

    args = ap.parse_args()
    db.get_conn()

    def need(email: str) -> dict:
        u = users.get_by_email(email)
        if not u:
            sys.exit(f"No account: {email}")
        return u

    if args.cmd in ("create-admin", "create-user"):
        role = "admin" if args.cmd == "create-admin" else args.role
        try:
            u = users.create(args.email, _password(), role=role, tier=args.tier)
        except ValueError as e:
            sys.exit(str(e))
        print(f"created #{u['id']}  {u['email']}  role={u['role']} tier={u['tier']}")
        print("Enrol a second factor from the console once you sign in.")
        return 0

    if args.cmd == "list":
        rows = users.listing(limit=1000)["users"]
        if not rows:
            print("(no accounts — the OPERATOR_TOKEN bootstrap login is still active)")
            return 0
        print(f"{'id':>4}  {'email':32} {'role':9} {'tier':13} {'status':10} mfa")
        for u in rows:
            mfa = ",".join(
                m for m, on in (
                    ("totp", u["totp_enabled"]),
                    ("email", u["email_otp_enabled"]),
                    ("sms", u["sms_otp_enabled"]),
                ) if on
            ) or "-"
            print(f"{u['id']:>4}  {u['email']:32} {u['role']:9} {u['tier']:13} "
                  f"{u['status']:10} {mfa}")
        return 0

    if args.cmd == "set-password":
        u = need(args.email)
        users.set_password(u["id"], _password())
        users.revoke_all_sessions(u["id"])
        print(f"password updated for {u['email']}; sessions signed out")
        return 0

    if args.cmd == "set-role":
        u = need(args.email)
        users.update(u["id"], role=args.role)
        print(f"{u['email']} role -> {args.role}")
        return 0

    if args.cmd == "set-tier":
        u = need(args.email)
        users.update(u["id"], tier=args.tier)
        print(f"{u['email']} tier -> {args.tier}")
        return 0

    if args.cmd == "set-status":
        u = need(args.email)
        was = u["status"]
        users.update(u["id"], status=args.status)
        if args.status != "active":
            # Match the admin API: switching someone off ends their sessions
            # now rather than whenever the cookie happens to expire.
            users.revoke_all_sessions(u["id"])
        print(f"{u['email']} status {was} -> {args.status}")
        if args.status == "active" and was != "active" and not args.quiet:
            subject, text = mail.account_approved(u.get("username") or u["email"])
            # Blocking send here on purpose: at a shell prompt you want to know
            # whether it actually went, and there is no event loop to protect.
            if mail.send(u["email"], subject, text):
                print(f"approval email sent to {u['email']}")
            elif mail.available():
                print("approval email FAILED — check the log for the reason")
            else:
                print("no SMTP configured, so no approval email was sent")
        return 0

    if args.cmd == "mail-test":
        if not mail.available():
            sys.exit("SMTP_HOST and SMTP_FROM must both be set in backend/.env")
        print(f"sending as {settings.smtp_from_name} <{settings.smtp_from}> "
              f"via {settings.smtp_host}:{settings.smtp_port} …")
        ok = mail.send(
            args.to,
            "DECINT mail test",
            "If you are reading this, the DECINT backend can send email.\n\n"
            "Sign-in codes, approval notices and password-reset links will "
            "reach this inbox.\n",
        )
        print("sent" if ok else "FAILED — the reason is on the line above")
        return 0 if ok else 1

    if args.cmd == "reset-mfa":
        u = need(args.email)
        twofactor.disable_all(u["id"])
        print(f"second factor cleared for {u['email']}")
        return 0

    if args.cmd == "token-create":
        u = users.get_by_email(args.email)
        created = False
        if not u:
            # No account yet: provision a password-less one the holder reaches
            # only via the token. (create_oauth is our "no password" path.)
            try:
                u = users.create_oauth(args.email, tier=args.tier, status="active")
            except ValueError as e:
                sys.exit(str(e))
            created = True
            users.audit("user.provisioned", target=u["email"], detail="token-only")
        token = tokens.create(u["id"], label=args.label)
        users.audit("token.created", target=u["email"], detail=args.label or "")
        if created:
            print(f"created token-only account #{u['id']}  {u['email']}  tier={u['tier']}\n")
        print(f"login token for {u['email']}:\n")
        print(f"    {token}\n")
        print("Give this to the account holder now — it is shown ONCE and cannot")
        print("be recovered. They sign in with it on the login page's 'Login token' tab.")
        return 0

    if args.cmd == "token-list":
        u = need(args.email)
        rows = tokens.list_for(u["id"])
        if not rows:
            print(f"{u['email']} has no login tokens.")
            return 0
        print(f"{'id':>4}  {'label':24} {'created':20} {'last used':20} state")
        for t in rows:
            print(f"{t['id']:>4}  {(t['label'] or '-'):24} {t['created_at'][:19]:20} "
                  f"{(t['last_used_at'] or '-')[:19]:20} "
                  f"{'revoked' if t['revoked'] else 'active'}")
        return 0

    if args.cmd == "token-revoke":
        ok = tokens.revoke(args.id)
        print(f"token #{args.id} revoked" if ok else f"token #{args.id}: nothing to revoke")
        return 0

    if args.cmd == "token-revoke-all":
        u = need(args.email)
        n = tokens.revoke_all(u["id"])
        print(f"revoked {n} token(s) for {u['email']}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

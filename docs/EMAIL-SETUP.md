# Email setup — noreply@decint.tools

The sign-in path now depends on email in four places. All four are dark until
`SMTP_HOST` and `SMTP_FROM` are both set, and all four fail *visibly* rather
than silently once they are:

| What | Where it fires | Without SMTP |
|---|---|---|
| Sign-in code (email 2FA) | `/auth/login`, `/auth/mfa/send` | the method is hidden and cannot be enabled |
| Signup notice + admin alert | `/auth/signup` | nobody is told a signup happened |
| Account-approved notice | admin sets status → `active` | the applicant never learns they're in |
| Password reset link | `/auth/password/forgot` | "Forgot password?" is hidden; the endpoint returns 503 |

`decint.tools` had no MX and no SPF record when this was written, so mail is
being set up from scratch. Sending is done through a relay rather than a local
Postfix: this box has no sending reputation, and a bare VPS mailing Hotmail and
Gmail as a brand-new domain goes to spam or gets refused outright.

---

## 1. DNS — prove the domain is yours

At **resend.com** ▸ *Domains* ▸ *Add Domain* ▸ `decint.tools`. It prints three
records; add them in Cloudflare DNS exactly as shown, all **DNS only** (grey
cloud — TXT and MX are never proxied anyway):

| Type | Name | Value |
|---|---|---|
| `MX` | `send` | `feedback-smtp.<region>.amazonses.com`, priority `10` |
| `TXT` | `send` | `v=spf1 include:amazonses.com ~all` |
| `TXT` | `resend._domainkey` | the DKIM key Resend generates — copy it verbatim |

These live on the `send.decint.tools` subdomain, so they do not collide with
Cloudflare Email Routing if you ever add an inbox on the root domain.

Worth adding while you are in there — it tells receivers what to do with mail
that forges your domain, and `p=none` only asks for reports, it rejects nothing:

| Type | Name | Value |
|---|---|---|
| `TXT` | `_dmarc` | `v=DMARC1; p=none; rua=mailto:you@decint.tools` |

Wait for Resend to show the domain **Verified**, then *API Keys* ▸ *Create* and
copy the `re_…` key. It is shown once.

Check propagation from the server:

```bash
dig +short TXT resend._domainkey.decint.tools
```

## 2. Point the backend at it

`backend/.env` already has an empty `SMTP_*` block. Fill it in rather than
appending a second one — later lines win, so a duplicate block is a trap that
looks fine and behaves unpredictably.

Back it up first:

```bash
sudo -u decint cp /var/www/html/decint-tools/backend/.env /var/www/html/decint-tools/backend/.env.bak.$(date +%Y%m%d%H%M%S)
```

Set the three values that aren't secret, in place:

```bash
sudo -u decint sed -i -e 's|^SMTP_HOST=.*|SMTP_HOST=smtp.resend.com|' -e 's|^SMTP_USER=.*|SMTP_USER=resend|' -e 's|^SMTP_FROM=.*|SMTP_FROM=noreply@decint.tools|' /var/www/html/decint-tools/backend/.env
```

Add the keys that don't exist in the file yet:

```bash
sudo -u decint tee -a /var/www/html/decint-tools/backend/.env >/dev/null <<'ENV'

# Added for noreply@decint.tools
SMTP_FROM_NAME=DECINT
ADMIN_ALERT_EMAIL=
PASSWORD_RESET_TTL_MINUTES=30
PASSWORD_RESET_MAX_PER_IP_PER_HOUR=5
ENV
```

Then paste the API key into `SMTP_PASSWORD=` by hand. Putting it in a command
would leave the key in your shell history and in the process list:

```bash
sudo -u decint nano /var/www/html/decint-tools/backend/.env
```

`PUBLIC_BASE_URL=https://decint.tools` is already set, which is what reset links
are built from. Without it the emailed link is a bare path and unusable.

## 3. Restart and prove it works

```bash
sudo systemctl restart decint-api && sudo systemctl status decint-api --no-pager
```

The startup line tells you which way it went:

```
[decint] email: sending as noreply@decint.tools via smtp.resend.com
```

Send a real message end to end:

```bash
cd /var/www/html/decint-tools && sudo -u decint python3 tools/accounts.py mail-test --to you@example.com
```

On failure the reason is printed and logged — wrong key, blocked port, or an
unverified domain all look different. Watch the log while you test:

```bash
sudo journalctl -u decint-api -f
```

## 4. Decide what happens to new signups

`SIGNUP_DEFAULT_STATUS=pending` holds every new account until an operator
approves it. That is a real choice, not a bug — but it only works now that the
applicant and the admins both get told. To let people straight in instead:

```bash
sudo -u decint sed -i 's/^SIGNUP_DEFAULT_STATUS=pending/SIGNUP_DEFAULT_STATUS=active/' /var/www/html/decint-tools/backend/.env && sudo systemctl restart decint-api
```

Approving someone by hand — this emails them:

```bash
cd /var/www/html/decint-tools && sudo -u decint python3 tools/accounts.py set-status --email them@example.com --status active
```

## 5. Turn on the email second factor

Per account, from *Console ▸ Settings ▸ Two-factor*, or the API. An
authenticator app is still the stronger option; email 2FA is only as strong as
the mailbox it lands in. Nothing is enabled automatically.

---

## Staff mailboxes are a different problem

Everything above is about **sending** — the relay the app pushes transactional
mail through. It does not give anyone a mailbox. `noreply@decint.tools` is a
From address, not an inbox; nothing arrives there and nobody logs into it.

For real addresses staff read and reply from, you need mail *hosting*, and the
two obvious free options are not interchangeable:

- **Cloudflare Email Routing** — forwarding only. No mailboxes, no login, and
  you cannot send as the address. It forwards mail arriving at your domain to
  an inbox each person already owns and has verified. Useful for role aliases
  if you already have inboxes; useless as staff email.
- **Zoho Mail, Forever Free** — 5 users, 5 GB each, one domain, webmail and
  mobile app only (no IMAP/POP on free). This is the one that actually gives
  five people five addresses.

They are mutually exclusive on the same domain: Email Routing requires
Cloudflare's own MX records on the root, and a domain has one set of MX records.
Picking Zoho rules out Routing on `decint.tools`, permanently, until you remove
the Zoho MX records.

Neither replaces the relay above — Zoho's free tier has no SMTP access, so the
console still cannot send through it. Resend stays on the `send.decint.tools`
subdomain precisely so its SPF and DKIM never collide with a mail host's
records on the root.

Full walkthrough, including creating each staff mailbox and free role aliases:
<https://claude.ai/code/artifact/e259301c-b2ee-46d0-9713-4f1a5692d812>

---

## Troubleshooting

**Nothing arrives, no error.** `mail.send` logs every attempt at INFO and every
failure at ERROR under the `decint.mail` logger. `journalctl -u decint-api -f`
shows both.

**"535 Authentication failed".** `SMTP_USER` is the literal string `resend`,
not your email address. The password is the `re_…` API key.

**Mail lands in spam.** The DKIM record has not propagated, or `SMTP_FROM` is
not on the verified domain. Both must be true for the signature to attach.

**Reset link says "expired" immediately.** Links are single-use and invalidated
by requesting another — if you clicked *Send reset link* twice, only the second
email works.

# Admin stack (executor / Stressor / Methods / Patch)

A second, independent copy of the site for admin-only features that don't belong on the
public site: the code executor ("Methods"/"Stressor" — runs Python locally on request,
used for authorized load-testing of infrastructure you own) and "Patch" (a Claude-backed
chat box in the admin panel; conversational only, cannot run code or touch the server).

`main` and the public `decint-api`/`decint-web` services never carry this code. It lives on
branch `custom-script-executor-41987`, in its own checkout, database and secrets.

## Layout

| | Public site | Admin stack |
|---|---|---|
| Checkout | `/var/www/html/decint-tools` (`main`) | `/var/www/html/decint-tools-admin` (`custom-script-executor-41987`) |
| API | `decint-api.service` — 127.0.0.1:8000 | `decint-api-admin.service` — 127.0.0.1:8001 |
| Web | `decint-web.service` — 127.0.0.1:3000 | `decint-web-admin.service` — 127.0.0.1:3002 |
| Database | `backend/data/analytics.db` (real visitor data) | its own `backend/data/analytics.db`, empty |
| Secrets | real (Stripe, mail, etc.) | freshly generated, third-party keys blank |

**Deliberately isolated**, not sharing the live DB/secrets: this stack runs a whole
separate, less-reviewed codebase with a live code-execution endpoint, so it gets no
standing access to production credentials or the visitor database (which holds IPs).

## Current exposure: none

Both services are bound to `127.0.0.1` only — confirmed not listening on the public
interface, and `https://admin.decint.tools` hits the nginx default-catch-all (404), not this
app, because there is no vhost for it yet. DNS exists (Cloudflare, proxied) but nothing is
wired to it. Reach it meanwhile with an SSH tunnel:

```bash
ssh -L 3002:127.0.0.1:3002 box
```

then open `http://127.0.0.1:3002` in your own browser.

## First login (break-glass)

The admin DB has zero accounts, so there's nothing to log into yet. `backend/.env` has a
freshly generated `OPERATOR_TOKEN` (never shown in chat — read it yourself):

```bash
sudo grep ^OPERATOR_TOKEN= /var/www/html/decint-tools-admin/backend/.env
```

POST that token to `/api/v1/auth/break-glass` (check `routers/auth.py` for the exact field
name) to get a one-time admin session, then immediately create your real account —
`sudo -u decint /var/www/html/decint-tools-admin/backend/.venv/bin/python -m app.cli create-admin --email you@example.com`
(prompts for a password, never touches argv/shell history) — since break-glass stops working
the instant any account exists.

## Before this goes public

1. **Ownership verification for stress-testing.** Today this is safe because it's admin-only
   and you personally attest each target is infrastructure you own. Before this — or any part
   of it — is ever opened beyond you, it needs a real ownership-proof step (e.g. a DNS TXT
   challenge, matching how domain/SSL verification works) so it can't be pointed at a third
   party. Don't skip this to move faster; it's the one thing standing between "load-testing
   tool" and "DDoS-for-hire."
2. **Cloudflare Access** — Zero Trust needs onboarding in the Cloudflare dashboard (pick a
   team name) before an Access app can gate `admin.decint.tools` at the edge. That's a
   dashboard step only you can do.
3. Only after Access (or an explicit alternative you choose, e.g. an nginx IP allowlist) is
   live: add the nginx vhost, get a cert (`certbot --nginx -d admin.decint.tools`), and update
   this doc. Note `/admin` and `/stressor` are gated client-side (matching how the branch built
   them), not by `middleware.ts` — fine behind Access (which gates before nginx even sees the
   request), not sufficient on its own.

## Maintaining it

Pull and rebuild like the public site (`docs/UPDATE-STEPS.md`), but in
`/var/www/html/decint-tools-admin` against `custom-script-executor-41987`, restarting
`decint-api-admin`/`decint-web-admin`. `npm ci` on this 2 GB box should run at normal
priority (a `Nice`-only `systemd-run`, no `OOMScoreAdjust`) — pairing `OOMScoreAdjust=1000`
with `npm ci` here once made it exit 0 after installing only 37 of ~400 packages, breaking
the next build in a confusing way (missing `tailwindcss` cascading into bogus
"module not found" errors for unrelated files). `next build` itself is fine deprioritized.

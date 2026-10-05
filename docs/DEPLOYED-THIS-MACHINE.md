# Deployment record — this machine

Deployed 2026-08-23. This box does **not** match `deploy/README.md` in two ways,
both deliberate:

| | Shipped runbook | This machine | Why |
|---|---|---|---|
| Reverse proxy | Caddy | **nginx** | requested |
| App root | `/opt/decint-tools` | **`/var/www/html/decint-tools`** | requested |

`deploy/decint-server-install.sh` was **not** run. Everything below was done by
hand, so re-running that script would fight this layout.

## Layout

```
/var/www/html/decint-tools/          app root, owned by the `decint` system user
  backend/.venv/                     Python 3.14 venv
  backend/.env                       mode 600 — secrets, never leaves this box
  backend/data/                      analytics.db + geoip/ (writable)
  frontend/.next/                    production build
/etc/systemd/system/decint-api.service
/etc/systemd/system/decint-web.service
/etc/nginx/sites-available/decint.tools
/etc/nginx/conf.d/cloudflare-realip.conf
```

Ports: nginx on 80 + 443 public (TLS since 2026-09-14); Next.js `127.0.0.1:3000` and FastAPI `127.0.0.1:8000`
are loopback-only. ufw allows 22/80/443 only.

## Two changes to the shipped units

**`decint-web.service`** appends `-- -H 127.0.0.1`. `next start` otherwise binds
`0.0.0.0`, which exposed :3000 on every interface — the runbook requires it stay
on loopback, and the shipped unit does not actually achieve that.

**`decint-api.service`** adds `--proxy-headers --forwarded-allow-ips 127.0.0.1`,
so the backend honours `X-Forwarded-Proto` from nginx (and only from nginx).

## Cloudflare real-IP is not optional here

`conf.d/cloudflare-realip.conf` rewrites `$remote_addr` from `CF-Connecting-IP`
for the 22 published Cloudflare ranges. Without it every visitor arrives as a
Cloudflare edge address, which silently breaks two things: visitor analytics
collapses to a handful of unique IDs, and `SIGNUP_MAX_PER_IP_PER_HOUR=5`
rate-limits the whole internet as one client.

Refresh the ranges when Cloudflare publishes new ones:

```bash
{ for u in ips-v4 ips-v6; do curl -sS "https://www.cloudflare.com/$u"; echo; done \
  | sed -E '/^[[:space:]]*$/d; s/[[:space:]]*$//; s/^/set_real_ip_from /; s/$/;/'
  echo; echo "real_ip_header CF-Connecting-IP;"; } > /etc/nginx/conf.d/cloudflare-realip.conf
nginx -t && systemctl reload nginx
```

nginx sets `X-Forwarded-For` to `$remote_addr` **explicitly** rather than
appending to the client's header. The backend reads `split(",")[0]`, so
appending would let a caller inject a forged left-most entry.

## Running the tests on this box

All four suites pass, but **only against the shipped `.env.example` defaults** —
not against this machine's production `.env`:

```bash
cd /var/www/html/decint-tools/backend
COOKIE_SECURE=false SIGNUP_DEFAULT_STATUS=active .venv/bin/python test_security.py
COOKIE_SECURE=false SIGNUP_DEFAULT_STATUS=active .venv/bin/python test_accounts.py
COOKIE_SECURE=false SIGNUP_DEFAULT_STATUS=active .venv/bin/python test_moderation.py
COOKIE_SECURE=false SIGNUP_DEFAULT_STATUS=active .venv/bin/python -m pytest -q tests/test_units.py
```

Both overrides are required and neither indicates a defect:

- `COOKIE_SECURE=true` is correct in production, but `TestClient` speaks plain
  HTTP, so a `Secure` cookie is never sent back. Login succeeds and every
  following request is 401. That surfaces as alarming-looking findings
  (`[CRITICAL] self-promotion possible — HTTP 401`) which are the opposite of a
  vulnerability: the tests assert 403, and 401 is *more* restrictive.
- `SIGNUP_DEFAULT_STATUS=pending` is correct in production; the tests sign up
  accounts and immediately use them, which a pending account cannot do.

`pytest` was added to the venv for the fourth suite; it is not in
`requirements.txt`.

## Updating

```bash
sudo -u decint bash -c "cd /var/www/html/decint-tools/frontend && npm ci && npm run build"
sudo systemctl restart decint-web     # backend-only change? restart decint-api
```

---

# Pricing model (changed 2026-08-28; re-priced 2026-09-17, twice)

| Tier | Key | Price | Quota |
|---|---|---|---|
| Free | `free` | — | 3, lifetime |
| Starter | `starter` | $4.95/mo · $49.50/yr | 500/mo |
| Pro | `pro` | $14.95/mo · $149.50/yr | 5,000/mo |
| Enterprise | `enterprise` | quoted | unmetered |

**2026-09-17, second pass.** Later the same day Starter moved from $2.95 to
$4.95/mo ($49.50/yr) and Pro from $9.95 to $14.95/mo ($149.50/yr). Keys,
quotas and features are unchanged, so no migration; only `plans.py` and the
frontend fallback changed. Stripe Prices are immutable, so four **new** live
Prices were created on the existing Starter/Pro products at the new amounts,
the $2.95/$9.95 ones archived (nothing was subscribed on them), and the
`STRIPE_PRICE_STARTER_*` / `STRIPE_PRICE_PRO_*` ids in `.env` swapped over;
`decint-api` and `decint-web` were restarted. Done here on 2026-09-17.

**2026-09-17 re-pricing.** To get users in the door the entry tier dropped
from $29 to $2.95 and took back its original name, Starter; Pro dropped from
$45 to $9.95. Yearly stays at ten months. That rename needed
`migrations/003_rename_tiers.py` (`essentials` → `starter`; run here — 2
`billing_orders` rows, no users). Stripe got four new Prices at the new amounts
and the old four were archived; `.env` now carries `STRIPE_PRICE_STARTER_*`
instead of `STRIPE_PRICE_ESSENTIALS_*`.

Tiers ladder by **depth, not tool count**. Every paid tier sees all three tools;
Pro adds full Tor mode, evidence hashes and corroboration scoring. There is no
five-tool tier because there are not five tools — inventing them would have put
features that do not exist next to a live checkout button.

On 2026-08-28 `starter`/`professional`/`custom` were renamed to
`essentials`/`pro`/`enterprise`. Because `plans.Plan.key` doubles as
`users.tier`, that required `migrations/002_rename_tiers.py` (already run here
— 3 rows). `users.TIERS` is now derived from the catalogue rather than
restated, so the two cannot drift.

## The packet sniffer

Removed from `lib/tools.ts`, the landing page and every tier's feature list. The
router, service, vendored `decint_sniffer.py` and the console tab all still
ship, gated behind `SNIFFER_ENABLED=false`. Nothing was deleted.

## Per-tool prices

`/tools` shows a standalone price per tool — $12 leaks, $19 dark-web, $5 password
checker — summing to $36 against Starter at $4.95. These are **anchors, not buy
buttons**: nothing is sold à la carte, and the page says so. Selling individual
tools would need a per-tool entitlement dimension that the tier-based model does
not have today.

## Stripe (updated 2026-09-12)

Live mode is fully wired on this box: a live restricted key, the live
`DECINT Starter` / `DECINT Pro` prices in `.env`, the webhook endpoint
`we_1UEERVJjzKSGZGhv3tKlJNks` at `https://decint.tools/api/v1/billing/webhook/stripe`
subscribed to every event the code handles, and a Billing Portal configuration
`bpc_1UEtsBJjzKSGZGhvxNId8EcD` (invoice history, card update, cancel at period
end, plan switching between the four prices, quantity locked at 1). It is the
account's default portal configuration — the account had none before, which
is why "Manage card & invoices" would have failed. `/billing/config` reports
`providers: ["stripe"]` here, and `GET /api/v1/billing/admin/status` shows
`webhook.missing_events: []` and `portal.plan_switching: true`.

The card rail now also changes plans in place for existing subscribers
(charged to the card on file, no second checkout), records renewals in the
payment history, closes abandoned checkouts, and reads the post-Basil webhook
shapes this account uses — see `docs/BILLING-SETUP.md`.

### DNS points here as of 2026-09-14

`decint.tools` and `www.decint.tools` A records were moved to this box
(**146.0.76.145**) on 2026-09-14 via the Cloudflare API, DNS-only (grey
cloud), TTL 300. TLS is terminated by **nginx on this box** with a Let's
Encrypt certificate (`/etc/letsencrypt/live/decint.tools/`, issued by
`certbot --nginx`, auto-renewed by `certbot.timer`; HTTP redirects to HTTPS).
`COOKIE_SECURE=true` was set at the same time.

Before that, `decint.tools` resolved to **62.238.106.137**, a separate host
serving an older build with `providers: []` — no Stripe key — so the public
pricing page fell back to "Email the operator" while this box was fully
wired. That host is now unreferenced by DNS; any accounts created there live
in its own `analytics.db` and do not exist here.

Because the records are grey-cloud, `cloudflare-realip.conf` is inert (no
traffic arrives from Cloudflare ranges, so `$remote_addr` is already the
visitor). It is left in place in case the records are ever proxied again;
in that case the zone's SSL mode is *Flexible* and must be changed to
*Full (strict)* now that the origin has a real certificate, or Cloudflare
will speak plain HTTP to :80 and get the redirect loop.

## Email (fixed 2026-09-14)

**This VPS blocks outbound 25, 465 and 587.** `smtp.resend.com:587` times out
from here, silently killing signup activation, password reset and admin
alerts — the first symptom after the DNS move was "I can't create an account".
Resend's alternate ports are open, so `.env` uses `SMTP_PORT=2587` (STARTTLS;
`2465` with `SMTP_SSL=true` also works). A fresh sending-only Resend key was
set the same day; the sending-domain records (`resend._domainkey` DKIM, MX +
SPF on `send.decint.tools`) were untouched by the DNS move. Verify with:

```bash
cd /var/www/html/decint-tools/backend && sudo -u decint .venv/bin/python -c \
  "from app.services import mail; print(mail.send('you@example.com','DECINT test','ok'))"
```

## Stripe webhook verified end-to-end (2026-09-14)

After the DNS move, a live Checkout Session was created and immediately
expired via the API; Stripe delivered `checkout.session.expired` to this box
and the endpoint answered 200 (signature verified, unknown session ignored).
A wrong `STRIPE_WEBHOOK_SECRET` answers 400 — that is the check to repeat if
the secret or the endpoint is ever recreated. The account settles in CAD;
prices are USD, so Stripe converts at payout.

## `tools/verify.py` cannot sign in here, by design

Three checks — `login succeeds`, `session reports authenticated`, `/console
reachable once signed in` — fail on this box. `verify.py`'s login helper uses
break-glass (only while the users table is empty) or two hard-coded fixture
accounts, `admin@decint.tools` / `victim@decint.tools`. This deployment has real
accounts and no fixtures. Seeding those accounts to make verify pass would be a
security hole; treat those three as expected on any hardened install.

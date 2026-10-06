# DECINT — decint.tools

Every signal. One console. A dark, purple, terminal-style OSINT + network
intelligence console: **leak database search**, the **Spider** correlation
tool, **dark-web search**, the **Password Checker**, **IP lookup**, **domain
lookup** and **phone lookup**.

- **Frontend** — Next.js 14 (App Router). The console (app switcher + ⌘K
  palette), a public landing page, and a login page. `frontend/`
- **Backend** — FastAPI wrapping the real Python tools as `/api/v1/*`. `backend/`
- **Dark-web search** — a meta-search over ~45 onion search engines, in
  `backend/app/services/darkweb/` (see `docs/DARKWEB.md`).
- **Tools** — vendored under `backend/tools/`:
  - `decint_darkweb_search.py`, `darknet_rerank.py`, `dwsearch.py` — the
    previous dark-web engines. **Legacy**: nothing in the app imports them any
    more; they are kept until deleted on purpose.

## Layout

```
backend/    FastAPI service (app/), vendored tools (tools/), .env
frontend/   Next.js app (app/, components/, lib/), public/ favicons
deploy/     decint-server-install.sh, Caddyfile, systemd/, production runbook
scripts/    make_favicon.py (purple shield icon set)
exe-maker/  DECINT EXE Maker — wrap any Python app in DECINT licensing, ship as EXE (own README)
android/    DECINT for Android — the site in a locked-down WebView, for Google Play (own README)
```

## Quick start — local development (WSL Ubuntu)

```bash
# one-time setup: venv + python deps + node + npm install
python3 tools/install.py --system    # apt packages + Tor (needs sudo)
python3 tools/install.py             # node, backend venv, frontend deps

# run both services
python3 tools/decint.py serve dev    # API :8000 + web :3000
```

Open http://localhost:3000. Tor should already be running
(`systemctl is-active tor`) for dark-web `tor` mode; `gateway` mode needs no Tor.

> The old `deploy/install-node.sh`, `install-backend.sh`, `install-frontend.sh`
> and `dev.sh` no longer exist — `tools/install.py` and `tools/decint.py`
> replaced them. This section previously still referenced the deleted scripts.

## Quick start — production server

Do **not** use `tools/install.py` on a server: it installs Node through nvm,
which a systemd service cannot see, and it never builds the frontend. Use:

```bash
sudo bash deploy/decint-server-install.sh
```

That handles Tor, system-wide Node, the venv, a hardened `.env`, both systemd
units, Caddy with automatic HTTPS, the firewall, and your first admin account.
See `START-HERE.md`.

### Config

Backend reads `backend/.env` (see `.env.example`). Key toggles:

| var | meaning |
|---|---|
| `OPERATOR_TOKEN` | empty = auth **off** (solo/local). Set it on a shared box. |
| `LEAKS_PROVIDERS` | free breach sources to aggregate. |
| `PASSWORDS_*` | Password Checker: API URL, Pwned Passwords fallback, timeout, batch size, concurrency. Works with the defaults. |
| `IPLOOKUP_*` | IP lookup: RDAP URL, timeout, addresses per hostname, RDAP cache, Tor exit list, DB-IP auto-update, and the AbuseIPDB key for reputation (empty = off). Works with the defaults. |
| `DOMAIN_*` | Domain lookup: DNS-over-HTTPS resolvers, RDAP URL, timeouts, subdomain cap, answer cache, and switches for the website fetch and the Wayback check. Works with the defaults. |
| `PHONE_*` | Phone lookup: the VeriRoute Intel API key (paid per lookup; empty = off), which add-ons to buy, answer cache, and the per-account monthly and site-wide daily caps. |
| `STRIPE_*` / `NOWPAYMENTS_*` | card and crypto billing. Both rails stay off until set — see `docs/BILLING-SETUP.md`. |
| `PLAY_*` | Google Play subscriptions in the Android app. Off until the service account is set — see `docs/BILLING-SETUP.md`. |

## The tools

- **Leak search** (`/api/v1/leaks/search`) — aggregates free public breach
  sources (XposedOrNot, ProxyNova COMB, LeakCheck, HIBP catalog). Deduped,
  source-tagged. Secrets are revealed (`reveal=true`) on every paid plan and for
  staff; the free trial gets them masked and `reveal=true` is a 403. `kind=name`
  (auto-detected for two or more words) searches first/last names, but only in
  uploaded datasets that have name columns — names are never sent to the public
  sources. Coverage is free-tier and not exhaustive by design.
- **Spider** (`/api/v1/spider/*`, async jobs) — the correlation companion to
  leak search. One seed identifier (email/username/domain/name) fans out into a
  graph: the leak sources, plus free keyless pivots (Gravatar, a username across
  a curated site list, domain DNS/crt.sh/RDAP, dark-web co-mentions), expanded
  breadth-first under node/lookup/depth caps. The one tool that **persists** its
  queries — per account — so a later scan flags an identifier you already turned
  up ("seen before"). Paid-plans only; a whole scan costs one search. History is
  owner-scoped and deletable. Details: `docs/SPIDER.md`.
- **Dark-web search** (`/api/v1/darkweb/*`, async jobs) — one query fans out to
  many onion search engines; hits are merged, de-duplicated, scored and pruned
  (scam listings, ads, dead and look-alike addresses), and every dropped hit
  keeps its reason. `gateway` mode asks the engines that have a clearnet
  gateway (seconds, no Tor); `tor` mode asks ~45 onion engines over isolated
  Tor circuits and adds entity extraction + an evidence SHA-256. Understands
  `"quoted phrases"` and `-excluded` words. Details: `docs/DARKWEB.md`.
- **Password Checker** (`POST /api/v1/passwords/check`) — has a password turned
  up in breach data, and how many times. The console hashes it with SHA-1 in the
  browser and sends only the hash; the endpoint refuses anything that is not a
  40-hex digest. Looked up through the [leakedpassword.com](https://leakedpassword.com/documentation/about/)
  API (Have I Been Pwned's Pwned Passwords) as `?p=&s=<sha1>` — its guard
  refuses a hash-only `?s=` query unless an empty `p` is present. If it fails,
  the Pwned Passwords range API answers instead and sees only a 5-character
  prefix (`PASSWORDS_HIBP_FALLBACK`). Up to 20 hashes a check, one search per
  check, refunded when nothing could be answered. Nothing is logged or cached;
  hashes travel in the POST body so they stay out of access logs. In the recon
  shell, `passwords [sha1]` takes a hash only — the shell keeps what is typed on
  screen, so a password argument is refused and masked.
- **IP lookup** (`POST /api/v1/ip/lookup`, body `{"target": ...}`) — an IPv4/IPv6
  address, hostname or URL (a hostname is resolved and its first
  `IPLOOKUP_MAX_ADDRESSES` addresses looked up). Returns approximate location
  and the network operator/ASN from local `.mmdb` files in `GEOIP_DIR` — DB-IP
  Lite (CC BY 4.0, attribution shown in the console), or GeoLite2 when those
  files are present — so that part never leaves the server; the registry record
  (holder, range, abuse contact, dates) over RDAP via rdap.org; reverse DNS
  checked against forward DNS; and Tor exit status from the Tor Project's bulk
  list (cached hourly). With `IPLOOKUP_ABUSEIPDB_KEY` set, AbuseIPDB's community
  abuse reports add a 0-100 abuse score, report count and usage type (free
  plan: 1,000 checks a day; answers cached an hour, and a spent quota stands
  the lookup down until AbuseIPDB's reset). Private/reserved addresses are
  classified and sent nowhere. One search per lookup, refunded when nothing was learned; bad or
  unresolvable input is refused before charging. The DB-IP files are fetched by
  the API when missing and re-checked daily for the monthly edition
  (`IPLOOKUP_AUTO_UPDATE`), or by hand with `python -m app.cli ipdb-update`.
  Shell: `ip <target>`.
- **Domain lookup** (`POST /api/v1/domain/lookup`, body `{"target": ...}`) — a
  domain, a URL (its host) or an email address (its domain). DNS records over
  DNS-over-HTTPS (Google, then Cloudflare when Google fails); mail protection
  graded from SPF, DMARC (falling back to the registered domain's, as
  receivers do) and MTA-STS; the registration over RDAP via rdap.org, asked of
  the registered domain found from the zone apex, through CNAMEs; subdomains
  from certificate-transparency logs (crt.sh, then Cert Spotter); the website's
  status, redirect chain, server, title, security headers and TLS certificate;
  hosting from the IP lookup's local databases; and the first Wayback Machine
  capture. The site fetch only ever connects to public addresses and pins each
  connection, redirects included, to the address it checked, so it can't be
  pointed at this server's own network. crt.sh and the Wayback Machine are
  rested for 10 minutes after three failures in a row. One search per lookup,
  refunded when nothing was learned; an IP address is refused uncharged and
  pointed at the IP tool. Shell: `domain <target>`.
- **Phone lookup** (`POST /api/v1/phone/lookup`, body `{"number": ...}`) — a US or
  Canadian (+1) number in any common format. One VeriRoute Intel LRN call with
  its add-ons returns the routing number and when the number last ported, the
  serving carrier and line type with the rate center/city/state/ZIP it is homed
  in, the caller ID name (CNAM), the messaging provider, and a 0-100 spam
  reputation. The console lays these out as VeriRoute's own objects (`lrn`,
  `enhanced_lrn`, `messaging`, `cnam`, `trust`) with the raw answer beside them.
  Paid per lookup, so: numbers that cannot exist are refused before anything is
  sent, answers are cached `PHONE_CACHE_TTL`, metered accounts get
  `PHONE_MONTHLY_LIMIT` a month (counted in `phone_counters`), and the site stops
  at `PHONE_DAILY_LIMIT` paid lookups a day. One search per lookup, refunded when
  VeriRoute fails. Shell: `phone <number>`.
- **Source health** (admins: `GET /api/v1/admin/health`, `POST
  /api/v1/admin/health/probe`) — every provider degrades quietly so one dead
  source can't sink a search, which also means an outage looks like an empty
  answer. Each tool scores every call it makes to an outside source (ok or
  failed, latency, last error, scrubbed of anything query-shaped), in memory
  since the last restart. The report adds local checks (Tor, the IP databases,
  the AbuseIPDB and VeriRoute keys and their quotas, the database, disk space,
  background jobs) and the dark-web engines' own health. The probe sends one
  canary query (`test@example.com`, `example.com`, `1.1.1.1`, the SHA-1 of
  `password`) to every free source plus VeriRoute's non-billable key check, at
  most once a minute. The public `/api/v1/health` is unchanged and says
  nothing about sources. Shell (admins only): `health`, or `health live` to
  probe first; `/health` works too.
- **Billing** (`/api/v1/billing/*`) — Stripe Checkout for cards and
  NOWPayments for BTC + ~300 other assets on the website, and Google Play
  subscriptions inside the Android app, all behind one entitlement model that
  drives `users.tier`. Cards and Play recur; crypto is a prepaid period, because
  no chain lets a merchant pull a renewal. See `docs/BILLING-SETUP.md`.

## Deploy (Netherlands server)

See `deploy/README.md` for the full runbook (Caddy TLS reverse proxy + systemd).
Short version:

1. `git clone`/copy to `/opt/decint-tools`; install Node, Python venv, Tor, Caddy.
2. `backend/.env`: set `OPERATOR_TOKEN` and a strong `SESSION_SECRET`.
3. `cd frontend && npm ci && npm run build`.
4. Install the two systemd units + the Caddyfile (edit the domain), enable them.
5. Firewall: only 80/443 public; 3000/8000 stay on localhost.

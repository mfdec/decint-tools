# Billing setup

Three payment rails, one entitlement model. The website sells through Stripe
and NOWPayments; the Android app sells through Google Play, because Play's
policy allows no other way to pay inside an app.

| | Stripe | NOWPayments | Google Play |
|---|---|---|---|
| Where | Website | Website | Android app only |
| Pays with | Cards, Apple/Google Pay, local methods | BTC + ~300 assets | Whatever the person's Play account has |
| Recurs | **Yes** — Stripe pulls the renewal | **No** — prepaid period | **Yes** — Google pulls the renewal |
| Fees | ~2.9% + 30¢ | ~0.5–1% | 15% of subscriptions |
| Prices | `services/billing/plans.py` (USD) | same | Set per country in Play Console |

All three rails are **off** until their keys are set. Neither is offered until
`PUBLIC_BASE_URL` is set too — checkout return URLs and webhook URLs are built
from it, and a checkout with no absolute return URL strands the customer on the
processor's page.

---

## Why crypto is not a subscription

Nothing on-chain lets a merchant debit a wallet on a schedule. There is no
mandate, no card on file, no pull. Anything that calls itself a "crypto
subscription" is a reminder email with extra steps.

So the crypto rail sells **a prepaid block of access** — one month or one year,
paid up front — and pushes the account's expiry forward. The UI says this at
the point of payment, the billing page tags the plan "Prepaid — does not
renew", and the account gets an email `BILLING_EXPIRY_NOTICE_DAYS` before it
runs out. Without that last part, the first thing a crypto customer learns is
that their access stopped.

Cards do recur, because Stripe holds the mandate. That asymmetry is the single
most important thing not to paper over in the UI.

---

## Stripe

1. **Keys** — dashboard.stripe.com ▸ Developers ▸ API keys. Copy the secret
   key into `STRIPE_SECRET_KEY`.
2. **Webhook** — Developers ▸ Webhooks ▸ Add endpoint.
   - URL: `https://YOURDOMAIN/api/v1/billing/webhook/stripe`
   - Events: `checkout.session.completed`, `checkout.session.expired`,
     `customer.subscription.created`, `customer.subscription.updated`,
     `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`
   - Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
   - `GET /api/v1/billing/admin/status` compares the registered endpoint
     against the list the code handles (`stripe.webhook.missing_events`), so
     a forgotten event shows up there rather than as a renewal that never
     lands.
3. **Prices (strongly recommended)** — create a Product and a monthly +
   yearly Price for Starter and Pro matching the amounts in
   `backend/app/services/billing/plans.py`, and paste the `price_…` ids into
   `STRIPE_PRICE_*`. Without them checkout still charges correctly (prices are
   built inline), but nothing can *switch* a plan: neither Stripe's portal nor
   the in-place change on the pricing page, both of which move a subscription
   between Prices that exist in your catalogue.
4. **Tax (optional)** — enable Stripe Tax and set your origin address, then
   `STRIPE_TAX_ENABLED=true`. Stripe then collects and remits VAT/sales tax.
5. **Customer portal** — nothing to do. Stripe refuses to open its portal
   until a configuration exists, which normally means someone pressing Save
   on the portal settings page in the Dashboard once. The backend creates
   its own configuration on first use instead (tagged `decint-portal` in its
   metadata, listed under Settings ▸ Billing ▸ Customer portal) with invoice
   history, card update, cancel-at-period-end and — when `STRIPE_PRICE_*` are
   set — plan switching between those Prices. Change the Price ids and the
   configuration is updated on the next portal open.

### API version

Stripe moved several fields in the 2025-03-31 (Basil) release: an invoice's
subscription is now `parent.subscription_details.subscription` rather than
`invoice.subscription`, and a subscription's `current_period_end` lives on
its items. Accounts created since default to a version after that change.
The handlers read both shapes, so it does not matter which version the
account or the webhook endpoint is pinned to — but if you ever fork the
webhook code, keep it that way: reading only the old field makes every
renewal resolve to no subscription and silently do nothing, and the first
symptom is a paying customer dropped to the free tier three days after their
card was charged.

### Changing plan with a card on file

A card subscriber who picks another plan or period on the pricing page is
**not** sent through Checkout again. Checkout in subscription mode always
opens a new subscription — a second one, billed alongside the first — so
`POST /billing/checkout` detects the live subscription and instead moves it
to the new Price in place:

- **Upgrade** — the difference for the rest of the current period is invoiced
  and charged to the card on file immediately (`proration_behavior:
  always_invoice`). If the card declines, the update is refused
  (`payment_behavior: error_if_incomplete`) and nothing changes: the API
  answers 402 with the decline reason. A plan that changed while the payment
  did not is the worst of both.
- **Downgrade** — the unused part of the current period is credited against
  the next renewal. Nothing is charged now.
- **Monthly ⇄ yearly** — the billing cycle restarts today
  (`billing_cycle_anchor: now`), so the new period is a clean one.
- **Picking the plan that is already cancelling** — resumes it. Nothing is
  charged.

The response carries `changed: true` and `url: null`; the entitlement is
updated before the response goes out, so the customer sees the new plan on
the next page load rather than after the webhook. The order row it writes is
attached to the proration invoice, and the `invoice.paid` webhook for that
invoice — which can arrive before the change call has even returned — settles
the same row rather than adding a second line of history.

The same endpoint refuses to sell a **crypto** period to a card subscriber
(400). A prepaid block under a subscription that keeps renewing over it is
not a purchase anyone meant to make; cancel the subscription in the portal
first.

> This deployment's `backend/.env` carries a **live-mode** catalogue —
> `DECINT Starter` and `DECINT Pro`, monthly + yearly, at the amounts in
> `plans.py` — behind a live *restricted* key with read/write on customers,
> checkout sessions, subscriptions, invoices, the customer portal and webhook
> endpoints. The earlier test-mode prices (`price_1U9Yk…`) remain in test
> mode only. The former `DECINT Starter` and `DECINT Professional` products
> are archived: Professional's prices were $129/$1,290, and reusing them for
> Pro at $45 would have charged nearly three times the advertised figure,
> which is exactly the failure mode this file warns about below. **Live mode
> needs its own products and prices; test-mode ids do not work there.**

Test the webhook locally without deploying:

```bash
stripe listen --forward-to localhost:8000/api/v1/billing/webhook/stripe
```

Card data never reaches this server — Checkout is hosted by Stripe, which is
what keeps the deployment in PCI SAQ-A. Do not replace it with your own card
form without understanding what that costs you.

## NOWPayments

1. **Payout wallet first.** nowpayments.io ▸ set the address settlements land
   in. Nothing works until this is set.
2. **API key** — Settings ▸ API keys ▸ `NOWPAYMENTS_API_KEY`.
3. **IPN secret** — Settings ▸ Payments ▸ IPN ▸ generate ▸
   `NOWPAYMENTS_IPN_SECRET`, and set the callback URL to
   `https://YOURDOMAIN/api/v1/billing/webhook/nowpayments`.
4. **Coins** — `NOWPAYMENTS_CURRENCIES` is the list offered at checkout,
   intersected with what your account actually supports. Leave it empty to let
   the customer pick from everything.

### Why not Coinbase Commerce

It is the name most people reach for, and it is not an option. Coinbase
Commerce dropped native Bitcoin and every other UTXO coin in February 2024, and
closed its merchant portal entirely on **31 March 2026**. A BTC-accepting
merchant cannot use it.

### If you would rather not use a custodian

NOWPayments is custodial and hosted — that is the trade for not running a node.
[BTCPay Server](https://btcpayserver.org) is the alternative: self-hosted,
zero fees, non-custodial, no third party holding your money, BTC + Lightning.
The cost is real ops work — a node, a server, key management. Adding it is one
new module beside `nowpayments_provider.py` implementing the same three
functions (`available`, `create_checkout`, `handle_webhook`); nothing else in
the system knows which processor paid.

---

## Google Play (the Android app)

The app sells the same Starter and Pro plans, through Google Play Billing.
The app grants nothing itself. It hands each purchase token to the server,
which looks it up with the Play Developer API. The plan is granted only if
Google confirms the purchase and it carries the buyer's account reference.
The server then acknowledges it; Google refunds anything left unacknowledged
for 3 days. Renewals, cancellations and refunds reach the server as
real-time developer notifications. Code: `services/billing/play_provider.py`.

1. **Subscriptions in Play Console** (Monetize ▸ Subscriptions). Create one
   subscription per plan, using the **plan key as the product id**, and give
   each one base plans with the **period key as the base plan id**:

   | Product id | Base plans (id ▸ billing period) |
   |---|---|
   | `starter` | `monthly` ▸ 1 month, `semiannual` ▸ 6 months, `yearly` ▸ 1 year |
   | `pro` | the same three |

   Both mappings are fixed conventions, so there is nothing to configure on
   the server. Set prices per country here; the website's USD prices don't
   apply in the app, which shows whatever Google returns. Leave out a base
   plan and the app simply doesn't offer that period. Activate each base plan.
2. **A service account that can read purchases.**
   1. In Google Cloud, pick or create a project, enable the **Google Play
      Android Developer API**, and create a service account with a JSON key.
   2. In Play Console, open **Users and permissions ▸ Invite new users**. Use
      the service account's email address, and grant *View financial data*
      plus *Manage orders and subscriptions* for this app.
   3. Copy the key to the server (e.g. `/opt/decint-tools/backend/play-key.json`),
      readable only by `decint` (`chmod 600`). Set `PLAY_SERVICE_ACCOUNT_FILE`
      to its path.

   Permissions can take a few hours to reach the API after the invite.
3. **Real-time developer notifications** (how renewals, cancellations and
   refunds arrive):
   1. Generate a secret:
      `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
      Set it as `PLAY_RTDN_TOKEN`.
   2. In Google Cloud Pub/Sub, create a topic such as `decint-play`. Grant
      `google-play-developer-notifications@system.gserviceaccount.com` the
      *Pub/Sub Publisher* role on it.
   3. Add a **push** subscription to the topic with the endpoint
      `https://YOURDOMAIN/api/v1/billing/webhook/play?token=PLAY_RTDN_TOKEN`.
   4. In Play Console, open **Monetize with Play ▸ Monetization setup**, enter
      the topic name (`projects/PROJECT/topics/decint-play`), and press
      *Send test notification*. The API log shows
      `play: test notification received`.
4. **Server.** `google-auth` is a new dependency, so reinstall and restart:

   ```bash
   cd /opt/decint-tools/backend
   sudo -u decint .venv/bin/pip install -r requirements.txt
   sudo systemctl restart decint-api
   ```

   `PLAY_PACKAGE_NAME` defaults to `tools.decint.app`; change it only if the
   app's `applicationId` changes.
5. **Test with license testers.** In Play Console, open **Setup ▸ License
   testing** and add the Google accounts that buy for free. Those accounts
   must install a release-signed build from a testing track. A debug build
   can't buy, because its package (`tools.decint.app.debug`) isn't on Play.

Someone who already pays on the website is told in the app that their plan is
billed outside it, and isn't offered a second one. A Play subscriber can't
check out on the website; the API refuses it. Deleting an account stops its
Play renewal, and if Google can't be reached, nothing is deleted.

---

## Verifying it

With an admin session:

```bash
curl -s https://YOURDOMAIN/api/v1/billing/admin/status | python3 -m json.tool
```

That reports, per rail, whether the keys are present, whether the gateway is
reachable, which Price ids are configured, and the exact webhook URL to
register. For Stripe it also checks the live account: whether an endpoint is
registered at that URL and which of the handled events it is missing
(`stripe.webhook`), and whether the customer portal can open and offers plan
switching (`stripe.portal`). It answers "why is there no crypto button" — and
"why did the renewal not land" — without reading `.env` over SSH.

Offline tests, no processor contacted:

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_billing.py
cd backend && .venv/bin/python -m pytest -q tests/test_play_billing.py   # Google Play, faked
```

---

## How access is granted

```
checkout  →  billing_orders row written BEFORE the customer leaves
             (this is where plan and price are decided)
             ↓
processor  →  webhook: "reference X settled"
             ↓
             order looked up by reference; plan and amount come from OUR row
             ↓
entitlements  →  tier + expires_at   (users.tier kept in step)
```

**A webhook never says what someone bought.** It says only that a reference
settled; what that reference was worth was decided on our side before the
customer ever reached the processor. That is why a tampered callback cannot buy
a plan even if it were signed.

Other invariants worth keeping:

- **Signature checks have no fallback.** With `STRIPE_WEBHOOK_SECRET` or
  `NOWPAYMENTS_IPN_SECRET` empty, callbacks are rejected, not trusted. An
  unverified billing webhook is an anonymous endpoint for granting yourself a
  paid plan.
- **Webhooks are idempotent.** Every processor retries. A replayed "payment
  finished" is deduped, not a second month — and a renewal invoice is one line
  of payment history however many times Stripe delivers it.
- **Renewals are recorded.** Every `invoice.paid` for an existing subscription
  writes a paid order row referencing the invoice, so the billing page shows
  each charge the card took, not just the first. The amount on that row is
  informational: access is set from the subscription, never from the invoice.
- **Abandoned checkouts close themselves.** A pending order older than
  `ORDER_TTL_HOURS` (48) is marked `expired` by the sweep, and
  `checkout.session.expired` closes Stripe ones as soon as the session dies.
  Without this, ten abandoned checkouts would trip the open-order cap and lock
  a customer out of buying anything. A payment that settles late is still
  honoured — both handlers grant on a settled reference whatever the row said.
- **Only `finished` grants.** `partially_paid` (an underpayment) is left for a
  human; granting a full period for a partial payment is the most expensive
  possible rounding error.
- **Renewing early stacks.** Paying a week before expiry adds to the end, not
  from today.
- **Lapses are swept on the auth path**, throttled to one pass a minute, so
  there is no cron to forget to install.

## Changing prices

Edit `backend/app/services/billing/plans.py`. That is the only copy that
decides what a card is charged; the pricing page fetches it from
`GET /api/v1/billing/config`. `frontend/lib/pricing.ts` is a fallback used only
when the API is unreachable, and renders without buy buttons.

If you use `STRIPE_PRICE_*` ids, create new Prices in Stripe to match —
existing subscriptions keep charging the old Price until they are migrated,
which is Stripe's behaviour, not a bug here.

## Changing a plan's monthly allowance

`quota` in `plans.py` (and the matching "N queries per month" feature line, and
`frontend/lib/pricing.ts`). Lowering it lowers it for everyone on that plan —
unless they are grandfathered. `usage.allowance()` gives an account the larger
of its `quota_grandfathers` row and the plan's number, for as long as it stays on
the same plan: changing plan or lapsing to free deletes the row, renewing keeps
it.

The 2026-10-09 cut (Starter 500→200, Pro 5,000→1,000) needs nothing run by hand:
`db._init_schema` creates the table on the first start of the new code and, in
that same start, writes a row at the old numbers for every account holding a live
paid plan (`_PRE_CUT_QUOTAS`). Later starts leave the table alone, so whoever buys
under the new numbers is never handed the old ones. Check it after the restart
with `SELECT * FROM quota_grandfathers;`.

For a future cut, the same mechanism needs a new table or a `generation` column —
the table is only ever filled once — plus the numbers being replaced.

## Comping an account

```bash
curl -X POST https://YOURDOMAIN/api/v1/billing/admin/grant \
  -H 'Content-Type: application/json' \
  -d '{"user_id": 7, "tier": "pro", "months": 12, "reason": "paid by invoice"}'
```

Omit `months` for access that never expires. Both forms are written to the
audit log with the acting admin.

## A note on merchant risk

OSINT and breach-lookup tooling sits close to categories payment processors
review carefully. Nothing here is on Stripe's prohibited list, but describe the
business accurately during onboarding rather than after — an account frozen
mid-month is far more expensive than a slower approval.

This is also why the provider abstraction is worth keeping: swapping Stripe for
a merchant-of-record like Paddle (which also handles global VAT) means adding
one module in `backend/app/services/billing/`, not a rewrite.

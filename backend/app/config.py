"""Runtime configuration, loaded from environment / .env.

Single source of truth for every tunable. Nothing else reads os.environ
directly — import `settings` from here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── HTTP server ──
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    # Comma-separated list of allowed CORS origins for the Next.js frontend.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── Operator auth (single-operator model) ──
    # If OPERATOR_TOKEN is empty, auth is DISABLED (everything is public) —
    # convenient for a solo WSL bring-up. Set it on any shared/public box.
    operator_token: str = ""
    # Signs the session cookie. Change it in production; a random default is
    # fine for local use but rotating it logs everyone out.
    session_secret: str = "dev-insecure-change-me"
    session_cookie: str = "decint_session"
    session_ttl_seconds: int = 60 * 60 * 12  # 12h
    # Set true once the site is behind TLS — the deploy runbook flips this.
    cookie_secure: bool = False

    # ── Tor / dark-web ──
    tor_host: str = "127.0.0.1"
    tor_port: int = 9050
    # Where the search engine keeps per-engine health (the circuit breaker) and
    # Ahmia's abuse banlist cache. Relative paths resolve from the backend dir.
    darkweb_data_dir: str = "data/darkweb"
    # Time budget for one search. Tor mode fans out to ~45 onion engines through
    # circuits that take tens of seconds; gateway mode makes a handful of
    # clearnet requests and should never need that long.
    darkweb_deadline: float = 75.0
    darkweb_gateway_deadline: float = 30.0
    # Searches that run at once. Every Tor search opens up to ~45 circuits, so a
    # burst of customers would otherwise starve each other (and the Tor daemon).
    # Extra searches wait their turn and show as "queued".
    darkweb_max_concurrent_searches: int = 3
    # Most result pages a customer may ask each engine for.
    darkweb_max_pages: int = 3
    # Let customers opt in to the experimental engines (unvetted, often dead).
    darkweb_allow_experimental: bool = True
    # Comma-separated engine names. Allow-list wins over the tier defaults;
    # deny-list always applies. Both empty = the catalog's default tiers.
    darkweb_engines: str = ""
    darkweb_disabled_engines: str = ""
    # Result shaping: hide results scoring below this quality (0..1) and show at
    # most this many results per onion site (the rest collapse under the best).
    darkweb_min_quality: float = 0.35
    darkweb_max_per_host: int = 2
    # Bench an engine after this many failed searches in a row, for this long.
    darkweb_breaker_failures: int = 3
    darkweb_breaker_cooldown: float = 1800.0
    # Point at a directory of saved engine pages to run searches with no network
    # (demos, CI, `tools/verify.py` on a box without Tor). Leave empty in production.
    darkweb_replay_dir: str = ""
    # Alternative engine catalog TOML, to add or fix engines without a code change.
    darkweb_catalog_path: str = ""

    # ── Packet sniffer (admin-only, local box) ──
    # OFF by default: the sniffer captures the HOST's traffic and needs root,
    # so it is only meaningful on the operator's own machine.
    sniffer_enabled: bool = False
    sniffer_iface: str = ""  # empty = auto-detect

    # ── Social login (OAuth) ──
    # The public origin the browser reaches this site on, e.g.
    # "https://decint.tools". REQUIRED for OAuth: the redirect URI registered
    # with GitHub/Google is derived from it, and must match exactly. Leave the
    # provider secrets empty to keep that button hidden.
    public_base_url: str = ""
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    # Signs the short-lived OAuth `state` (CSRF binding). Reuses the session
    # secret by default; no separate value needed.
    oauth_state_ttl_seconds: int = 600

    # ── Accounts / login ──
    # Bootstrap admin, created on first startup only if the users table is empty.
    # Clear these from .env once the account exists.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    otp_ttl_seconds: int = 600
    # Offer login-token sign-in on the login page. Tokens are issued per account
    # (see services/tokens.py); turn this off to hide the option entirely.
    login_token_enabled: bool = True
    # Signed, short-lived token issued between password and second factor.
    mfa_challenge_ttl_seconds: int = 300

    # ── Signup + captcha ──
    signup_enabled: bool = True
    # New accounts land here. "pending" if you want to approve manually.
    signup_default_status: str = "active"
    # With SIGNUP_DEFAULT_STATUS=pending, email every new account a link that
    # activates it — proof of the mailbox stands in for the operator's
    # approval, so someone can sign up, activate and buy a plan with nobody in
    # the loop. Needs a mail relay; without one accounts stay pending for
    # manual approval exactly as before. Ignored when the default status is
    # already "active" (there is nothing to activate).
    signup_email_activation: bool = False
    signup_activation_ttl_hours: int = 48
    # New accounts land on the free tier and buy up from there. Setting this
    # to a paid tier gives every signup that plan for nothing.
    signup_default_tier: str = "free"
    signup_max_per_ip_per_hour: int = 5

    # Captcha vendor: "hcaptcha" or "recaptcha" (Google reCAPTCHA v2 checkbox).
    # Blank auto-selects whichever vendor has BOTH of its keys set, hCaptcha
    # first so existing hCaptcha deployments keep working unchanged. Set it
    # explicitly when both are configured and you want the other one.
    captcha_provider: str = ""

    # hCaptcha — the provider Discord uses. Both keys must be set for captcha
    # to be enforced at all; with them unset the feature is simply off.
    hcaptcha_site_key: str = ""
    hcaptcha_secret: str = ""
    # Google reCAPTCHA v2 ("I'm not a robot" checkbox). Same both-or-nothing rule.
    recaptcha_site_key: str = ""
    recaptcha_secret: str = ""
    # Failed auth attempts from one IP before login demands a captcha.
    captcha_login_threshold: int = 3
    captcha_window_seconds: int = 900
    captcha_always_on_login: bool = False

    # ── Email (second factor + notifications) ──
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    # Display name on the From header: "DECINT <noreply@decint.tools>".
    smtp_from_name: str = "DECINT"
    # Where new-signup and security alerts go. Comma-separated. Blank means
    # every admin account's own address.
    admin_alert_email: str = ""
    # Master switch for the ADMIN alerts (new signup, and anything else
    # operational). Sign-in codes, approval notices and password-reset links
    # are part of authentication, not news, and are always sent.
    email_notifications: bool = True
    # How long a password-reset link stays valid, and how many may be requested
    # from one address per hour.
    password_reset_ttl_minutes: int = 30
    password_reset_max_per_ip_per_hour: int = 5
    # How long the link emailed to a *new* address stays valid when someone
    # changes their account email from the profile page.
    email_change_ttl_minutes: int = 60

    # ── SMS second factor (Twilio) ──
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""

    # ── Visitor analytics ──
    analytics_enabled: bool = True
    analytics_db: str = "data/analytics.db"
    # Where GeoLite2-City.mmdb / GeoLite2-ASN.mmdb live. Free from MaxMind;
    # without them, geo/ASN columns stay null and everything else still works.
    geoip_dir: str = "data/geoip"
    # How much of a visitor's IP to keep:
    #   full        — exact address. Most useful, and personal data under GDPR.
    #   anonymized  — last octet zeroed. Keeps city/ASN accuracy, much lower risk.
    #   none        — store only the salted hash.
    analytics_ip_mode: str = "full"
    # rotating  — visitor id re-salts daily; unique counts are right, but a
    #             visitor cannot be followed from one day to the next.
    # persistent— stable salt, so a returning visitor keeps the same id. This is
    #             cross-session tracking; if you serve EU visitors it needs
    #             consent, not just a privacy-policy line.
    analytics_id_mode: str = "rotating"
    # Store crawler hits too. On by default: they're flagged is_bot=1 and hidden
    # from every default query, but keeping them means you can actually see how
    # much of your traffic is bots. Set false to drop them at the door instead.
    analytics_track_bots: bool = True
    # Delete rows older than this on startup. 0 disables pruning.
    analytics_retention_days: int = 90

    # ── Billing ──
    # Master switch. Off = no /billing routes advertise anything, the pricing
    # page falls back to "email the operator", and nothing calls a processor.
    billing_enabled: bool = True
    billing_currency: str = "usd"
    # Where a processor returns the customer. Both are paths on
    # public_base_url, which is REQUIRED once billing is on — a checkout with
    # no absolute return URL strands the customer on the processor's page.
    billing_success_path: str = "/billing/success"
    billing_cancel_path: str = "/billing/cancel"
    # How long paid access survives past its expiry before the sweep drops the
    # account to free. Covers a card retry cycle, and a crypto payment that
    # confirmed slowly.
    billing_grace_days: int = 3
    # How many days before a PREPAID period ends to email the customer. Card
    # subscriptions renew themselves and are never warned. 0 disables it.
    billing_expiry_notice_days: int = 7

    # ── Stripe (cards, wallets, and the local methods Checkout enables) ──
    # Card data never reaches this server: Checkout is hosted by Stripe, which
    # keeps the deployment in PCI SAQ-A.
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    # From the endpoint you register at dashboard.stripe.com/webhooks. Without
    # it every webhook is rejected — an unverified billing webhook is an open
    # "give me a free subscription" endpoint.
    stripe_webhook_secret: str = ""
    # Optional Price ids. With them Checkout uses your real catalog and the
    # customer portal can switch plans; without them prices are built inline
    # from services/billing/plans.py, which works but cannot offer that switch.
    stripe_price_starter_monthly: str = ""
    stripe_price_starter_semiannual: str = ""
    stripe_price_starter_yearly: str = ""
    stripe_price_pro_monthly: str = ""
    stripe_price_pro_semiannual: str = ""
    stripe_price_pro_yearly: str = ""
    # Let Stripe collect and remit VAT/sales tax on Checkout. Requires Stripe
    # Tax to be enabled and your origin address set in the dashboard.
    stripe_tax_enabled: bool = False

    # ── NOWPayments (BTC + ~300 other assets) ──
    # Crypto is sold as a prepaid period, never a subscription: no chain lets a
    # merchant pull a renewal, so "recurring crypto" would be a lie in the UI.
    nowpayments_api_key: str = ""
    # Payment Settings ▸ IPN secret. Same reasoning as the Stripe secret: with
    # it empty, callbacks are rejected rather than trusted.
    nowpayments_ipn_secret: str = ""
    # Coins offered at checkout, comma-separated, first is the default. Empty
    # means "let the customer pick from everything the account supports".
    nowpayments_currencies: str = "btc,eth,usdttrc20,usdc,ltc,xmr"
    # Fix the payout asset so a BTC payment lands as, say, USDT. Empty keeps
    # each coin as itself.
    nowpayments_payout_currency: str = ""

    # ── Google Play (subscriptions bought inside the Android app) ──
    # Play requires its own billing for anything sold in the app. The product
    # ids in Play Console are the plan keys ("starter", "pro") and each one's
    # base plans are the period keys ("monthly", "semiannual", "yearly").
    play_package_name: str = "tools.decint.app"
    # JSON key of a Google Cloud service account that Play Console has granted
    # "View financial data" and "Manage orders and subscriptions". Every
    # purchase the app reports is checked against Google with it; without it
    # the app shows no subscribe buttons.
    play_service_account_file: str = ""
    # Shared secret in the Pub/Sub push URL for real-time developer
    # notifications (renewals, cancellations, refunds):
    #   https://YOURDOMAIN/api/v1/billing/webhook/play?token=THIS
    # With it empty every notification is rejected.
    play_rtdn_token: str = ""

    # ── Leak providers ──
    # Which free providers the aggregator fans out to, comma-separated.
    leaks_providers: str = "xposedornot,proxynova,leakcheck,hibp_catalog"
    leaks_timeout: float = 15.0
    leaks_cache_ttl: int = 300  # seconds
    # Datasets the admin uploads (Admin ▸ Data ▸ Leak datasets) are searched
    # alongside those providers. They live in their own SQLite file, not the
    # app database: a bulk import must never hold the lock the whole site
    # shares, and the records are easier to back up, wipe or move on their own.
    leaks_db: str = "data/leak_datasets.db"
    leaks_upload_max_mb: int = 300

    # ── Password checker ──
    # leakedpassword.com answers "has this SHA-1 appeared in a breach, and how
    # often" from Have I Been Pwned's Pwned Passwords. The console hashes in
    # the browser, so only the SHA-1 ever reaches this server or that API.
    passwords_api_url: str = "https://leakedpassword.com/api/"
    # When leakedpassword.com fails, ask Pwned Passwords directly. It is the
    # same data, and its range API only ever sees the first 5 hex characters
    # of the hash (k-anonymity). Off = a failed lookup is reported as failed.
    passwords_hibp_fallback: bool = True
    passwords_timeout: float = 10.0
    # Hashes one request may carry. A batch costs one search.
    passwords_batch_max: int = 20
    # Lookups in flight at once. The API's terms forbid aggressive querying.
    passwords_concurrency: int = 4

    # ── IP lookup ──
    # Location and ASN come from local .mmdb files in GEOIP_DIR: DB-IP's free
    # Lite databases (CC BY 4.0), or MaxMind GeoLite2 when those are installed.
    # Registration (owner, range, abuse contact) comes from RDAP, which
    # rdap.org redirects to the regional registry that holds the address.
    iplookup_rdap_url: str = "https://rdap.org/ip/"
    iplookup_timeout: float = 8.0
    # A hostname can resolve to many addresses; this many are looked up.
    iplookup_max_addresses: int = 4
    # Seconds an RDAP answer is reused. Registries throttle repeat queries.
    iplookup_cache_ttl: int = 3600
    iplookup_tor_list_url: str = "https://check.torproject.org/torbulkexitlist"
    # Fetch DB-IP Lite when it is missing, and the new edition each month.
    # Off = install the files yourself (`python -m app.cli ipdb-update`).
    iplookup_auto_update: bool = True

    # ── Phone lookup ──
    # VeriRoute Intel answers for US and Canadian (+1) numbers: the routing
    # number and when it last ported, the serving carrier and its location
    # data, the caller ID name (CNAM), the messaging provider, and a spam
    # reputation. Paid per lookup from a prepaid wallet, so the key lives in
    # .env only; with no key the lookup answers 503 and nothing is sent.
    phone_vri_api_key: str = ""
    phone_vri_url: str = "https://verirouteintel.com/api/v1/lrn"
    phone_timeout: float = 15.0
    # Add-ons billed on top of the routing lookup (carrier/location is free):
    # CNAM ~$0.006, trust ~$0.007, messaging ~$0.0009 at the time of writing.
    phone_include_cnam: bool = True
    phone_include_trust: bool = True
    phone_include_messaging: bool = True
    # Seconds an answer is reused, so the same number looked up twice in a day
    # is paid for once. Held in memory only.
    phone_cache_ttl: int = 86400
    # Spend guards. A metered account gets this many phone lookups a month on
    # top of its search allowance (admins and operators are exempt), and the
    # whole site stops calling VeriRoute after this many paid lookups in a UTC
    # day. 0 turns a guard off.
    phone_monthly_limit: int = 100
    phone_daily_limit: int = 500

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def leaks_provider_list(self) -> list[str]:
        return [p.strip() for p in self.leaks_providers.split(",") if p.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.operator_token)

    # ── billing capability flags ──
    # Each rail is "available" only when it is completely configured. A
    # half-configured processor must not be offered: the customer would reach a
    # checkout that cannot be settled, or worse, one whose callback we cannot
    # verify.
    @property
    def stripe_enabled(self) -> bool:
        return bool(
            self.billing_enabled
            and self.stripe_secret_key
            and self.stripe_webhook_secret
            and self.public_base_url
        )

    @property
    def crypto_enabled(self) -> bool:
        return bool(
            self.billing_enabled
            and self.nowpayments_api_key
            and self.nowpayments_ipn_secret
            and self.public_base_url
        )

    @property
    def play_enabled(self) -> bool:
        return bool(self.billing_enabled and self.play_service_account_file)

    @property
    def billing_providers(self) -> list[str]:
        out = []
        if self.stripe_enabled:
            out.append("stripe")
        if self.crypto_enabled:
            out.append("nowpayments")
        return out

    @property
    def nowpayments_currency_list(self) -> list[str]:
        return [
            c.strip().lower()
            for c in self.nowpayments_currencies.split(",")
            if c.strip()
        ]

    def stripe_price_for(self, plan: str, period: str) -> str:
        """Configured Price id for a plan/period, or "" to price inline."""
        return getattr(self, f"stripe_price_{plan}_{period}", "") or ""

    @property
    def oauth_providers(self) -> list[str]:
        """Which social-login providers are fully configured and therefore
        offered on the login page. A provider needs its client id + secret AND
        a public_base_url (to build a correct redirect URI)."""
        if not self.public_base_url:
            return []
        out = []
        if self.github_oauth_client_id and self.github_oauth_client_secret:
            out.append("github")
        if self.google_oauth_client_id and self.google_oauth_client_secret:
            out.append("google")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

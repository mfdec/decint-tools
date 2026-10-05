"""DECINT backend entrypoint.

    uvicorn app.main:app --host 127.0.0.1 --port 8000

Run from the `backend/` directory so the vendored `tools/` package is importable.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import settings
from .routers import (
    admin, admin_data, admin_leaks, analytics, auth, billing, darkweb, health,
    iplookup, leaks, oauth, passwords, phonelookup, spider, support,
)

app = FastAPI(
    title="DECINT API",
    version=__version__,
    description="OSINT + network intelligence tools: leak search, a correlation "
    "spider, dark-web search, a breached-password checker, IP lookup and phone lookup.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api/v1"
_datahub_task: asyncio.Task | None = None   # held so the hourly rollup isn't garbage-collected
_ipdb_task: asyncio.Task | None = None      # likewise the daily DB-IP update check
app.include_router(health.router, prefix=API)
app.include_router(auth.router, prefix=API)
app.include_router(oauth.router, prefix=API)   # inert until a provider's keys are set
app.include_router(leaks.router, prefix=API)
app.include_router(spider.router, prefix=API)
app.include_router(darkweb.router, prefix=API)
app.include_router(passwords.router, prefix=API)
app.include_router(iplookup.router, prefix=API)
app.include_router(phonelookup.router, prefix=API)
app.include_router(analytics.router, prefix=API)
app.include_router(admin.router, prefix=API)
app.include_router(admin_data.router, prefix=API)
app.include_router(admin_leaks.router, prefix=API)
app.include_router(billing.router, prefix=API)
app.include_router(support.router, prefix=API)


@app.on_event("shutdown")
async def _shutdown() -> None:
    from .services import darkweb as darkweb_svc

    await darkweb_svc.aclose()  # drain the Tor/clearnet connection pools


@app.on_event("startup")
async def _startup() -> None:
    import logging

    from . import db
    from .services import mail as mail_svc
    from .services import reset as reset_svc
    from .services import users as users_svc

    db.get_conn()  # create the schema up front, not on first request
    users_svc.purge_expired_sessions()
    reset_svc.purge_expired()

    # Entitlements that lapsed while the service was down. The same sweep runs
    # throttled on the auth path, so this only matters after a restart.
    from .services.billing import store as billing_store

    lapsed = billing_store.sweep()
    if lapsed:
        print(f"[decint] billing: {lapsed} entitlement(s) lapsed while stopped")

    # Uvicorn configures only its own loggers, so without this the mail
    # module's INFO lines vanish and a successful send looks identical to no
    # send at all. Failures would still reach stderr; that is not enough when
    # the question is "did my sign-in code actually go out".
    dec_log = logging.getLogger("decint")
    dec_log.setLevel(logging.INFO)
    if not dec_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
        dec_log.addHandler(handler)

    print(
        f"[decint] email: sending as {settings.smtp_from} via {settings.smtp_host}"
        if mail_svc.available()
        else "[decint] email: NOT configured — no 2FA codes, no password resets"
    )

    # Open registration with no captcha is a bot magnet: the only thing left in
    # the way is the per-IP hourly cap, which a bot with a proxy pool walks
    # straight through. Worth shouting about on every boot until keys are set.
    from .services import captcha as captcha_svc

    if (
        settings.signup_enabled
        and settings.signup_default_status == "active"
        and not captcha_svc.configured()
    ):
        print(
            "[decint] WARNING: signups are open and auto-approved, but no captcha "
            "is configured — set RECAPTCHA_SITE_KEY and RECAPTCHA_SECRET (Google) "
            "or HCAPTCHA_SITE_KEY and HCAPTCHA_SECRET, and check CAPTCHA_PROVIDER"
        )
    elif captcha_svc.configured():
        print(f"[decint] captcha: {captcha_svc.provider()}")

    if settings.billing_enabled:
        rails = settings.billing_providers
        print(
            f"[decint] billing: {', '.join(rails)}"
            if rails
            else "[decint] billing: enabled but NO rail is fully configured — "
                 "check STRIPE_*/NOWPAYMENTS_* and PUBLIC_BASE_URL"
        )
    else:
        print("[decint] billing: disabled (BILLING_ENABLED=false)")

    # Bootstrap admin, only when there are no accounts at all.
    if (
        users_svc.count() == 0
        and settings.bootstrap_admin_email
        and settings.bootstrap_admin_password
    ):
        try:
            users_svc.create(
                settings.bootstrap_admin_email,
                settings.bootstrap_admin_password,
                role="admin",
                tier="enterprise",
            )
            users_svc.audit("user.bootstrap", target=settings.bootstrap_admin_email)
            print(f"[decint] bootstrap admin created: {settings.bootstrap_admin_email}")
        except ValueError as e:
            print(f"[decint] bootstrap admin NOT created: {e}")

    # Uploads or imports cut off by this restart can never finish; clean them up.
    from .services.leaks import local as leak_datasets

    leak_datasets.recover()

    # Roll the day's numbers up BEFORE pruning: raw visits older than the
    # retention window are deleted just below, and the long-run trend must
    # survive that. A failure here must never stop the service starting.
    from .services import datahub

    try:
        datahub.refresh(full=True)
    except Exception as e:
        print(f"[decint] data hub: rollup failed ({type(e).__name__}: {e})")
    global _datahub_task
    _datahub_task = asyncio.create_task(datahub.refresh_loop())

    if settings.analytics_enabled and settings.analytics_retention_days > 0:
        db.prune(settings.analytics_retention_days)

    # IP lookup's location/ASN databases: fetched when missing, refreshed monthly.
    if settings.iplookup_auto_update:
        from .services import iplookup as iplookup_svc

        global _ipdb_task
        _ipdb_task = asyncio.create_task(iplookup_svc.update_loop())


@app.get("/")
async def root() -> dict:
    return {"service": "decint-api", "version": __version__, "docs": "/docs"}

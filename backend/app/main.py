"""DECINT backend entrypoint.

    uvicorn app.main:app --host 127.0.0.1 --port 8000

Run from the `backend/` directory so the vendored `tools/` package is importable.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import settings
from .routers import (
    admin, analytics, auth, billing, darkweb, discord, executor, fleet, health,
    leaks, packets,
)
# Social login (services/routers/oauth.py) is intentionally NOT registered:
# the login page offers email + login tokens only. Re-add `oauth` to the import
# above and the include below to bring GitHub/Google back.

app = FastAPI(
    title="DECINT API",
    version=__version__,
    description="OSINT + network intelligence tools: leak search, dark-web search, "
    "Discord OSINT, and (admin/local) packet capture.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api/v1"
app.include_router(health.router, prefix=API)
app.include_router(auth.router, prefix=API)
app.include_router(leaks.router, prefix=API)
app.include_router(darkweb.router, prefix=API)
app.include_router(discord.router, prefix=API)
app.include_router(packets.router, prefix=API)
app.include_router(analytics.router, prefix=API)
app.include_router(admin.router, prefix=API)
app.include_router(fleet.router, prefix=API)
app.include_router(billing.router, prefix=API)
app.include_router(executor.router, prefix=API)


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
            "[decint] WARNING: signups are open and auto-approved, but hCaptcha "
            "is NOT configured — set HCAPTCHA_SITE_KEY and HCAPTCHA_SECRET"
        )

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

    if settings.analytics_enabled and settings.analytics_retention_days > 0:
        db.prune(settings.analytics_retention_days)


@app.get("/")
async def root() -> dict:
    return {"service": "decint-api", "version": __version__, "docs": "/docs"}

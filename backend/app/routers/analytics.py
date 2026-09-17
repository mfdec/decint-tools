"""Visitor analytics — public collector, admin-only reads.

The collect endpoint is intentionally unauthenticated (it has to accept hits
from anonymous visitors) but it is write-only: it never returns data, and it
returns 204 regardless of outcome so it can't be used to probe the store.
Everything that *reads* visitor data requires an operator session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import require_admin
from ..config import settings
from ..services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/collect", status_code=204)
async def collect(request: Request) -> Response:
    """Record one event. Public, write-only, never errors out loud."""
    if not settings.analytics_enabled:
        return Response(status_code=204)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        analytics.record(request, payload)
    except Exception:
        # A logging failure must never break page delivery.
        pass
    return Response(status_code=204)


@router.get("/summary", dependencies=[Depends(require_admin)])
async def summary(
    days: int = Query(7, ge=1, le=365),
    include_bots: bool = False,
) -> dict:
    return analytics.summary(days=days, include_bots=include_bots)


@router.get("/recent", dependencies=[Depends(require_admin)])
async def recent(
    limit: int = Query(200, ge=1, le=2000),
    include_bots: bool = False,
) -> dict:
    return {"rows": analytics.recent(limit=limit, include_bots=include_bots)}


@router.get("/visitor/{visitor_id}", dependencies=[Depends(require_admin)])
async def visitor(visitor_id: str) -> dict:
    return analytics.visitor_detail(visitor_id)


@router.get("/config", dependencies=[Depends(require_admin)])
async def config() -> dict:
    """What the collector is actually doing — surfaced in the admin UI so the
    retention/identity posture is visible rather than buried in .env."""
    return {
        "enabled": settings.analytics_enabled,
        "ip_mode": settings.analytics_ip_mode,
        "id_mode": settings.analytics_id_mode,
        "track_bots": settings.analytics_track_bots,
        "retention_days": settings.analytics_retention_days,
    }

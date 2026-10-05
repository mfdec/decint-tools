"""Health + capability discovery. Public (no auth) so the frontend can render
the right apps before login."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .. import __version__
from ..config import settings
from ..models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    tor_ok, tor_detail = False, "not checked"
    try:
        from ..services.darkweb import tor_status

        tor_ok, tor_detail = await asyncio.to_thread(tor_status)
    except Exception as e:  # pragma: no cover
        tor_detail = f"check failed: {type(e).__name__}"

    return HealthResponse(
        version=__version__,
        tor=tor_ok,
        tor_detail=tor_detail,
        sniffer_enabled=settings.sniffer_enabled,
        leak_providers=settings.leaks_provider_list,
        auth_enabled=settings.auth_enabled,
    )

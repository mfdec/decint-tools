"""Admin health: the console's `health` command reads these.

GET  /admin/health        what is already known: local checks, every outside
                          source's tally since the last restart, dark-web
                          engine health. Calls nothing outside.
POST /admin/health/probe  one canary query to each free source (and
                          VeriRoute's free key check), then the same report.
                          At most once a minute; sooner returns the last run.

Admins only. The public /health stays as it was: it says whether Tor is up,
and nothing about which sources are failing or why.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..auth import require_admin
from ..services import healthcheck

router = APIRouter(prefix="/admin/health", tags=["admin-health"], dependencies=[Depends(require_admin)])


@router.get("")
async def health_report() -> dict[str, Any]:
    return healthcheck.report()


@router.post("/probe")
async def health_probe() -> dict[str, Any]:
    return await healthcheck.probe()

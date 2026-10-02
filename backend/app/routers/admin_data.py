"""Admin ▸ Data: the dataset catalogue, each dataset, and CSV export.

Read-only over data the app already holds — see services/datahub.py for what
each dataset contains and, as importantly, what it deliberately does not (what
people search for is never stored). Every route requires role=admin, and an
export is written to the audit log because it moves personal data off the page.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..auth import require_admin
from ..services import datahub, users
from ..services.analytics import client_ip
from ..services.datahub import Params

router = APIRouter(prefix="/admin/data", tags=["admin-data"], dependencies=[Depends(require_admin)])


@router.get("")
def catalogue() -> dict:
    datahub.ensure_fresh()
    return {"datasets": datahub.catalog()}


def _params(days: int, q: str, bots: bool, limit: int) -> Params:
    return Params(days=days, q=q.strip(), bots=bots, limit=limit)


@router.get("/{dataset}")
def dataset(
    dataset: str,
    days: int = Query(30, ge=1, le=365),
    q: str = Query("", max_length=100),
    bots: bool = False,
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    datahub.ensure_fresh()
    data = datahub.build(dataset, _params(days, q, bots, limit))
    if data is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    return data


@router.get("/{dataset}/export")
def export(
    dataset: str,
    request: Request,
    table: str | None = None,
    chart: int | None = None,
    days: int = Query(30, ge=1, le=365),
    q: str = Query("", max_length=100),
    bots: bool = False,
    limit: int = Query(1000, ge=1, le=1000),
    actor: dict = Depends(require_admin),
) -> Response:
    """One table (or chart's points) as CSV. Which one is named explicitly so a
    typo can't silently export something else."""
    if (table is None) == (chart is None):
        raise HTTPException(status_code=400, detail="Name exactly one of table= or chart=.")
    datahub.ensure_fresh()
    data = datahub.build(dataset, _params(days, q, bots, limit))
    if data is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    body = datahub.table_csv(data, table) if table is not None else datahub.chart_csv(data, chart or 0)
    if body is None:
        raise HTTPException(status_code=404, detail="No such table or chart in that dataset.")
    what = table if table is not None else f"chart{chart}"
    users.audit("data.exported", actor=actor, target=f"{dataset}/{what}",
                detail=f"{days}d", ip=client_ip(request))
    name = f"decint-{dataset}-{what}-{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )

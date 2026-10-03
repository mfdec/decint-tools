"""Leak database search (flagship). Free public sources plus any datasets an
admin uploaded; secrets masked by default, revealed on paid plans."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_session
from ..models import LeakKind, LeakSearchResponse
from ..services import usage
from ..services.leaks import search_leaks

router = APIRouter(prefix="/leaks", tags=["leaks"], dependencies=[Depends(require_session)])


@router.get("/search", response_model=LeakSearchResponse)
async def search(
    query: str = Query(..., min_length=2, description="email, username, domain, or a first/last name"),
    kind: LeakKind = "auto",
    reveal: bool = Query(False, description="unmask passwords/lines (paid plans)"),
    user: dict = Depends(require_session),
) -> LeakSearchResponse:
    # Refused before the allowance is touched: a request we won't answer is not
    # a search the customer should pay for.
    if reveal and not usage.reveals_secrets(user):
        raise HTTPException(
            status_code=403,
            detail="Revealing leaked passwords needs a paid plan. "
                   "Upgrade to unmask them; masked results are still available.",
            headers={"X-Upgrade-Path": "/pricing"},
        )
    usage.take(user)
    try:
        return await search_leaks(query, kind=kind, reveal=reveal)
    except Exception:
        # Charged before it ran; an aggregator fault is ours, not a search.
        usage.refund(user)
        raise

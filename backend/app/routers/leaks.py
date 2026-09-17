"""Leak database search (flagship). Free public sources; secrets masked by
default."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import require_session
from ..models import LeakKind, LeakSearchResponse
from ..services import usage
from ..services.leaks import search_leaks

router = APIRouter(prefix="/leaks", tags=["leaks"], dependencies=[Depends(require_session)])


@router.get("/search", response_model=LeakSearchResponse)
async def search(
    query: str = Query(..., min_length=2, description="email, username, or domain"),
    kind: LeakKind = "auto",
    reveal: bool = Query(False, description="unmask passwords/lines"),
    user: dict = Depends(require_session),
) -> LeakSearchResponse:
    usage.take(user)
    try:
        return await search_leaks(query, kind=kind, reveal=reveal)
    except Exception:
        # Charged before it ran; an aggregator fault is ours, not a search.
        usage.refund(user)
        raise

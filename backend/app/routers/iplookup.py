"""IP lookup: location, network operator, registry record, reverse DNS and Tor
exit status for an address, or for the addresses a hostname resolves to.

The target travels in a POST body so it stays out of the access logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..models import IpLookupRequest, IpLookupResponse
from ..services import iplookup as svc
from ..services import usage

router = APIRouter(prefix="/ip", tags=["ip"], dependencies=[Depends(require_session)])


@router.post("/lookup", response_model=IpLookupResponse)
async def lookup(
    body: IpLookupRequest, user: dict = Depends(require_session)
) -> IpLookupResponse:
    # Parsed and resolved before the allowance is touched: a typo or a name
    # that doesn't resolve is not a search.
    try:
        target = await svc.resolve_target(body.target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    usage.take(user)
    try:
        res = await svc.lookup(target)
    except Exception:
        usage.refund(user)
        raise
    if not svc.answered(res):
        # Every address private, or every source down: nothing was learned.
        usage.refund(user)
    return res

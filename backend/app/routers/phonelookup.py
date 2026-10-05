"""Phone lookup: carrier, line type, home location, caller ID name, messaging
provider and spam reputation for a US or Canadian number, from VeriRoute Intel.

The number travels in a POST body so it stays out of the access logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..models import PhoneLookupRequest, PhoneLookupResponse
from ..services import phonelookup as svc
from ..services import usage

router = APIRouter(prefix="/phone", tags=["phone"], dependencies=[Depends(require_session)])


@router.post("/lookup", response_model=PhoneLookupResponse)
async def lookup(
    body: PhoneLookupRequest, user: dict = Depends(require_session)
) -> PhoneLookupResponse:
    # Parsed before the allowance is touched: a typo, or a number that cannot
    # exist on the North American plan, is not a search and costs nothing.
    try:
        number = svc.parse_number(body.number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not svc.configured():
        raise HTTPException(status_code=503, detail="Phone lookup is not configured on this server.")

    usage.take(user)
    try:
        svc.take_monthly(user)
    except svc.CapReached as e:
        usage.refund(user)
        raise HTTPException(status_code=429, detail=str(e)) from e

    try:
        return await svc.lookup(number, body.number.strip())
    except svc.CapReached as e:
        usage.refund(user)
        svc.refund_monthly(user)
        raise HTTPException(status_code=429, detail=str(e)) from e
    except svc.LookupFailed as e:
        usage.refund(user)
        svc.refund_monthly(user)
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    except Exception:
        usage.refund(user)
        svc.refund_monthly(user)
        raise

"""Domain / website lookup: DNS, mail protection, registration, certificate
subdomains, the website and its TLS certificate, hosting and archive history
for a domain, a URL's host or an email address's domain.

The target travels in a POST body so it stays out of the access logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..models import DomainLookupRequest, DomainLookupResponse
from ..services import domainlookup as svc
from ..services import usage

router = APIRouter(prefix="/domain", tags=["domain"], dependencies=[Depends(require_session)])


@router.post("/lookup", response_model=DomainLookupResponse)
async def lookup(
    body: DomainLookupRequest, user: dict = Depends(require_session)
) -> DomainLookupResponse:
    # Parsed before the allowance is touched: a typo is not a search.
    try:
        host = svc.parse_target(body.target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    usage.take(user)
    try:
        res = await svc.lookup(host)
    except Exception:
        usage.refund(user)
        raise
    res.query = body.target.strip()
    if not svc.answered(res):
        # Nothing exists under the name, or every source failed.
        usage.refund(user)
    return res

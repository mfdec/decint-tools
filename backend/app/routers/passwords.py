"""Password checker: has a password appeared in a breach, and how often.

Takes SHA-1 digests only. The console hashes in the browser, so a password is
never sent to this server, and the digests travel in a POST body rather than a
query string so they stay out of the access logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..config import settings
from ..models import PasswordCheckRequest, PasswordCheckResponse
from ..services import passwords as svc
from ..services import usage

router = APIRouter(
    prefix="/passwords", tags=["passwords"], dependencies=[Depends(require_session)]
)


@router.post("/check", response_model=PasswordCheckResponse)
async def check(
    body: PasswordCheckRequest, user: dict = Depends(require_session)
) -> PasswordCheckResponse:
    if len(body.hashes) > settings.passwords_batch_max:
        raise HTTPException(
            status_code=400,
            detail=f"At most {settings.passwords_batch_max} hashes per check.",
        )
    hashes: list[str] = []
    for i, raw in enumerate(body.hashes, 1):
        h = svc.normalize(raw)
        # The value is never echoed: if a client sent a password here by
        # mistake, it must not come back in an error message.
        if h is None:
            raise HTTPException(
                status_code=400,
                detail=f"Entry {i} is not a SHA-1 hash (40 hex characters). "
                       "Hash passwords before sending them; this endpoint never "
                       "takes a password itself.",
            )
        hashes.append(h)
    hashes = list(dict.fromkeys(hashes))

    # Validated first, so a malformed request is refused without being charged.
    # One search per request, however many hashes it carries.
    usage.take(user)
    try:
        res = await svc.check(hashes)
    except Exception:
        usage.refund(user)
        raise
    if res.failed == res.total:
        # Neither source answered for anything: an outage, not a search.
        usage.refund(user)
    return res

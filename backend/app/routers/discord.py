"""Discord OSINT lookups."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..models import DiscordLookupResponse
from ..services import discord as svc
from ..services import usage

router = APIRouter(
    prefix="/discord", tags=["discord"], dependencies=[Depends(require_session)]
)


def _validate_id(value: str) -> None:
    if not value.isdigit() or not (17 <= len(value) <= 20):
        raise HTTPException(status_code=400, detail="Not a valid Discord snowflake ID.")


@router.get("/snowflake/{snowflake}", response_model=DiscordLookupResponse)
async def snowflake(snowflake: str) -> DiscordLookupResponse:
    _validate_id(snowflake)
    return await svc.lookup_snowflake(snowflake)


async def _metered(caller: dict, coro_factory):
    """Charge one search, run the live lookup, refund if it is our fault."""
    usage.take(caller)
    try:
        return await coro_factory()
    except Exception:
        usage.refund(caller)
        raise


@router.get("/user/{user_id}", response_model=DiscordLookupResponse)
async def user(user_id: str, caller: dict = Depends(require_session)) -> DiscordLookupResponse:
    _validate_id(user_id)
    return await _metered(caller, lambda: svc.lookup_user(user_id))


@router.get("/invite/{code}", response_model=DiscordLookupResponse)
async def invite(code: str, caller: dict = Depends(require_session)) -> DiscordLookupResponse:
    return await _metered(caller, lambda: svc.lookup_invite(code))


@router.get("/guild/{guild_id}/widget", response_model=DiscordLookupResponse)
async def guild_widget(guild_id: str, caller: dict = Depends(require_session)) -> DiscordLookupResponse:
    _validate_id(guild_id)
    return await _metered(caller, lambda: svc.lookup_guild_widget(guild_id))

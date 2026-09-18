"""Patch tab: a Claude-backed chat box for the admin panel.

Read services/patch_assistant.py first - this endpoint is conversational only
and does not execute anything against the server.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..services import patch_assistant

log = logging.getLogger("decint.patch")

router = APIRouter(prefix="/patch", tags=["patch"], dependencies=[Depends(require_admin)])


class PatchMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class PatchChatRequest(BaseModel):
    history: list[PatchMessage]


class PatchChatResponse(BaseModel):
    reply: str


@router.get("/status")
async def status() -> dict:
    return {"available": patch_assistant.available()}


@router.post("/chat")
async def chat(body: PatchChatRequest) -> PatchChatResponse:
    if not patch_assistant.available():
        raise HTTPException(status_code=503, detail="Patch assistant is not configured")
    if not body.history:
        raise HTTPException(status_code=400, detail="history must not be empty")

    try:
        text = await patch_assistant.reply([m.model_dump() for m in body.history])
    except httpx.HTTPStatusError as e:
        log.error("patch assistant upstream error: %s", e)
        raise HTTPException(status_code=502, detail="Patch assistant upstream error") from e
    except httpx.HTTPError as e:
        log.error("patch assistant request failed: %s", e)
        raise HTTPException(status_code=502, detail="Patch assistant request failed") from e

    return PatchChatResponse(reply=text)

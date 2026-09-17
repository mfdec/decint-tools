"""Packet capture — admin-only, local box. Hidden/disabled unless
SNIFFER_ENABLED. Streams decoded packets over a WebSocket."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from ..auth import is_authenticated, require_admin
from ..config import settings
from ..services.packets import CaptureError, CaptureSession, list_interfaces

router = APIRouter(prefix="/packets", tags=["packets"])


def _require_enabled() -> None:
    if not settings.sniffer_enabled:
        raise HTTPException(
            status_code=404,
            detail="Packet capture is disabled on this host (SNIFFER_ENABLED=false).",
        )


@router.get("/interfaces", dependencies=[Depends(require_admin)])
async def interfaces() -> dict:
    _require_enabled()
    try:
        return {"interfaces": list_interfaces(), "default": settings.sniffer_iface or None}
    except CaptureError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.websocket("/stream")
async def stream(websocket: WebSocket, iface: str | None = None, bpf: str = "") -> None:
    # WebSocket can't use HTTP dependencies for auth; check manually.
    if not settings.sniffer_enabled:
        await websocket.close(code=4404, reason="capture disabled")
        return
    if not is_authenticated(websocket):  # reads the same session cookie
        await websocket.close(code=4401, reason="unauthorized")
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    session = CaptureSession(iface or settings.sniffer_iface or None, bpf, loop)
    session.start()
    await websocket.send_json({"event": "started", "iface": session.iface or "default"})

    try:
        while True:
            item = await session.queue.get()
            await websocket.send_json({"event": "packet", **item})
    except WebSocketDisconnect:
        pass
    finally:
        session.stop()

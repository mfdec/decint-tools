"""Fleet hub proxy — admin-only.

The hub is a separate process on this box, bound to loopback and holding its
own SSH identity for every enrolled server. It is never exposed through nginx;
the console reaches it only through this router, which means fleet access is
gated by the same session cookie and `require_admin` check as everything else,
and the hub's token never reaches the browser.

Set FLEET_HUB_URL / FLEET_TOKEN in .env. Without a token the tab reports the
hub as unconfigured rather than failing per-request.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import require_admin
from ..config import settings

router = APIRouter(prefix="/fleet", tags=["fleet"])

# The hub answers locally; a slow reply means a slow ssh, not a slow network.
_TIMEOUT = httpx.Timeout(15.0, read=30.0)


def _require_configured() -> None:
    if not settings.fleet_token:
        raise HTTPException(
            status_code=503,
            detail="Fleet hub is not configured on this host (set FLEET_TOKEN in .env).",
        )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.fleet_token}"}


async def _call(method: str, path: str, *, json: Any = None) -> Any:
    """One request to the hub, with its failures translated for the console."""
    _require_configured()
    url = f"{settings.fleet_hub_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.request(method, url, json=json, headers=_headers())
    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Fleet hub is not running at {settings.fleet_hub_url}.",
        ) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Fleet hub error: {e}") from e

    if res.status_code >= 400:
        detail = "Fleet hub rejected the request."
        try:
            detail = res.json().get("error", detail)
        except ValueError:
            pass
        # 401 from the hub means our token is wrong, which is our problem, not
        # the operator's session — do not bubble it up as a login failure.
        status = 502 if res.status_code == 401 else res.status_code
        raise HTTPException(status_code=status, detail=detail)

    if res.status_code == 204 or not res.content:
        return {}
    return res.json()


@router.get("/state", dependencies=[Depends(require_admin)])
async def state() -> dict:
    """Servers, tags, scripts and recent runs — everything the tab renders."""
    if not settings.fleet_token:
        # A soft answer so the tab can explain itself instead of erroring.
        return {"configured": False, "servers": [], "tags": [], "scripts": [], "runs": []}
    data = await _call("GET", "/api/state")
    return {
        "configured": True,
        "hubUrl": settings.fleet_public_url or settings.fleet_hub_url,
        "servers": data.get("servers", []),
        "tags": data.get("tags", []),
        "scripts": data.get("scripts", []),
        "runs": data.get("runs", []),
        "history": data.get("history", []),
        "inventoryError": data.get("inventoryError"),
    }


@router.post("/runs", dependencies=[Depends(require_admin)])
async def start_run(body: dict) -> dict:
    """Fan a script or command out across the selected servers."""
    payload = {
        "kind": "script" if body.get("kind") == "script" else "adhoc",
        "script": body.get("script"),
        "command": body.get("command"),
        "args": body.get("args", ""),
        "ids": body.get("ids", []),
        "tags": body.get("tags", []),
        "dryRun": bool(body.get("dryRun")),
    }
    if body.get("sudo") is not None:
        payload["sudo"] = bool(body["sudo"])
    return await _call("POST", "/api/runs", json=payload)


@router.get("/runs/{run_id}", dependencies=[Depends(require_admin)])
async def run_detail(run_id: str) -> dict:
    return await _call("GET", f"/api/runs/{run_id}")


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_run(run_id: str) -> dict:
    return await _call("POST", f"/api/runs/{run_id}/cancel")


@router.post("/health", dependencies=[Depends(require_admin)])
async def recheck(body: dict | None = None) -> dict:
    return await _call("POST", "/api/health", json=body or {})


@router.get("/stream", dependencies=[Depends(require_admin)])
async def stream(request: Request) -> StreamingResponse:
    """Pass the hub's server-sent events straight through to the console.

    Output arrives per host as it happens, so this stays a stream end to end
    rather than being buffered into one response.
    """
    _require_configured()
    url = f"{settings.fleet_hub_url.rstrip('/')}/api/stream"

    async def relay():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
                async with client.stream("GET", url, headers=_headers()) as res:
                    if res.status_code != 200:
                        yield b"event: error\ndata: {\"error\":\"hub stream unavailable\"}\n\n"
                        return
                    async for chunk in res.aiter_raw():
                        if await request.is_disconnected():
                            break
                        yield chunk
        except httpx.HTTPError:
            yield b"event: error\ndata: {\"error\":\"hub stream disconnected\"}\n\n"

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

"""Admin ▸ Data ▸ Leak datasets: files the operator adds to the leak search.

An upload is a short session — create, send 4 MB pieces, finish — because the
reverse proxies cap a request body at 10 MB. The dataset row is the session, so
a half-sent upload is visible (and removable) in the list rather than invisible.
Every change is written to the audit log with the acting admin.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..auth import require_admin
from ..services import users
from ..services.analytics import client_ip
from ..services.leaks import local

router = APIRouter(prefix="/admin/leak-datasets", tags=["admin-leaks"],
                   dependencies=[Depends(require_admin)])


class StartUpload(BaseModel):
    filename: str
    size: int
    name: str = ""
    description: str = ""


class UpdateDataset(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    description: str | None = None


def _fail(e: local.UploadError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


def _must_get(dataset_id: int) -> dict:
    ds = local.get(dataset_id)
    if not ds:
        raise HTTPException(status_code=404, detail="No such dataset.")
    return ds


@router.get("")
def list_datasets() -> dict:
    return {"datasets": local.list_datasets(), "limits": local.limits()}


@router.post("", status_code=201)
def start_upload(body: StartUpload, actor: dict = Depends(require_admin)) -> dict:
    try:
        return local.create_upload(body.filename, body.size, body.name, body.description,
                                   actor.get("email") or "")
    except local.UploadError as e:
        raise _fail(e) from e


@router.put("/{dataset_id}/chunk")
async def upload_chunk(dataset_id: int, request: Request, offset: int = Query(..., ge=0)) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > local.CHUNK_MAX:
        raise HTTPException(status_code=413, detail="Chunk too large.")
    buf = bytearray()
    async for part in request.stream():
        buf += part
        if len(buf) > local.CHUNK_MAX:          # chunked transfer has no Content-Length to trust
            raise HTTPException(status_code=413, detail="Chunk too large.")
    try:
        return await asyncio.to_thread(local.append_chunk, dataset_id, offset, bytes(buf))
    except local.UploadError as e:
        raise _fail(e) from e


@router.post("/{dataset_id}/finish")
def finish_upload(dataset_id: int, request: Request, actor: dict = Depends(require_admin)) -> dict:
    try:
        ds = local.finish_upload(dataset_id)
    except local.UploadError as e:
        raise _fail(e) from e
    users.audit("leak_dataset.uploaded", actor=actor, target=ds["name"],
                detail=f"{ds['filename']} · {ds['size_bytes']} bytes", ip=client_ip(request))
    return ds


@router.get("/{dataset_id}/preview")
def preview(dataset_id: int) -> dict:
    _must_get(dataset_id)
    return {"rows": local.preview(dataset_id)}


@router.patch("/{dataset_id}")
def update_dataset(dataset_id: int, body: UpdateDataset, request: Request,
                   actor: dict = Depends(require_admin)) -> dict:
    ds = _must_get(dataset_id)
    out = local.update(dataset_id, enabled=body.enabled, name=body.name, description=body.description)
    if body.enabled is not None and body.enabled != ds["enabled"]:
        users.audit("leak_dataset.resumed" if body.enabled else "leak_dataset.paused", actor=actor,
                    target=ds["name"], ip=client_ip(request))
    elif body.name is not None or body.description is not None:
        users.audit("leak_dataset.edited", actor=actor, target=ds["name"], ip=client_ip(request))
    return out  # type: ignore[return-value]


@router.delete("/{dataset_id}")
def remove_dataset(dataset_id: int, request: Request, actor: dict = Depends(require_admin)) -> dict:
    ds = _must_get(dataset_id)
    try:
        local.remove(dataset_id)
    except local.UploadError as e:
        raise _fail(e) from e
    users.audit("leak_dataset.removed", actor=actor, target=ds["name"],
                detail=f"{ds['records']} record(s)", ip=client_ip(request))
    return {"ok": True}

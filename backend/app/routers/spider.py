"""Spider — the correlation / pivoting tool.

A scan starts one identifier fanning out across the sources it turns up in and
the identifiers those reveal, building a graph. Like the dark-web search it runs
as a background async job (a full scan is many lookups and takes a while), and
the console polls `GET /spider/jobs/{id}` while it fills in live.

Unlike every other tool, a spider scan is kept — per account — so a later scan
can flag an identifier the account already turned up in an earlier search. That
history is read and deleted through `/spider/history*`, always scoped to the
signed-in account.

Metering: paid plans only (the free trial gets the upgrade card, never a scan),
and a whole scan — however many lookups it makes — costs one search.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..jobs import Job, store
from ..models import (
    SpiderJob,
    SpiderScanDetail,
    SpiderScanRequest,
    SpiderScanSummary,
)
from ..services import spider
from ..services import usage
from ..services.leaks.base import mask_secret
from ..services.spider import store as history

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/spider", tags=["spider"], dependencies=[Depends(require_session)]
)

# Secrets ride the same mask-by-default policy as the leak search. In practice
# every account that may run a scan can also reveal (both gate on a paid plan),
# so this only ever masks for a hypothetical no-reveal paid tier — kept anyway
# so a password never leaves the API in the clear by accident.
_SECRET_TYPES = {"password", "hash"}


def _apply_mask(graph: dict, reveal: bool) -> None:
    if reveal:
        return
    for n in graph.get("nodes", []):
        if n.get("type") in _SECRET_TYPES:
            n["label"] = mask_secret(n.get("label")) or n.get("label")
            n["value"] = mask_secret(n.get("value")) or n.get("value")
            n["masked"] = True


async def _run_scan(job: Job, req: SpiderScanRequest, user: dict, reveal: bool) -> None:
    try:
        graph = {"nodes": [], "edges": []}
        stats = None
        async for event in spider.run_scan_events(
            req.seed, job.data["seed_kind"], modules=req.modules, max_nodes=req.max_nodes
        ):
            kind = event["type"]
            if kind == "start":
                job.data["modules"] = event["modules"]
                job.status = "running"
                job.progress = 0.05
                job.message = f"expanding {len(event['modules'])} module(s)…"
            elif kind == "lookup":
                job.message = f"{event['module']} · {event['node']['type']}"
            elif kind == "nodes":
                # Progress is soft — a scan has no fixed size — so creep toward
                # 0.9 as nodes accrue rather than pretend to know the total.
                job.progress = min(0.9, job.progress + 0.03)
            elif kind == "done":
                graph = event["graph"]
                stats = event["stats"]

        # Correlate against this account's earlier scans, then persist.
        history.annotate(user["id"], graph)
        _apply_mask(graph, reveal)
        scan_id = history.save(
            user["id"], seed=req.seed, seed_kind=job.data["seed_kind"],
            modules=[m["key"] for m in job.data.get("modules", [])], graph=graph,
        )
        job.data["graph"] = graph
        job.data["stats"] = stats
        job.data["scan_id"] = scan_id
        job.progress = 1.0
        job.status = "done"
        n = len((graph or {}).get("nodes", []))
        job.message = f"{n} node{'s' if n != 1 else ''}"
    except Exception as exc:  # noqa: BLE001
        log.exception("spider scan crashed")
        job.status = "error"
        job.progress = 1.0
        job.message = "scan failed"
        # Only staff see the internals; a scan fault is ours, so give the search back.
        job.error = (f"{type(exc).__name__}: {exc}" if user.get("role") in ("admin", "operator")
                     else "The scan failed unexpectedly. Try again.")
        usage.refund(user)


@router.post("/scan")
async def start_scan(req: SpiderScanRequest, user: dict = Depends(require_session)):
    # Paid-plans-only, refused before charging (mirrors the reveal gate in leaks).
    if not usage.is_paid(user):
        raise HTTPException(
            status_code=403,
            detail="The spider is a paid-plan tool. Upgrade to run correlation scans.",
            headers={"X-Upgrade-Path": "/pricing"},
        )
    kind = req.kind
    if kind == "auto":
        kind = spider.detect_seed_kind(req.seed)
    usage.take(user)
    job = store.create(
        "spider", seed=req.seed, seed_kind=kind, owner=user.get("id"),
        modules=[], graph={"nodes": [], "edges": []}, stats=None, scan_id=None,
    )
    reveal = usage.reveals_secrets(user)
    job._task = asyncio.create_task(_run_scan(job, req, user, reveal))
    return {"job_id": job.job_id, "status": job.status}


@router.get("/jobs/{job_id}", response_model=SpiderJob)
async def get_job(job_id: str, user: dict = Depends(require_session)) -> SpiderJob:
    job = store.get(job_id)
    # 404, not 403, for someone else's job: don't confirm the id exists.
    if (
        not job
        or job.kind != "spider"
        or (job.data.get("owner") != user.get("id") and user.get("role") != "admin")
    ):
        raise HTTPException(status_code=404, detail="Scan not found.")
    d = job.as_dict()
    return SpiderJob(
        job_id=job.job_id,
        status=job.status,  # type: ignore[arg-type]
        seed=d.get("seed", ""),
        kind=d.get("seed_kind", "auto"),
        progress=job.progress,
        message=job.message,
        modules=d.get("modules", []),
        graph=d.get("graph", {"nodes": [], "edges": []}),
        stats=d.get("stats"),
        scan_id=d.get("scan_id"),
        error=job.error,
    )


@router.get("/history", response_model=list[SpiderScanSummary])
async def list_history(user: dict = Depends(require_session)) -> list[SpiderScanSummary]:
    return [SpiderScanSummary(**row) for row in history.list_scans(user["id"])]


@router.get("/history/{scan_id}", response_model=SpiderScanDetail)
async def get_history(scan_id: int, user: dict = Depends(require_session)) -> SpiderScanDetail:
    row = history.get(user["id"], scan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return SpiderScanDetail(**row)


@router.delete("/history/{scan_id}")
async def delete_history(scan_id: int, user: dict = Depends(require_session)) -> dict:
    if not history.delete(user["id"], scan_id):
        raise HTTPException(status_code=404, detail="Scan not found.")
    return {"deleted": True}

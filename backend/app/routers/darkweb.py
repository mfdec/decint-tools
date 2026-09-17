"""Dark-web search — async jobs (Tor is slow, ahmia is quick but still I/O)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..jobs import Job, store
from ..models import DarkwebJob, DarkwebSearchRequest, JobRef
from ..services import darkweb, usage

router = APIRouter(
    prefix="/darkweb", tags=["darkweb"], dependencies=[Depends(require_session)]
)


async def _run_job(job: Job, req: DarkwebSearchRequest, user: dict) -> None:
    job.status = "running"
    job.progress = 0.15
    job.message = f"querying ({req.mode})…"
    try:
        if req.mode == "tor":
            results, manifest = await asyncio.to_thread(
                darkweb.run_tor, req.query, req.verify, req.limit
            )
        else:
            results, manifest = await asyncio.to_thread(
                darkweb.run_ahmia, req.query, req.limit
            )
        job.data["results"] = [r.model_dump() for r in results]
        job.data["manifest"] = manifest
        job.progress = 1.0
        job.status = "done"
        job.message = f"{len(results)} result(s)"
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = f"{type(e).__name__}: {e}"
        job.message = "search failed"
        # Charged at submission; a Tor circuit that never came up is not the
        # customer's search to lose.
        usage.refund(user)


@router.post("/search", response_model=JobRef)
async def start_search(
    req: DarkwebSearchRequest, user: dict = Depends(require_session)
) -> JobRef:
    usage.take(user)
    job = store.create(
        "darkweb", mode=req.mode, query=req.query, results=[], manifest={}
    )
    job._task = asyncio.create_task(_run_job(job, req, user))
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/jobs/{job_id}", response_model=DarkwebJob)
async def get_job(job_id: str) -> DarkwebJob:
    job = store.get(job_id)
    if not job or job.kind != "darkweb":
        raise HTTPException(status_code=404, detail="Job not found.")
    d = job.as_dict()
    return DarkwebJob(
        job_id=job.job_id,
        status=job.status,  # type: ignore[arg-type]
        mode=d.get("mode", "ahmia"),
        query=d.get("query", ""),
        progress=job.progress,
        message=job.message,
        results=d.get("results", []),
        manifest=d.get("manifest", {}),
        error=job.error,
    )

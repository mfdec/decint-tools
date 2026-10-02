"""Dark-web search — async jobs (a Tor fan-out takes tens of seconds; gateway mode is quick).

POST /darkweb/search starts a job; the console polls GET /darkweb/jobs/{id}. While the job
runs, `engines` fills in one entry per engine as it answers, so the page can show the fan-out
happening instead of a spinner. The query text is never logged or persisted beyond the
in-memory job, which is only readable by the account that started it.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_session
from ..config import settings
from ..jobs import Job, store
from ..models import DarkwebJob, DarkwebMode, DarkwebSearchRequest, JobRef
from ..services import darkweb, usage
from ..services import users as users_svc

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/darkweb", tags=["darkweb"], dependencies=[Depends(require_session)]
)

_STAFF = ("admin", "operator")
_UNAVAILABLE = {
    "tor": (
        "Tor search is unavailable right now: none of the onion engines answered. "
        "Try again in a minute, or use gateway mode."
    ),
    "gateway": "The search gateways did not answer. Try again in a minute.",
}


def _fail(job: Job, user: dict, public: str, staff_detail: str | None = None) -> None:
    """Settle a job as failed and give the search back — the provider failed, not the customer.

    Customers get a plain message; only staff see the internals (proxy address, exception type).
    """
    job.status = "error"
    job.progress = 1.0
    job.message = "search failed"
    job.error = staff_detail if staff_detail and user.get("role") in _STAFF else public
    usage.refund(user)


async def _run_job(job: Job, req: DarkwebSearchRequest, user: dict) -> None:
    try:
        if darkweb.slots_busy():
            job.message = "queued — waiting for a free search slot"
        async with darkweb.search_slot():
            # Timed from here, not from submission: waiting for a slot is not the search being
            # slow. The cap is for a hung parser or stuck circuit that would pin a slot forever.
            async with asyncio.timeout(settings.darkweb_deadline + 45):
                await _search(job, req, user)
    except TimeoutError:
        _fail(job, user, "The search took too long and was stopped. Try again.", "job timed out")
    except Exception as exc:  # noqa: BLE001
        log.exception("dark-web search crashed (mode=%s)", req.mode)  # never the query text
        _fail(job, user, "The search failed unexpectedly. Try again.", f"{type(exc).__name__}: {exc}")


async def _search(job: Job, req: DarkwebSearchRequest, user: dict) -> None:
    job.status = "running"
    job.progress = 0.05
    job.message = "starting…"
    planned = answered = 0
    async for event in darkweb.search_events(
        req.query, req.mode, pages=req.pages, experimental=req.experimental, limit=req.limit
    ):
        kind = event["type"]
        if kind == "start":
            planned = len(event["engines"])
            job.data["engines_planned"] = planned
            job.data["transport"] = event["transport"]
            job.message = f"querying {planned} engine{'s' if planned != 1 else ''}…"
        elif kind == "engine":
            answered += 1
            job.data["engines"].append(event["report"].model_dump())
            job.progress = 0.08 + 0.87 * answered / max(planned, 1)
            job.message = f"{answered}/{planned} engines answered"
        elif kind == "results":
            _finish(job, req, user, event["response"], event["manifest"])


def _finish(job: Job, req: DarkwebSearchRequest, user: dict, response, manifest: dict) -> None:
    job.data.update(
        engines=[r.model_dump() for r in response.engines] or job.data["engines"],
        results=[r.model_dump() for r in response.results],
        pruned=[p.model_dump() for p in response.pruned],
        stats=response.stats.model_dump(),
        operators=response.operators,
        manifest=manifest,
    )
    if response.blocked_query or darkweb.upstream_failed(response):
        # Nothing here is the customer's to lose: refused queries are caught before they are
        # charged (so this is a backstop), and "no engine answered" is our outage.
        log.warning("dark-web search got no answer (mode=%s): %s", req.mode, response.message)
        _fail(job, user, response.message if response.blocked_query else _UNAVAILABLE[req.mode],
              response.message)
        return
    job.progress = 1.0
    job.status = "done"
    n = len(response.results)
    job.message = f"{n} result{'s' if n != 1 else ''}"


@router.post("/search", response_model=JobRef)
async def start_search(
    req: DarkwebSearchRequest, user: dict = Depends(require_session)
) -> JobRef:
    # Refuse before charging: a query that cannot or must not be searched costs nothing.
    try:
        darkweb.preflight(req.query)
    except ValueError as e:
        if str(e) == darkweb.BLOCKED_QUERY_MESSAGE:
            # Count only, never the text: an abuse signal that keeps the no-queries promise.
            users_svc.audit("darkweb.blocked", actor=user)
        raise HTTPException(status_code=422, detail=str(e)) from e
    usage.take(user)
    job = store.create(
        "darkweb", mode=req.mode, query=req.query, owner=user.get("id"),
        transport="", engines_planned=0, engines=[], results=[], pruned=[],
        stats=None, operators={}, manifest={},
    )
    job._task = asyncio.create_task(_run_job(job, req, user))
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/jobs/{job_id}", response_model=DarkwebJob)
async def get_job(job_id: str, user: dict = Depends(require_session)) -> DarkwebJob:
    job = store.get(job_id)
    # 404, not 403, for someone else's job: don't confirm that the id exists.
    if (
        not job
        or job.kind != "darkweb"
        or (job.data.get("owner") != user.get("id") and user.get("role") != "admin")
    ):
        raise HTTPException(status_code=404, detail="Job not found.")
    d = job.as_dict()
    return DarkwebJob(
        job_id=job.job_id,
        status=job.status,  # type: ignore[arg-type]
        mode=d.get("mode", "gateway"),
        query=d.get("query", ""),
        progress=job.progress,
        message=job.message,
        transport=d.get("transport", ""),
        engines_planned=d.get("engines_planned", 0),
        engines=d.get("engines", []),
        results=d.get("results", []),
        pruned=d.get("pruned", []),
        stats=d.get("stats"),
        operators=d.get("operators", {}),
        manifest=d.get("manifest", {}),
        error=job.error,
    )


@router.get("/engines")
async def engines(mode: DarkwebMode = Query("gateway")) -> list[dict]:
    """The engines a search in this mode queries, with live health (benched, success rate)."""
    return darkweb.engine_roster(mode)

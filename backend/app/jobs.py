"""In-process async job store.

Dark-web searches over Tor are slow (tens of seconds) and must not block a
request, so they run as background asyncio tasks and the client polls. This is
a single-process store — fine for one operator; swap for Redis/RQ if this ever
needs to scale across workers.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    _task: asyncio.Task | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
        }
        d.update(self.data)
        return d


class JobStore:
    def __init__(self, max_jobs: int = 200) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._max = max_jobs

    def create(self, kind: str, **data: Any) -> Job:
        job_id = secrets.token_hex(8)
        job = Job(job_id=job_id, kind=kind, data=data)
        self._jobs[job_id] = job
        self._order.append(job_id)
        self._evict()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _evict(self) -> None:
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._jobs.pop(old, None)


store = JobStore()

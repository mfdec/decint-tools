"""Source health: is each outside source a tool depends on actually answering?

Every leak provider and lookup source degrades quietly by design: a dead
source returns "no results" or an error string instead of raising, so one
flaky source can't sink a search. The price is that an outage looks like an
empty answer from the outside. This module keeps score. Each tool reports
every call it makes to an outside source (answered or not, how long it took,
why it failed), and the admin `health` command reads the tally.

In memory only, since the process started: there is one API process, and a
restart is a fresh slate. Nothing here holds a query, only source names,
counts, timings and error strings, which the tools keep free of queries.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

T = TypeVar("T")

_ALPHA = 0.3  # EWMA smoothing for the success rate and latency
# This many failures in a row and the source reads as down rather than flaky.
DOWN_AFTER = 3

STARTED = time.time()


@dataclass
class SourceStat:
    tool: str
    key: str
    label: str
    ok: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    success_rate: float = 1.0
    latency_ms: float | None = None  # successful calls only
    last_ok: float | None = None  # epoch seconds
    last_failure: float | None = None
    last_error: str | None = None

    @property
    def state(self) -> str:
        if self.ok + self.failed == 0:
            return "idle"
        if self.consecutive_failures >= DOWN_AFTER:
            return "down"
        if self.consecutive_failures:
            return "degraded"
        return "ok"


_stats: dict[str, SourceStat] = {}

# A provider's own error text can echo what it was asked. Belt and braces on
# top of the tools wording their errors without the query.
_SCRUB = re.compile(
    r"[^\s@]+@[^\s@]+\.[^\s@]+"      # an email address
    r"|\b[0-9a-fA-F]{32,}\b"          # a hash
    r"|\+?\d[\d\s().-]{5,}\d"         # a phone number or similar digit run
)


def _scrub(text: str) -> str:
    return _SCRUB.sub("…", text)[:200]


def _id(tool: str, key: str) -> str:
    return f"{tool}.{key}"


def register(tool: str, key: str, label: str) -> None:
    """Declare a source up front, so `health` lists it before its first call."""
    stat = _stats.setdefault(_id(tool, key), SourceStat(tool=tool, key=key, label=label))
    stat.label = label


def record(
    tool: str, key: str, label: str, ok: bool, *,
    latency_ms: float | None = None, error: str | None = None,
) -> None:
    """One call to one source. Never raises: bookkeeping must not fail a search."""
    try:
        stat = _stats.setdefault(_id(tool, key), SourceStat(tool=tool, key=key, label=label))
        now = time.time()
        stat.success_rate = (1 - _ALPHA) * stat.success_rate + _ALPHA * (1.0 if ok else 0.0)
        if ok:
            stat.ok += 1
            stat.consecutive_failures = 0
            stat.last_ok = now
            if latency_ms is not None:
                stat.latency_ms = (
                    latency_ms if stat.latency_ms is None
                    else (1 - _ALPHA) * stat.latency_ms + _ALPHA * latency_ms
                )
        else:
            stat.failed += 1
            stat.consecutive_failures += 1
            stat.last_failure = now
            stat.last_error = _scrub(error or "failed")
    except Exception:  # pragma: no cover - defensive
        pass


def resting(tool: str, key: str, seconds: float) -> bool:
    """True while a source that is down should be left alone: it failed
    DOWN_AFTER times running, the last time less than `seconds` ago. After
    that it gets one call again, and a success clears it."""
    stat = _stats.get(_id(tool, key))
    if stat is None or stat.consecutive_failures < DOWN_AFTER or stat.last_failure is None:
        return False
    return time.time() - stat.last_failure < seconds


class Timer:
    """`t = Timer()` ... `t.ms` — milliseconds since it was made."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    @property
    def ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000


def tracked(
    tool: str, key: str, label: str,
    safe: tuple[type[BaseException], ...] = (),
    answered: Callable[[BaseException], bool] | None = None,
):
    """Decorate the async function that makes one call to one source: a return
    is a success, a raise is a failure. Only the exception types in `safe`
    have their message kept. Those are the tools' own errors, worded without
    the query. Anything else is kept as its type name, because an httpx error
    can quote the URL, and the URL can hold what was looked up.

    `answered(e)` marks a raise that is still the source working, such as a
    provider rejecting malformed input; it counts as a success."""
    register(tool, key, label)

    def wrap(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def inner(*args: Any, **kwargs: Any) -> T:
            t = Timer()
            try:
                out = await fn(*args, **kwargs)
            except Exception as e:
                if answered is not None and answered(e):
                    record(tool, key, label, True, latency_ms=t.ms)
                    raise
                msg = (str(e) or type(e).__name__) if isinstance(e, safe) else type(e).__name__
                record(tool, key, label, False, error=msg)
                raise
            record(tool, key, label, True, latency_ms=t.ms)
            return out

        return inner

    return wrap


def snapshot() -> list[dict[str, Any]]:
    """Every source, grouped by tool, with its state worked out."""
    out = []
    for stat in sorted(_stats.values(), key=lambda s: (s.tool, s.key)):
        d = asdict(stat)
        d["state"] = stat.state
        d["success_rate"] = round(stat.success_rate, 2)
        d["latency_ms"] = None if stat.latency_ms is None else round(stat.latency_ms)
        out.append(d)
    return out


def reset() -> None:
    """Forget every tally (tests). Registered sources are kept, zeroed."""
    for k, s in list(_stats.items()):
        _stats[k] = SourceStat(tool=s.tool, key=s.key, label=s.label)

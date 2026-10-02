"""Per-engine health: success/latency averages, adaptive timeouts and a circuit breaker.

Onion engines come and go constantly. Engines that fail several searches in a row are benched
for a cool-down period so they stop eating the search deadline; a success resets them.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_ALPHA = 0.3  # EWMA smoothing


@dataclass
class EngineHealth:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    success_rate: float = 1.0
    latency_s: float | None = None
    last_ok: float | None = None
    last_error: str | None = None
    benched_until: float = 0.0


class HealthStore:
    def __init__(self, path: Path | None, *, failures_to_bench: int = 3, cooldown: float = 1800.0):
        self.path = path
        self.failures_to_bench = failures_to_bench
        self.cooldown = cooldown
        self._data: dict[str, EngineHealth] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text())
            self._data = {name: EngineHealth(**vals) for name, vals in raw.items()}
        except (OSError, ValueError, TypeError) as exc:
            log.warning("ignoring unreadable health file %s: %s", self.path, exc)

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".health-")
            with os.fdopen(fd, "w") as fh:
                json.dump({k: asdict(v) for k, v in self._data.items()}, fh, indent=1)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("could not save engine health to %s: %s", self.path, exc)

    def get(self, name: str) -> EngineHealth:
        return self._data.setdefault(name, EngineHealth())

    def is_benched(self, name: str, now: float | None = None) -> bool:
        return self.get(name).benched_until > (now or time.time())

    def record(self, name: str, ok: bool, latency_s: float | None = None, error: str | None = None) -> None:
        h = self.get(name)
        h.success_rate = (1 - _ALPHA) * h.success_rate + _ALPHA * (1.0 if ok else 0.0)
        if ok:
            h.successes += 1
            h.consecutive_failures = 0
            h.benched_until = 0.0
            h.last_ok = time.time()
            h.last_error = None
            if latency_s is not None:
                h.latency_s = latency_s if h.latency_s is None else (1 - _ALPHA) * h.latency_s + _ALPHA * latency_s
        else:
            h.failures += 1
            h.consecutive_failures += 1
            h.last_error = error
            if h.consecutive_failures >= self.failures_to_bench:
                h.benched_until = time.time() + self.cooldown

    def timeout_for(self, name: str, base: float) -> float:
        """Healthy fast engines get a tighter read timeout so dead circuits fail sooner."""
        latency = self.get(name).latency_s
        if latency is None:
            return base
        return max(10.0, min(base, latency * 3 + 5))

    def snapshot(self) -> dict[str, dict]:
        return {k: asdict(v) for k, v in self._data.items()}

    def reset(self, name: str | None = None) -> None:
        if name is None:
            self._data.clear()
        else:
            self._data.pop(name, None)

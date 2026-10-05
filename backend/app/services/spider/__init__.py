"""Spider — the correlation/pivoting tool (SpiderFoot-style).

The leak search gives flat, per-source rows. The spider turns one seed
identifier into a graph: every source that identifier turns up in becomes a
node, the emails/usernames/domains found there become new nodes, and those are
expanded in turn — so you can see what links to what, and which identifier ties
two findings together.

Unlike the other tools, a spider scan *is* persisted (per account, see
`store.py`): that is what lets a later scan say "this same item showed up in one
of your earlier searches". The global "we don't store what you search for"
promise carries a Spider carve-out on the privacy page.

Routers talk to this package through `run_scan_events()` (an async event
stream, mirroring `services/darkweb.search_events`) and the `store` module.
"""

from __future__ import annotations

from ..leaks.base import detect_kind as detect_seed_kind
from .engine import run_scan_events
from .modules import MODULES, enabled_modules

__all__ = [
    "MODULES",
    "detect_seed_kind",
    "enabled_modules",
    "run_scan_events",
]

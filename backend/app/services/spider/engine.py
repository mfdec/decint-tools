"""The spider loop: a bounded breadth-first expansion of the graph.

Start from the seed node, expand it with every module that consumes its type,
add whatever they find, and enqueue the new nodes to be expanded in turn —
until one of three limits is hit: the node budget (`spider_max_nodes`), the
lookup budget (`spider_lookup_budget`, one lookup = one node expanded by one
module), or the depth limit (`spider_max_depth`). The limits are what keep a
single scan — which costs the customer exactly one search — from fanning out
without bound.

`run_scan_events()` is an async generator of events, mirroring the shape of
`services/darkweb.search_events` so the router can drive a background job and
report progress live:

  {"type": "start",  "modules": [...], "seed": {...}}
  {"type": "lookup", "module": "...", "node": {...}}      # about to expand
  {"type": "nodes",  "added": [NodeDict, ...], "edges": [EdgeDict, ...]}
  {"type": "done",   "graph": {"nodes": [...], "edges": [...]}, "stats": {...}}
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ...config import settings
from .graph import SEED_TYPES, Edge, Graph, Node, canonical
from .modules import Module, enabled_modules

log = logging.getLogger("decint.spider")


def _node_dict(n: Node) -> dict[str, Any]:
    return {
        "type": n.type, "value": n.value, "label": n.label, "depth": n.depth,
        "sources": sorted(n.sources), "detail": n.detail, "url": n.url,
        "masked": n.masked, "seen_before": n.seen_before,
    }


def _edge_dict(e: Edge) -> dict[str, Any]:
    return {"src": list(e.src), "dst": list(e.dst), "source": e.source, "label": e.label}


async def run_scan_events(
    seed_value: str,
    seed_kind: str,
    *,
    modules: list[str] | None = None,
    max_nodes: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run a scan, yielding progress events. Never raises for a module fault —
    a module that errors simply contributes nothing (see modules.py)."""
    seed_kind = seed_kind if seed_kind in SEED_TYPES else "username"
    seed_value = canonical(seed_kind, seed_value)

    active: list[Module] = [m for m in enabled_modules()
                            if modules is None or m.key in modules]
    node_cap = min(max_nodes or settings.spider_max_nodes, settings.spider_max_nodes)
    lookup_budget = settings.spider_lookup_budget
    depth_cap = settings.spider_max_depth

    graph = Graph()
    seed = graph.make(seed_kind, seed_value, depth=0, source="seed", label=seed_value)
    if seed is None:
        yield {"type": "done", "graph": {"nodes": [], "edges": []},
               "stats": {"nodes": 0, "edges": 0, "lookups": 0, "truncated": False}}
        return

    yield {"type": "start",
           "modules": [{"key": m.key, "name": m.name} for m in active],
           "seed": _node_dict(seed)}

    queue: deque[Node] = deque([seed])
    expanded: set[tuple[str, str]] = set()
    lookups = 0
    truncated = False

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=httpx.Timeout(settings.spider_timeout)
    ) as client:
        while queue:
            node = queue.popleft()
            if node.key in expanded:
                continue
            expanded.add(node.key)
            mods = [m for m in active if node.type in m.consumes]
            for mod in mods:
                if lookups >= lookup_budget:
                    truncated = True
                    break
                lookups += 1
                yield {"type": "lookup", "module": mod.key, "node": _node_dict(node)}
                try:
                    findings = await mod.expand(client, node)
                except Exception as exc:  # noqa: BLE001 — defensive; modules already guard
                    log.debug("spider module %s raised: %s", mod.key, type(exc).__name__)
                    findings = []

                added_nodes: list[dict] = []
                added_edges: list[dict] = []
                for f in findings:
                    child = graph.make(
                        f.type, f.value, depth=node.depth + 1, source=mod.key,
                        label=f.display or f.value, detail=f.detail, url=f.url,
                    )
                    if child is None:
                        continue
                    is_new = child.key not in {(n["type"], n["value"]) for n in added_nodes}
                    edge = Edge(src=node.key, dst=child.key, source=mod.key, label=f.label)
                    before = len(graph.edges)
                    graph.add_edge(edge)
                    if len(graph.edges) > before:
                        added_edges.append(_edge_dict(edge))
                    # Enqueue for further expansion when there's still room and
                    # it hasn't gone past the depth limit.
                    if (child.depth < depth_cap
                            and len(graph.nodes) <= node_cap
                            and child.key not in expanded):
                        queue.append(child)
                    if is_new:
                        added_nodes.append(_node_dict(child))

                if added_nodes or added_edges:
                    # Re-emit the possibly-updated source node too (detail may
                    # have been annotated by the module, e.g. RDAP registrar).
                    yield {"type": "nodes", "added": added_nodes,
                           "edges": added_edges, "source_node": _node_dict(node)}

                if len(graph.nodes) >= node_cap:
                    truncated = True
            if lookups >= lookup_budget or len(graph.nodes) >= node_cap:
                truncated = truncated or bool(queue)
                break

    yield {
        "type": "done",
        "graph": {
            "nodes": [_node_dict(n) for n in graph.nodes.values()],
            "edges": [_edge_dict(e) for e in graph.edges],
        },
        "stats": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "lookups": lookups,
            "truncated": truncated,
        },
    }

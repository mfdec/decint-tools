"""Per-account spider scan history, in the app database.

Spider is the one tool that keeps what it was asked about — that is what lets a
later scan say "this same item showed up in one of your earlier searches". The
scans live in the shared app DB (`app/db.py`), not in the leak-datasets file:
a scan is tens of nodes, not a bulk import, so it sits comfortably alongside
`usage_counters` and `tickets` under the one connection.

Everything here is scoped to a `user_id`. One account can never read, correlate
against or delete another's scans — the router passes the signed-in user's id
and every query filters on it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ... import db

# How many scans an account keeps; the oldest are dropped past this on save.
from ...config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(user_id: int, *, seed: str, seed_kind: str, modules: list[str],
         graph: dict, title: str | None = None) -> int:
    """Persist a finished scan and return its id. Trims the account's history
    to `spider_history_max`, newest kept."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    now = _now()
    scan_id = db.execute(
        "INSERT INTO spider_scans "
        "(user_id, seed, seed_kind, title, modules, node_count, edge_count, graph, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, seed, seed_kind, title or seed, json.dumps(modules),
         len(nodes), len(edges), json.dumps(graph), now),
    )
    rows = [
        (scan_id, user_id, n["type"], n["value"], n.get("label") or n["value"], now)
        for n in nodes
    ]
    if rows:
        db.executemany(
            "INSERT INTO spider_nodes (scan_id, user_id, type, value, label, created_at) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
    _trim(user_id)
    return scan_id


def _trim(user_id: int) -> None:
    keep = max(1, settings.spider_history_max)
    stale = db.query(
        "SELECT id FROM spider_scans WHERE user_id = ? ORDER BY created_at DESC, id DESC "
        "LIMIT -1 OFFSET ?",
        (user_id, keep),
    )
    for row in stale:
        delete(user_id, row["id"])


def seen_before(user_id: int, nodes: list[dict], exclude_scan_id: int | None = None) -> dict:
    """For each node value in `nodes`, the account's earlier scans it already
    appeared in. Keyed by "type:value" → list of {scan_id, at, seed}.

    This is the correlation signal: a hit returned here is the same identifier
    the account turned up in a previous search.
    """
    if not nodes:
        return {}
    out: dict[str, list[dict]] = {}
    for n in nodes:
        params: list = [user_id, n["type"], n["value"]]
        extra = ""
        if exclude_scan_id is not None:
            extra = " AND s.id != ?"
            params.append(exclude_scan_id)
        rows = db.query(
            "SELECT s.id AS scan_id, s.seed, s.created_at "
            "FROM spider_nodes sn JOIN spider_scans s ON s.id = sn.scan_id "
            "WHERE sn.user_id = ? AND sn.type = ? AND sn.value = ?" + extra +
            " ORDER BY s.created_at DESC LIMIT 10",
            tuple(params),
        )
        if rows:
            out[f"{n['type']}:{n['value']}"] = [
                {"scan_id": r["scan_id"], "at": r["created_at"], "seed": r["seed"]}
                for r in rows
            ]
    return out


def annotate(user_id: int, graph: dict, exclude_scan_id: int | None = None) -> dict:
    """Stamp each node in `graph` with its `seen_before` history, in place."""
    hist = seen_before(user_id, graph.get("nodes", []), exclude_scan_id)
    for n in graph.get("nodes", []):
        n["seen_before"] = hist.get(f"{n['type']}:{n['value']}", [])
    return graph


def list_scans(user_id: int) -> list[dict]:
    rows = db.query(
        "SELECT id, seed, seed_kind, title, modules, node_count, edge_count, created_at "
        "FROM spider_scans WHERE user_id = ? ORDER BY created_at DESC, id DESC",
        (user_id,),
    )
    for r in rows:
        r["modules"] = json.loads(r["modules"]) if r.get("modules") else []
    return rows


def get(user_id: int, scan_id: int) -> dict | None:
    row = db.one(
        "SELECT id, seed, seed_kind, title, modules, node_count, edge_count, graph, created_at "
        "FROM spider_scans WHERE user_id = ? AND id = ?",
        (user_id, scan_id),
    )
    if not row:
        return None
    row["modules"] = json.loads(row["modules"]) if row.get("modules") else []
    row["graph"] = json.loads(row["graph"]) if row.get("graph") else {"nodes": [], "edges": []}
    return row


def delete(user_id: int, scan_id: int) -> bool:
    """Remove one scan and its nodes. Scoped to the owner, so an id that isn't
    theirs deletes nothing and returns False."""
    owned = db.one("SELECT id FROM spider_scans WHERE user_id = ? AND id = ?", (user_id, scan_id))
    if not owned:
        return False
    db.transaction([
        ("DELETE FROM spider_nodes WHERE user_id = ? AND scan_id = ?", (user_id, scan_id)),
        ("DELETE FROM spider_scans WHERE user_id = ? AND id = ?", (user_id, scan_id)),
    ])
    return True

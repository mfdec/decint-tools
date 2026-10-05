"""The spider's graph: typed nodes, the edges between them, and the value
normalisation that keeps the same thing found two ways as one node.

A node is `(type, value)`. `value` is the canonical, comparable form — a
lower-cased email, a bare hostname, a lower-cased username — so a node
discovered by the leaks module and again by Gravatar collapses into one, and so
the cross-scan "seen before" lookup in `store.py` compares like with like.

Secrets (passwords, hashes) are nodes too, but they follow the same
mask-by-default policy as the leak search: the value kept on the node is always
the real one (the owner paid to see it), and masking is a presentation choice
left to the API layer, exactly as `services/leaks/service.py` does it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The entity kinds the graph understands. `name` is a person's name (free-text,
# only ever found inside a dataset, never sent to a third party); `account` is a
# profile URL on a specific site; `onion`/`wallet` come from dark-web mentions.
NodeType = str  # one of the below; kept a plain str so pydantic maps it cleanly

NODE_TYPES = (
    "email", "username", "domain", "name",
    "password", "hash", "breach", "account", "onion", "wallet",
)

# The kinds a seed may be. `name` seeds are allowed but only the leaks module
# (uploaded datasets) can expand them — the public modules never receive a name.
SEED_TYPES = ("email", "username", "domain", "name")

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I)
_SPACE_RE = re.compile(r"\s+")


def canonical(node_type: NodeType, value: str) -> str:
    """The comparable form of a value for a given node type.

    Keeping this in one place is what makes dedupe and the cross-scan lookup
    reliable: `Alice@Example.COM` and `alice@example.com` are the same node, and
    `https://example.com/` and `example.com` are the same domain.
    """
    v = (value or "").strip()
    if node_type in ("email", "username", "domain", "name", "onion"):
        v = v.lower()
    if node_type in ("name",):
        v = _SPACE_RE.sub(" ", v)
    if node_type == "domain":
        v = _host(v) or v
    if node_type == "onion":
        v = v.removeprefix("http://").removeprefix("https://").split("/")[0]
    return v


def _host(value: str) -> str | None:
    """Bare hostname from a URL/email-ish string (mirrors leaks/local._host)."""
    v = value.strip().lower()
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v)
    v = re.split(r"[/?#]", v, 1)[0]
    v = v.rsplit("@", 1)[-1].split(":", 1)[0].removeprefix("www.")
    return v if _DOMAIN_RE.match(v) else None


@dataclass
class Node:
    type: NodeType
    value: str                 # canonical value — the identity of the node
    label: str = ""            # display form (e.g. the original-case value or a title)
    depth: int = 0             # hops from the seed (0 = the seed itself)
    sources: set[str] = field(default_factory=set)  # module keys that produced it
    detail: str | None = None  # a human note (registrar, breach date, profile name…)
    url: str | None = None     # a link the node points at, when it has one
    masked: bool = False       # presentation flag set by the API for secrets
    # Scan ids (and when) where this account has seen this value before. Filled
    # in by store.seen_before() — the "featured in a previous search" signal.
    seen_before: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.type, self.value)


@dataclass
class Edge:
    src: tuple[str, str]       # source node key
    dst: tuple[str, str]       # destination node key
    source: str                # module key that discovered the link
    label: str = ""            # relationship, e.g. "appears in", "profile", "subdomain"


class Graph:
    """Accumulates nodes and edges, collapsing duplicates by node key."""

    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], Node] = {}
        self.edges: list[Edge] = []
        self._edge_keys: set[tuple] = set()

    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.key)
        if existing is None:
            self.nodes[node.key] = node
            return node
        # Merge into the node we already have: keep the earliest depth, union
        # the sources, and fill in any detail/url/label we didn't have yet.
        existing.depth = min(existing.depth, node.depth)
        existing.sources |= node.sources
        existing.detail = existing.detail or node.detail
        existing.url = existing.url or node.url
        existing.label = existing.label or node.label
        return existing

    def add_edge(self, edge: Edge) -> None:
        k = (edge.src, edge.dst, edge.source, edge.label)
        if k in self._edge_keys or edge.src == edge.dst:
            return
        self._edge_keys.add(k)
        self.edges.append(edge)

    def make(self, node_type: NodeType, value: str, *, depth: int,
             source: str, label: str = "", detail: str | None = None,
             url: str | None = None) -> Node | None:
        """Build + add a node from a raw value, or None if the value is empty
        after normalisation."""
        canon = canonical(node_type, value)
        if not canon:
            return None
        node = Node(
            type=node_type, value=canon, label=(label or value or canon),
            depth=depth, sources={source}, detail=detail, url=url,
        )
        return self.add_node(node)

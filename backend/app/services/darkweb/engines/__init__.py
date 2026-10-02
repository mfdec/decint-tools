"""Engine catalog loading."""

from __future__ import annotations

import logging
import tomllib
from importlib import resources
from pathlib import Path

from ..onion import is_valid_v3
from .base import Engine, EngineOutcome, EngineSpec
from .parsers import PARSERS
from .special import PREP_HOOKS

log = logging.getLogger(__name__)

TIERS = ("core", "extended", "experimental")

__all__ = ["Engine", "EngineOutcome", "EngineSpec", "TIERS", "load_catalog"]


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def _clean_host(value: str) -> str:
    host = value.strip().lower()
    for prefix in ("http://", "https://"):
        host = host.removeprefix(prefix)
    return host.split("/", 1)[0]


def load_catalog(path: Path | None = None) -> list[Engine]:
    """Load and validate the engine catalog. Invalid onion addresses are dropped with a warning,
    which also protects against typo-squatted phishing mirrors copied from link lists."""
    if path is None:
        text = resources.files(__package__).joinpath("catalog.toml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    data = tomllib.loads(text)
    engines: list[Engine] = []
    names: set[str] = set()
    for raw in data.get("engine", []):
        name = str(raw["name"]).strip().lower()
        if name in names:
            raise ValueError(f"duplicate engine name in catalog: {name}")
        tier = raw.get("tier", "extended")
        if tier not in TIERS:
            raise ValueError(f"{name}: unknown tier {tier!r}")
        parser = raw.get("parser", "generic")
        if parser not in PARSERS:
            raise ValueError(f"{name}: unknown parser {parser!r}")
        prep = raw.get("prep")
        if prep and prep not in PREP_HOOKS:
            raise ValueError(f"{name}: unknown prep hook {prep!r}")
        mirrors, rejected = [], []
        for mirror in _as_list(raw.get("mirrors")):
            host = _clean_host(mirror)
            (mirrors if is_valid_v3(host) else rejected).append(host)
        for host in rejected:
            log.warning("engine %s: dropping invalid onion address %s", name, host)
        clearnet = raw.get("clearnet") or None
        if not mirrors and not clearnet:
            log.warning("engine %s: no valid endpoint, skipped", name)
            continue
        spec = EngineSpec(
            name=name,
            label=raw.get("label", name),
            tier=tier,
            parser=parser,
            mirrors=mirrors,
            search_paths=_as_list(raw["search_path"]),
            clearnet=clearnet,
            clearnet_search_paths=_as_list(raw.get("clearnet_search_path")) or None,
            per_page=int(raw.get("per_page", 20)),
            max_pages=int(raw.get("max_pages", 1)),
            weight=float(raw.get("weight", 0.6)),
            group=raw.get("group", ""),
            prep=prep,
            page_delay=float(raw.get("page_delay", 0.0)),
            max_query_len=raw.get("max_query_len"),
            notes=raw.get("notes", ""),
            rejected_mirrors=rejected,
        )
        names.add(name)
        engines.append(Engine(spec))
    return engines

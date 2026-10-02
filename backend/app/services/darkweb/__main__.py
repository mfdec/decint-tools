"""Command line for the dark-web search, through the same service layer the API uses.

    python -m app.services.darkweb search "ransomware leak" --mode tor --show-pruned
    python -m app.services.darkweb search '"leaked database" -conti' --json -
    python -m app.services.darkweb engines --mode tor --probe
    python -m app.services.darkweb tor-check
    python -m app.services.darkweb --replay tests/darkweb/fixtures/replay search bitcoin

Run from backend/ so `app` is importable. `tools/decint.py osint darkweb ...` does that for you.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

from ...config import settings
from . import aclose, aggregator_for, engine_roster, search_events, tor_verify
from .models import SearchResponse

_ICON = {"ok": "+", "empty": "o", "blocked": "!", "timeout": "~", "error": "x", "benched": "-", "skipped": "-"}


def _print_results(resp: SearchResponse, manifest: dict, show_pruned: bool) -> None:
    if resp.message:
        print(f"\n{resp.message}")
    if resp.blocked_query:
        return
    st = resp.stats
    print(
        f"\n{st.shown} results for {resp.query!r}  ({st.raw_results} raw hits from "
        f"{st.engines_with_results}/{st.engines_queried} engines, {st.merged_duplicates} duplicates "
        f"merged, {sum(st.pruned_by_reason.values())} pruned, {st.collapsed} collapsed, "
        f"{resp.took_ms / 1000:.1f}s)\n"
    )
    for i, r in enumerate(resp.results, 1):
        flags = f"  [{', '.join(r.flags)}]" if r.flags else ""
        print(f"{i:>3}. {r.title}\n     {r.url}")
        if r.snippet:
            print(f"     {r.snippet[:220]}")
        print(f"     score {r.score:.2f} · {r.corroboration} index(es): {', '.join(r.engines[:5])}{flags}")
        for kind, values in r.entities.items():
            print(f"     {kind}: {values if isinstance(values, bool) else ', '.join(values[:3])}")
        if r.mirrors:
            print(f"     mirrors/clones: {', '.join(m.url for m in r.mirrors[:3])}")
        if r.more_from_site:
            print(f"     +{len(r.more_from_site)} more from this site")
        print()
    if show_pruned and resp.pruned:
        print("Pruned:")
        for p in resp.pruned:
            print(f"  - [{p.reason}] {p.title[:70]}  {p.url}  {p.detail}")
    if st.safety_blocked:
        print(f"\n{st.safety_blocked} result(s) withheld by the child-safety filter.")
    if "sha256" in manifest:
        print(f"\nevidence sha256: {manifest['sha256']}")


def csv_safe(value: object) -> object:
    """Titles and snippets come from sites anyone can publish, and the file will be opened in a
    spreadsheet: a cell starting with = + - @ would run as a formula. A leading apostrophe makes
    spreadsheets show the text without evaluating it."""
    text = "" if value is None else value
    return "'" + text if isinstance(text, str) and text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _write_csv(resp: SearchResponse, path: str) -> None:
    fh = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")  # noqa: SIM115
    try:
        w = csv.writer(fh)
        w.writerow(["rank", "title", "url", "snippet", "score", "engines", "quality", "flags", "mirrors"])
        for i, r in enumerate(resp.results, 1):
            w.writerow(map(csv_safe, [i, r.title, r.url, r.snippet, r.score, ";".join(r.engines),
                                      r.quality, ";".join(r.flags), ";".join(m.url for m in r.mirrors)]))
    finally:
        if fh is not sys.stdout:
            fh.close()


async def _search(args: argparse.Namespace) -> int:
    quiet = "-" in (args.json, args.csv)
    final: dict = {}
    try:
        async for ev in search_events(
            args.query, args.mode, pages=args.pages, experimental=args.experimental, limit=args.limit
        ):
            if ev["type"] == "start" and not quiet:
                print(f"Searching {len(ev['engines'])} engines via {ev['transport']}...", file=sys.stderr)
            elif ev["type"] == "engine" and not quiet:
                r = ev["report"]
                lat = f"{r.latency_ms / 1000:.1f}s" if r.latency_ms is not None else ""
                why = f" - {r.error}" if r.error and r.status != "ok" else ""
                print(f"  [{_ICON.get(r.status, '?')}] {r.engine:<18} {r.status:<8} {r.results:>4} hits "
                      f"{lat:>6}{why[:90]}", file=sys.stderr)
            elif ev["type"] == "results":
                final = ev
    finally:
        await aclose()
    resp: SearchResponse = final["response"]
    if args.json:
        payload = json.dumps({"manifest": final["manifest"], **json.loads(
            resp.model_dump_json(exclude=None if args.show_pruned else {"pruned"}))}, indent=2)
        if args.json == "-":
            print(payload)
        else:
            Path(args.json).write_text(payload, encoding="utf-8")
            print(f"wrote {args.json}", file=sys.stderr)
    if args.csv:
        _write_csv(resp, args.csv)
    if not quiet:
        _print_results(resp, final["manifest"], args.show_pruned)
    return 2 if resp.blocked_query else 0


async def _engines(args: argparse.Namespace) -> int:
    try:
        if args.probe:
            agg = aggregator_for(args.mode)
            print(f"Probing {args.mode} engines with {args.query!r}...", file=sys.stderr)
            resp = await agg.probe(args.query, include_experimental=args.experimental)
            for r in resp.engines:
                lat = f"{r.latency_ms / 1000:.1f}s" if r.latency_ms is not None else "-"
                print(f"[{_ICON.get(r.status, '?')}] {r.engine:<18} {r.status:<8} {r.results:>4} hits "
                      f"{lat:>7}  {r.endpoint or ''} {(r.error or '')[:80]}")
            alive = sum(r.status == "ok" for r in resp.engines)
            print(f"\n{alive}/{len(resp.engines)} engines returned results.")
            return 0
        print(f"{'engine':<18} {'tier':<12} {'health':<22} notes")
        for e in engine_roster(args.mode):
            if e["benched"]:
                health = "benched"
            elif e["success_rate"] is None:
                health = "unknown"
            else:
                health = f"{e['success_rate'] * 100:.0f}% ok" + (f" {e['latency_s']}s" if e["latency_s"] else "")
            print(f"{e['name']:<18} {e['tier']:<12} {health:<22} {e['notes'][:60]}")
        return 0
    finally:
        await aclose()


async def _tor_check(_args: argparse.Namespace) -> int:
    result = await tor_verify()
    print(json.dumps(result, indent=2))
    return 0 if result.get("is_tor") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.services.darkweb",
                                description="Meta-search across onion search engines.")
    p.add_argument("--replay", metavar="DIR", help="serve saved engine pages from DIR instead of the network")
    sub = p.add_subparsers(dest="command", required=True)

    def mode(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--mode", choices=["gateway", "tor"], default="gateway",
                        help="gateway: clearnet gateways, no Tor (default) · tor: onion engines over Tor")
        sp.add_argument("--experimental", action="store_true", help="tor mode: also the unvetted engines")

    s = sub.add_parser("search", help="search and print ranked results")
    s.add_argument("query", help='words, "quoted phrases" and -excluded terms')
    mode(s)
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--show-pruned", action="store_true", help="list dropped results and why")
    s.add_argument("--json", metavar="FILE", help="write JSON to FILE ('-' for stdout)")
    s.add_argument("--csv", metavar="FILE", help="write CSV to FILE ('-' for stdout)")

    e = sub.add_parser("engines", help="list engines and health, or --probe them")
    mode(e)
    e.add_argument("--probe", action="store_true", help="query every engine to see which are alive")
    e.add_argument("--query", default="bitcoin")

    sub.add_parser("tor-check", help="verify that traffic exits through Tor")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.replay:
        settings.darkweb_replay_dir = str(Path(args.replay).resolve())
    started = time.monotonic()
    run = {"search": _search, "engines": _engines, "tor-check": _tor_check}[args.command]
    code = asyncio.run(run(args))
    if args.command == "search" and "-" not in (args.json, args.csv):
        print(f"\n({time.monotonic() - started:.1f}s)", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

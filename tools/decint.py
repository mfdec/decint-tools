#!/usr/bin/env python3
"""DECINT — unified tool entry point.

    python tools/decint.py <command> [args...]

Commands
    install     Install the stack (node, venv, python deps, npm)
    serve       dev | start | stop | restart | status | build | logs
    verify      all | auth | api | analytics | theme | icons | offline
    accounts    list | create-admin | create-user | set-role | set-tier |
                set-password | reset-mfa | audit
    assets      Regenerate the favicon / icon set
    snapshot    Export the website source to a folder
    osint       Run an OSINT tool directly (darkweb, sniffer; legacy: rerank, dwsearch)

Examples
    python tools/decint.py install
    python tools/decint.py serve dev
    python tools/decint.py verify all
    python tools/decint.py accounts create-admin --email you@decint.tools
    python tools/decint.py snapshot --out /mnt/c/Users/you/Desktop/decint-v3 --zip
    python tools/decint.py osint darkweb search "leaked database" --mode tor

Every command is also runnable on its own (tools/serve.py, tools/verify.py …);
this is just a single door into all of them.
"""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import BACKEND, die, head, info, venv_python  # noqa: E402

COMMANDS = {
    "install": "install.py",
    "serve": "serve.py",
    "verify": "verify.py",
    "accounts": "accounts.py",
    "assets": "assets.py",
    "snapshot": "snapshot.py",
}

# The vendored OSINT tools, runnable straight from the CLI.
OSINT = {
    "darkweb": ("-m app.services.darkweb",
                "Dark-web meta-search over onion engines (the engine behind the API)"),
    "sniffer": ("tools/decint_sniffer.py",
                "Interactive packet sniffer (needs root)"),
    # Superseded by `darkweb` above; kept until they are deleted on purpose.
    "legacy-darkweb": ("tools/decint_darkweb_search.py",
                       "LEGACY: v2 multi-source Tor search (replaced by `darkweb`)"),
    "rerank": ("tools/darknet_rerank.py",
               "LEGACY: BM25 re-ranking of ahmia results (replaced by `darkweb`)"),
    "dwsearch": ("tools/dwsearch.py",
                 "LEGACY: original dark-web keyword search (v1)"),
}


def run_osint(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "list"):
        head("vendored OSINT tools")
        for name, (path, desc) in OSINT.items():
            info(f"{name:10} {desc}")
            info(f"{'':10} " + (f"python {path} (from backend/)" if path.startswith("-m ") else str(BACKEND / path)))
        print()
        info('e.g.  python tools/decint.py osint darkweb search "leaked database" --mode tor')
        return 0
    name, *rest = argv
    if name not in OSINT:
        die(f"unknown tool {name!r} — one of: {', '.join(OSINT)}")
    target = OSINT[name][0]
    if target.startswith("-m "):  # a module inside the backend package, run from backend/
        cmd = [str(venv_python()), "-m", target[3:], *rest]
    else:
        script = BACKEND / target
        if not script.exists():
            die(f"missing {script}")
        cmd = [str(venv_python()), str(script), *rest]
    return subprocess.run(cmd, cwd=str(BACKEND)).returncode


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == "osint":
        return run_osint(rest)

    if cmd not in COMMANDS:
        print(__doc__)
        die(f"unknown command {cmd!r}")

    target = HERE / COMMANDS[cmd]
    sys.argv = [str(target), *rest]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

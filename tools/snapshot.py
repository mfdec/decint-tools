#!/usr/bin/env python3
"""Export the current website source to a folder.

    python tools/snapshot.py                    # -> ../decint-v3-snapshot
    python tools/snapshot.py --out /mnt/c/Users/you/Desktop/decint-v3
    python tools/snapshot.py --zip              # also produce a .zip

Copies everything that *is* the website — backend, frontend, tools, deploy
config, scripts, assets — and deliberately omits:

  * build artifacts (.venv, node_modules, .next, __pycache__)  ~520 MB
  * the analytics database (contains visitor IPs — personal data)
  * GeoLite2 .mmdb files (large, and MaxMind-licensed)
  * .env (secrets). `.env.example` is copied instead.
  * .git

The result is a clean, portable copy you can zip, archive, or hand to someone
without leaking credentials or visitor data.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, head, info, ok, warn  # noqa: E402

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", ".next", "out",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "data",            # analytics DB + geoip
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".mmdb", ".db", ".sqlite3", ".log", ".pid"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", "next-env.d.ts"}


def keep(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    if path.suffix in EXCLUDE_SUFFIX:
        return False
    # .env holds live secrets; .env.example is the template and is kept.
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return False
    if path.name.endswith("Zone.Identifier"):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the website source")
    ap.add_argument("--out", default=str(ROOT.parent / "decint-v3-snapshot"))
    ap.add_argument("--zip", action="store_true", help="also write a .zip archive")
    ap.add_argument("--force", action="store_true", help="overwrite an existing folder")
    a = ap.parse_args()

    dest = Path(a.out).expanduser().resolve()
    head(f"exporting {ROOT.name} -> {dest}")

    if dest.exists():
        if not a.force and any(dest.iterdir()):
            warn(f"{dest} exists and is not empty — re-run with --force to replace")
            return 1
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    copied = 0
    total_bytes = 0
    pruned: set[str] = set()

    # os.walk with in-place pruning of `dirs`, rather than rglob + filter.
    # rglob descends into node_modules and .venv before discarding them, which
    # is both slow and made the "omitted" summary list every nested package.
    for dirpath, dirs, files in os.walk(ROOT):
        here = Path(dirpath)
        drop = [d for d in dirs if d in EXCLUDE_DIRS]
        for d in drop:
            pruned.add(str((here / d).relative_to(ROOT)))
            dirs.remove(d)          # prevents os.walk from descending
        for name in files:
            src = here / name
            if not keep(src, ROOT):
                continue
            rel = src.relative_to(ROOT)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied += 1
            total_bytes += src.stat().st_size

    ok(f"{copied} files, {total_bytes / 1024:.0f} KB")
    if pruned:
        info("omitted: " + ", ".join(sorted(pruned)))
    info("omitted secrets: backend/.env (kept .env.example)")

    # A short note so the folder explains itself later.
    (dest / "SNAPSHOT.md").write_text(
        "# DECINT v3 — source snapshot\n\n"
        "Complete website source. Build artifacts, the analytics database, the\n"
        "GeoLite2 files and `.env` are intentionally absent.\n\n"
        "## Run it\n\n"
        "```bash\n"
        "python tools/decint.py install\n"
        "cp backend/.env.example backend/.env   # then edit\n"
        "python tools/decint.py serve dev\n"
        "```\n\n"
        "Open http://localhost:3000. See `tools/README.md` for the full tool list\n"
        "and `deploy/README.md` for the server runbook.\n",
        encoding="utf-8",
    )

    if a.zip:
        archive = shutil.make_archive(str(dest), "zip", root_dir=dest)
        ok(f"archive: {archive} ({Path(archive).stat().st_size / 1024 / 1024:.1f} MB)")

    print()
    ok(f"snapshot ready: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

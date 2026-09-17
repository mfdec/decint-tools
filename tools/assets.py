#!/usr/bin/env python3
"""Regenerate the brand assets (favicon / icon set / SVG mark path).

    python tools/assets.py                 # regenerate into frontend/public
    python tools/assets.py --out /tmp/ico  # somewhere else, to preview first
    python tools/assets.py --show          # print the current palette only

The geometry and colours live in scripts/make_favicon.py — edit GRAD_TL,
GRAD_BR, OUTLINE or TOP_RISE there, then run this. It also prints the SVG path
for components/icons.tsx so the in-app mark and the favicon stay the same
shape; if you change the geometry, paste that path into the Shield component.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FRONTEND, ROOT, die, head, info, ok, run, venv_python  # noqa: E402

MAKER = ROOT / "scripts" / "make_favicon.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate DECINT brand assets")
    ap.add_argument("--out", default=str(FRONTEND / "public"))
    ap.add_argument("--show", action="store_true", help="print the palette and exit")
    a = ap.parse_args()

    if not MAKER.exists():
        die(f"missing {MAKER}")

    if a.show:
        head("current palette")
        src = MAKER.read_text(encoding="utf-8")
        for line in src.splitlines():
            s = line.strip()
            if s.startswith(("GRAD_", "ACCENT", "OUTLINE", "TOP_RISE", "SHOULDER", "BELLY")):
                info(s)
        return 0

    head("regenerating icon set")
    r = run([str(venv_python()), str(MAKER), a.out], check=False)
    if r.returncode != 0:
        die("generation failed — is Pillow installed? (tools/decint.py install --backend)")
    print()
    ok(f"assets written to {a.out}")
    info("If you changed the geometry, paste the SVG path above into")
    info("frontend/components/icons.tsx so the in-app mark matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

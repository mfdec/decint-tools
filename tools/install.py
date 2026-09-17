#!/usr/bin/env python3
"""Install the DECINT stack. Idempotent — safe to re-run.

    python tools/install.py            # everything
    python tools/install.py --backend  # just the venv + python deps
    python tools/install.py --frontend # just npm install
    python tools/install.py --node     # just Node LTS via nvm
    python tools/install.py --system   # apt packages + Tor (needs sudo)

Replaces deploy/install*.sh.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    BACKEND, FRONTEND, ROOT, die, have, head, info, node_bin, ok, out, run, warn,
)

APT_PACKAGES = [
    "python3-venv", "python3-pip", "tor", "curl", "build-essential", "libpcap0.8",
]
NVM_VERSION = "v0.40.1"


def system() -> None:
    head("system packages")
    if not have("apt"):
        warn("apt not found — skipping (install python3-venv, tor, libpcap manually)")
        return
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    run(sudo + ["apt", "update"], check=False)
    run(sudo + ["apt", "install", "-y", *APT_PACKAGES], check=False)
    run(sudo + ["systemctl", "enable", "--now", "tor"], check=False)
    state = out(["systemctl", "is-active", "tor"])
    (ok if state == "active" else warn)(f"tor: {state or 'unknown'}")


def node() -> None:
    head("Node LTS")
    if node_bin("npm"):
        ok(f"node {out([node_bin('node') or 'node', '--version'])} already installed")
        return
    nvm_dir = Path.home() / ".nvm"
    if not (nvm_dir / "nvm.sh").exists():
        info("installing nvm")
        script = Path("/tmp/nvm-install.sh")
        url = f"https://raw.githubusercontent.com/nvm-sh/nvm/{NVM_VERSION}/install.sh"
        urllib.request.urlretrieve(url, script)
        run(["bash", str(script)], check=False)
    # nvm is a shell function, so this one genuinely needs bash — but it's a
    # fixed string with no interpolation, which is what made the old scripts
    # fragile.
    run(["bash", "-c",
         'export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; '
         'nvm install --lts && nvm alias default "lts/*"'], check=False)
    npm = node_bin("npm")
    (ok if npm else die)(f"npm at {npm}" if npm else "node install failed")


def backend() -> None:
    head("backend venv + python deps")
    venv = BACKEND / ".venv"
    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)])
    py = venv / "bin" / "python"
    if not py.exists():
        py = venv / "Scripts" / "python.exe"
    run([str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"], check=False)
    run([str(py), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")])

    env = BACKEND / ".env"
    if not env.exists():
        shutil.copy(BACKEND / ".env.example", env)
        env.chmod(0o600)
        ok("seeded backend/.env from .env.example")
    else:
        info("backend/.env already present — left alone")
    ok(f"python {out([str(py), '--version'])}")


def frontend() -> None:
    head("frontend deps")
    npm = node_bin("npm")
    if not npm:
        die("npm not found — run with --node first")
    run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND)
    ok("node_modules ready")


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the DECINT stack")
    ap.add_argument("--system", action="store_true", help="apt packages + Tor (sudo)")
    ap.add_argument("--node", action="store_true")
    ap.add_argument("--backend", action="store_true")
    ap.add_argument("--frontend", action="store_true")
    a = ap.parse_args()

    selected = any([a.system, a.node, a.backend, a.frontend])
    if not selected:
        # Default run skips --system: it needs sudo and most people already
        # have these packages. Ask for it explicitly.
        node(); backend(); frontend()
        print()
        info("system packages skipped — add --system if Tor or libpcap are missing")
    else:
        if a.system:
            system()
        if a.node:
            node()
        if a.backend:
            backend()
        if a.frontend:
            frontend()

    print()
    ok("install complete —  python tools/decint.py serve dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared helpers for the DECINT tool suite.

These tools replace the deploy/*.sh scripts. The motivation is not style: the
shell versions kept breaking on quoting once they were invoked through
`wsl -d Ubuntu -- bash -lc '...'`, because each layer ate a level of escaping.
Python subprocess calls take an argument *list*, so nothing is re-parsed by a
shell and the same command works identically from Windows, WSL, or the server.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# ─────────────────────────── output ───────────────────────────

_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def head(msg: str) -> None:
    print(_c("1;36", f"\n== {msg} =="))


def info(msg: str) -> None:
    print(f"   {msg}")


def ok(msg: str) -> None:
    print(f"   {_c('32', 'ok')}    {msg}")


def warn(msg: str) -> None:
    print(f"   {_c('33', 'warn')}  {msg}")


def err(msg: str) -> None:
    print(f"   {_c('31', 'FAIL')}  {msg}")


def die(msg: str, code: int = 1):
    print(_c("31", f"\n{msg}"))
    raise SystemExit(code)


# ─────────────────────────── process ───────────────────────────

def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    check: bool = True,
    capture: bool = False,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command from an argument list — never through a shell."""
    if not quiet:
        info(_c("90", "$ " + " ".join(str(a) for a in args)))
    e = {**os.environ, **(env or {})}
    return subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        env=e,
        check=check,
        text=True,
        capture_output=capture,
    )


def out(args: list[str], cwd: Path | None = None) -> str:
    """Run and return stdout, empty string on failure."""
    try:
        r = run(args, cwd=cwd, check=False, capture=True, quiet=True)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ─────────────────────────── toolchain paths ───────────────────────────

def venv_python() -> Path:
    p = BACKEND / ".venv" / "bin" / "python"
    if p.exists():
        return p
    p_win = BACKEND / ".venv" / "Scripts" / "python.exe"
    if p_win.exists():
        return p_win
    return Path(sys.executable)


def venv_ready() -> bool:
    return (BACKEND / ".venv").is_dir()


def node_bin(name: str) -> str | None:
    """Find node/npm, including one installed by nvm (which is not on PATH for
    a non-login shell)."""
    if have(name):
        return name
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        versions = sorted(
            (d for d in nvm.iterdir() if d.is_dir()),
            key=lambda d: [int(x) for x in d.name.lstrip("v").split(".") if x.isdigit()] or [0],
            reverse=True,
        )
        for v in versions:
            cand = v / "bin" / name
            if cand.exists():
                return str(cand)
    return None


def require_node() -> str:
    npm = node_bin("npm")
    if not npm:
        die("npm not found. Run:  python tools/decint.py install --node")
    return npm


def node_env(extra: dict | None = None) -> dict:
    """Environment for running npm.

    npm is a Node script whose shebang is `#!/usr/bin/env node`, so invoking it
    by absolute path is not enough — `node` itself must be resolvable on PATH.
    An nvm install is only on PATH inside an interactive login shell, so a
    detached subprocess launched from here would fail with
    "env: 'node': No such file or directory". Prepending node's own bin
    directory fixes it without needing a shell at all.
    """
    env = {**os.environ, **(extra or {})}
    npm = node_bin("npm")
    if npm and os.path.isabs(npm):
        bindir = str(Path(npm).parent)
        if bindir not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


# ─────────────────────────── services ───────────────────────────

API_PID = Path("/tmp/decint_api.pid")
WEB_PID = Path("/tmp/decint_web.pid")
API_LOG = Path("/tmp/decint_api.log")
WEB_LOG = Path("/tmp/decint_web.log")

API_URL = "http://127.0.0.1:8000"
WEB_URL = "http://127.0.0.1:3000"


def http(url: str, timeout: float = 3.0):
    """Tiny GET returning (status, body). Uses stdlib so the tools work before
    the venv exists."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception:
        return 0, ""


def wait_for(url: str, seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http(url, timeout=2.0)[0]:
            return True
        time.sleep(0.5)
    return False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text().strip())
        return pid if pid_alive(pid) else None
    except (OSError, ValueError):
        return None

"""Locate a Python interpreter to build with, and make sure it has PyInstaller.

When the maker itself runs as a frozen EXE, ``sys.executable`` is the maker —
useless for building — so we look for a real interpreter on the machine. The
build interpreter must also have the *target app's* third-party packages
installed, which :func:`missing_modules` / :func:`pip_install` take care of.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

Log = Callable[[str], None]
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=_CREATE_NO_WINDOW)


def candidate_pythons(preferred: str = "") -> list[str]:
    seen, out = set(), []

    def add(p):
        if p and p not in seen and Path(p).exists():
            seen.add(p)
            out.append(p)

    add(preferred)
    if not getattr(sys, "frozen", False):
        add(sys.executable)
    if sys.platform == "win32":
        try:
            r = _run(["py", "-3", "-c", "import sys;print(sys.executable)"], timeout=20)
            if r.returncode == 0:
                add(r.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
        for name in ("python", "python3"):
            add(shutil.which(name))
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
        if base.is_dir():
            for d in sorted(base.glob("Python3*"), reverse=True):
                add(str(d / "python.exe"))
        for d in sorted(Path("C:/").glob("Python3*"), reverse=True):
            add(str(d / "python.exe"))
    else:
        for name in ("python3", "python"):
            add(shutil.which(name))
    # Windows Store alias stubs are not real interpreters.
    return [p for p in out if "WindowsApps" not in p]


def python_info(exe: str) -> dict:
    """``{"ok", "version", "pyinstaller", "exe"}`` — cheap probe of an interpreter."""
    info = {"exe": exe, "ok": False, "version": "", "pyinstaller": ""}
    try:
        r = _run([exe, "-c", "import sys,platform;print(platform.python_version());"
                             "\ntry:\n import PyInstaller;print(PyInstaller.__version__)\n"
                             "except Exception:\n print('')"], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return info
    if r.returncode != 0:
        return info
    lines = r.stdout.strip().splitlines() + ["", ""]
    info.update(ok=True, version=lines[0].strip(), pyinstaller=lines[1].strip())
    return info


def find_python(preferred: str = "") -> str | None:
    best = None
    for p in candidate_pythons(preferred):
        i = python_info(p)
        if not i["ok"]:
            continue
        if i["pyinstaller"]:
            return p
        best = best or p
    return best


def ensure_pyinstaller(python: str, log: Log) -> bool:
    if python_info(python)["pyinstaller"]:
        return True
    log(f"Installing PyInstaller into {python} …")
    return pip_install(python, ["pyinstaller", "pillow"], log)


def pip_install(python: str, packages: list[str], log: Log) -> bool:
    if not packages:
        return True
    cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", *packages]
    log("$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                creationflags=_CREATE_NO_WINDOW)
        for line in proc.stdout:  # type: ignore[union-attr]
            log(line.rstrip())
        return proc.wait() == 0
    except OSError as ex:
        log(f"pip failed: {ex}")
        return False


def missing_modules(python: str, import_names: list[str]) -> list[str]:
    """Which of these top-level imports the build interpreter cannot import."""
    if not import_names:
        return []
    script = ("import importlib.util,sys\n"
              "for n in sys.argv[1:]:\n"
              "  print(n, bool(importlib.util.find_spec(n.split('.')[0])))")
    try:
        r = _run([python, "-c", script, *import_names], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return list(import_names)
    missing = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "False":
            missing.append(parts[0])
    return missing

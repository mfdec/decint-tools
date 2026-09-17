#!/usr/bin/env python3
"""Build DECINT EXE Maker itself into a standalone executable.

    python build_maker.py            # GUI exe:  dist/DECINT-EXE-Maker(.exe)
    python build_maker.py --cli      # also the console variant: dist/decint-exe-maker-cli(.exe)

Run this ON WINDOWS to get .exe files (PyInstaller does not cross-compile).
The runtime/ and assets/ folders are bundled as data so the frozen maker can
still stage them into the apps it builds.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE / "decint_exe_maker"
SEP = os.pathsep


def ensure(mod: str, pip: str) -> None:
    try:
        __import__(mod)
    except ImportError:
        print(f"installing {pip} …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", pip])


def build(name: str, windowed: bool) -> Path:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--name", name, "--windowed" if windowed else "--console",
           "--distpath", str(HERE / "dist"), "--workpath", str(HERE / "build"), "--specpath", str(HERE / "build"),
           "--paths", str(HERE),
           "--add-data", f"{PKG / 'runtime'}{SEP}decint_exe_maker/runtime",
           "--add-data", f"{PKG / 'assets'}{SEP}decint_exe_maker/assets",
           "--hidden-import", "decint_exe_maker.gui", "--hidden-import", "decint_exe_maker.cli",
           "--collect-submodules", "decint_exe_maker"]
    if sys.platform != "darwin":
        cmd += ["--icon", str(PKG / "assets" / "decint.ico")]
    if sys.platform == "win32":
        from decint_exe_maker import __version__, branding
        vf = HERE / "build" / "maker_version_info.txt"
        vf.parent.mkdir(exist_ok=True)
        vf.write_text(branding.version_info_text("DECINT EXE Maker", __version__,
                                                 "Wrap Python apps in DECINT licensing and build EXEs",
                                                 exe_name=name), encoding="utf-8")
        cmd += ["--version-file", str(vf)]
    cmd.append(str(HERE / "decint_exe_maker.py"))
    print("$", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(HERE))
    exe = HERE / "dist" / (name + (".exe" if sys.platform == "win32" else ""))
    print(f"\nbuilt {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
    return exe


def main() -> int:
    sys.path.insert(0, str(HERE))
    ensure("PyInstaller", "pyinstaller")
    ensure("PIL", "pillow")
    build("DECINT-EXE-Maker", windowed=True)
    if "--cli" in sys.argv:
        build("decint-exe-maker-cli", windowed=False)
    shutil.rmtree(HERE / "build", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

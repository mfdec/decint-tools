"""Stage a project + the DECINT runtime and run PyInstaller on it.

Staging layout (in a fresh temp dir, so the user's project is never touched)::

    <staging>/
      decint_launcher.py     the EXE's real entry point (from runtime/launcher_template.py)
      decint_rt/             rsa_lite.py, licensing.py, decint.ico   (shipped inside the EXE)
      app/                   a copy of the project, minus junk dirs
      version_info.txt, icon.ico, splash.png

PyInstaller output goes to ``spec.output_dir``; the work dir stays in staging.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from . import analyzer, branding, toolchain, vendor
from .runtime import rsa_lite

Log = Callable[[str], None]
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class BuildSpec:
    project_dir: str
    entry_file: str                       # relative to project_dir
    product_name: str
    product_id: str = ""
    version: str = "1.0.0"
    description: str = ""
    app_type: str = "gui"                  # gui | console
    onefile: bool = True
    icon_path: str = ""                    # empty = DECINT shield
    splash: bool = True
    hidden_imports: list[str] = field(default_factory=list)
    collect_all: list[str] = field(default_factory=list)
    exclude_modules: list[str] = field(default_factory=list)
    data_files: list[str] = field(default_factory=list)   # project-relative
    chdir_to_bundle: bool = False
    entry_callable: str = ""
    install_missing: bool = True
    pip_requirements: list[str] = field(default_factory=list)
    third_party_imports: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)
    # licensing
    licensing: bool = True
    bind_machine: bool = False
    trial_days: int = 0
    vendor_contact: str = ""
    activation_ui: str = "auto"            # auto | tk | console
    # environment
    output_dir: str = ""
    python: str = ""
    company: str = "DECINT"
    copyright_holder: str = "DECINT"

    def exe_name(self) -> str:
        keep = "".join(ch for ch in self.product_name if ch.isalnum() or ch in " -_.")
        return keep.strip().replace(" ", "-") or "app"

    def as_public_dict(self) -> dict:
        d = asdict(self)
        d.pop("python", None)
        return d


@dataclass
class BuildResult:
    ok: bool
    exe_path: str = ""
    staging: str = ""
    log_path: str = ""
    error: str = ""
    seconds: float = 0.0


class BuildCancelled(Exception):
    pass


# ─────────────────────────────── staging ───────────────────────────────

def _copy_project(src: Path, dest: Path, log: Log) -> int:
    n = 0
    for p in analyzer.iter_files(src):
        rel = p.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        n += 1
    log(f"Copied {n} project files to staging.")
    return n


def _copy_runtime(staging: Path, icon: Path) -> Path:
    rt_src = branding.resource_dir() / "runtime"
    rt = staging / "decint_rt"
    rt.mkdir()
    for name in ("__init__.py", "rsa_lite.py", "licensing.py"):
        shutil.copy2(rt_src / name, rt / name)
    shutil.copy2(icon, rt / "decint.ico")
    return rt


def _launcher_config(spec: BuildSpec, entry_module: str, key: dict | None, ui: str) -> dict:
    lic = {"enabled": bool(spec.licensing and key)}
    if key:
        lic.update({"public_n": str(key["n"]), "public_e": int(key["e"]),
                    "bind_machine": bool(spec.bind_machine), "trial_days": int(spec.trial_days or 0),
                    "ui": ui, "vendor_contact": spec.vendor_contact.strip(),
                    "fingerprint": rsa_lite.fingerprint(key["n"], key["e"])})
    return {"product_id": spec.product_id, "product_name": spec.product_name, "version": spec.version,
            "entry_module": entry_module, "entry_callable": spec.entry_callable.strip() or None,
            "chdir_to_bundle": bool(spec.chdir_to_bundle), "licensing": lic,
            "built_with": "DECINT EXE Maker", "built_at": int(time.time())}


def _write_launcher(staging: Path, config: dict) -> Path:
    template = (branding.resource_dir() / "runtime" / "launcher_template.py").read_text(encoding="utf-8")
    cfg = json.dumps(config, indent=1).replace('"""', '\\"\\"\\"')
    p = staging / "decint_launcher.py"
    p.write_text(template.replace("__DECINT_CONFIG_JSON__", cfg), encoding="utf-8")
    return p


def _customer_readme(spec: BuildSpec, config: dict) -> str:
    lic = config["licensing"]
    lines = [f"{spec.product_name} {spec.version} — built with DECINT EXE Maker", ""]
    if lic.get("enabled"):
        lines += [
            "LICENSING",
            f"  This program needs a DECINT license key for product '{spec.product_id}'.",
            "  On first start it shows an activation screen: paste the key you received.",
            "  You can also place the key in a file named 'license.lic' (or",
            f"  '{spec.product_id}.lic') next to the executable, or set DECINT_LICENSE.",
            f"  Keys are time-limited; you will be warned {14} days before expiry.",
        ]
        if lic.get("bind_machine"):
            lines.append("  Keys are bound to one machine — send the Machine ID shown on the")
            lines.append("  activation screen to your vendor when purchasing.")
        if lic.get("trial_days"):
            lines.append(f"  A {lic['trial_days']}-day trial runs without a key.")
        if spec.vendor_contact:
            lines.append(f"  Vendor contact: {spec.vendor_contact}")
        lines.append(f"  Vendor key fingerprint: {lic.get('fingerprint')}")
    else:
        lines.append("This build has licensing disabled.")
    return "\n".join(lines) + "\n"


# ─────────────────────────────── command ───────────────────────────────

def pyinstaller_command(spec: BuildSpec, staging: Path, app_dir: Path, entry_module: str,
                        extra_paths: list[str], windowed: bool, excludes: list[str]) -> list[str]:
    sep = os.pathsep
    out = Path(spec.output_dir)
    cmd = [spec.python, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "INFO",
           "--name", spec.exe_name(), "--onefile" if spec.onefile else "--onedir",
           "--windowed" if windowed else "--console",
           "--distpath", str(out), "--workpath", str(staging / "build"), "--specpath", str(staging),
           "--paths", str(app_dir)]
    for p in extra_paths:
        cmd += ["--paths", p]
    icon = staging / "icon.ico"
    if icon.is_file() and sys.platform != "darwin":
        cmd += ["--icon", str(icon)]
    if sys.platform == "win32":
        cmd += ["--version-file", str(staging / "version_info.txt")]
    cmd += ["--hidden-import", entry_module]
    for h in dict.fromkeys(spec.hidden_imports):
        if h.strip():
            cmd += ["--hidden-import", h.strip()]
    for c in dict.fromkeys(spec.collect_all):
        if c.strip():
            cmd += ["--collect-all", c.strip()]
    for x in dict.fromkeys(excludes):
        if x.strip():
            cmd += ["--exclude-module", x.strip()]
    cmd += ["--add-data", f"{staging / 'decint_rt' / 'decint.ico'}{sep}decint_rt"]
    for rel in dict.fromkeys(spec.data_files):
        rel = rel.strip().replace("\\", "/").strip("/")
        if not rel:
            continue
        src = app_dir / rel
        if not src.exists():
            continue
        dest = rel if src.is_dir() else (str(Path(rel).parent) if Path(rel).parent != Path(".") else ".")
        cmd += ["--add-data", f"{src}{sep}{dest}"]
    splash = staging / "splash.png"
    if spec.splash and windowed and splash.is_file() and sys.platform != "darwin":
        cmd += ["--splash", str(splash)]
    cmd += [a for a in spec.extra_args if a.strip()]
    cmd.append(str(staging / "decint_launcher.py"))
    return cmd


# ─────────────────────────────── build ───────────────────────────────

def build(spec: BuildSpec, log: Log, cancel: threading.Event | None = None) -> BuildResult:
    t0 = time.time()
    cancel = cancel or threading.Event()
    project = Path(spec.project_dir).resolve()
    entry_rel = spec.entry_file.replace("\\", "/")
    if not (project / entry_rel).is_file():
        return BuildResult(False, error=f"Entry file not found: {entry_rel}")
    spec.product_id = spec.product_id or vendor.slugify(spec.product_name)
    spec.output_dir = spec.output_dir or str(project.parent / f"{spec.exe_name()}-dist")
    Path(spec.output_dir).mkdir(parents=True, exist_ok=True)

    # 1. interpreter
    python = spec.python or toolchain.find_python()
    if not python:
        return BuildResult(False, error="No Python interpreter found to build with (Settings → Build Python).")
    spec.python = python
    info = toolchain.python_info(python)
    log(f"Build interpreter: {python} (Python {info['version']})")
    if not toolchain.ensure_pyinstaller(python, log):
        return BuildResult(False, error="PyInstaller is not installed in the build interpreter and could not be installed.")
    log(f"PyInstaller {toolchain.python_info(python)['pyinstaller']}")

    # 2. dependencies of the target app
    missing = toolchain.missing_modules(python, spec.third_party_imports)
    if missing:
        pkgs = sorted({analyzer.pip_name(m) for m in missing})
        if spec.install_missing:
            log(f"Installing missing packages into the build interpreter: {', '.join(pkgs)}")
            if not toolchain.pip_install(python, pkgs, log):
                log("WARNING: some packages failed to install; the build may be incomplete.")
        else:
            log(f"WARNING: not importable in the build interpreter: {', '.join(missing)} "
                f"(pip install {' '.join(pkgs)})")
    if cancel.is_set():
        raise BuildCancelled()

    # 3. staging
    staging = Path(tempfile.mkdtemp(prefix=f"decint-build-{spec.product_id}-"))
    log(f"Staging: {staging}")
    app_dir = staging / "app"
    app_dir.mkdir()
    _copy_project(project, app_dir, log)

    icon_src = Path(spec.icon_path) if spec.icon_path and Path(spec.icon_path).is_file() else branding.default_icon()
    shutil.copy2(icon_src, staging / "icon.ico")
    _copy_runtime(staging, staging / "icon.ico")
    (staging / "version_info.txt").write_text(
        branding.version_info_text(spec.product_name, spec.version, spec.description, spec.company,
                                   spec.copyright_holder, spec.exe_name()), encoding="utf-8")
    if spec.splash:
        try:
            branding.write_splash(staging / "splash.png", spec.product_name)
        except Exception as ex:  # Pillow missing or font trouble → ship the stock splash
            log(f"(splash: {ex}; using stock image)")
            if branding.default_splash().is_file():
                shutil.copy2(branding.default_splash(), staging / "splash.png")

    entry_module, extra_paths = analyzer.entry_module_name(app_dir, entry_rel)
    windowed = spec.app_type == "gui"
    ui = spec.activation_ui if spec.activation_ui in ("tk", "console") else ("tk" if windowed else "console")
    if windowed and ui == "console":
        ui = "tk"   # a windowed EXE has no console to prompt in

    key = None
    if spec.licensing:
        key = vendor.load_or_create_key(spec.product_id, spec.product_name)
        log(f"Product key for '{spec.product_id}': fingerprint {rsa_lite.fingerprint(key['n'], key['e'])} "
            f"(private key stays in {vendor.key_path(spec.product_id)})")
    config = _launcher_config(spec, entry_module, key, ui)
    _write_launcher(staging, config)

    excludes = list(spec.exclude_modules)
    uses_tk = any(m in ("tkinter", "customtkinter", "ttkbootstrap", "tkinterdnd2") for m in
                  (spec.third_party_imports + spec.collect_all)) or spec.app_type == "gui"
    if ui == "console" and not uses_tk and not (spec.splash and windowed):
        excludes += ["tkinter", "_tkinter"]   # keeps console builds ~10 MB smaller

    # 4. PyInstaller
    cmd = pyinstaller_command(spec, staging, app_dir, entry_module, extra_paths, windowed, excludes)
    log("$ " + " ".join(_q(c) for c in cmd))
    log_path = staging / "pyinstaller.log"
    with log_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", cwd=str(staging),
                                creationflags=_CREATE_NO_WINDOW)
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            fh.write(line + "\n")
            if not line.startswith(("DEBUG", " ")):
                log(line)
            if cancel.is_set():
                proc.kill()
                raise BuildCancelled()
        rc = proc.wait()
    if rc != 0:
        return BuildResult(False, staging=str(staging), log_path=str(log_path),
                           error=f"PyInstaller exited with code {rc}", seconds=time.time() - t0)

    # 5. result
    name = spec.exe_name()
    out = Path(spec.output_dir)
    if spec.onefile:
        exe = out / (name + (".exe" if sys.platform == "win32" else ""))
    else:
        exe = out / name / (name + (".exe" if sys.platform == "win32" else ""))
    if not exe.exists():
        return BuildResult(False, staging=str(staging), log_path=str(log_path),
                           error=f"Build reported success but {exe} is missing", seconds=time.time() - t0)
    (out / f"{name}-README.txt").write_text(_customer_readme(spec, config), encoding="utf-8")
    meta = {"spec": spec.as_public_dict(), "config": {k: v for k, v in config.items() if k != "licensing"},
            "licensing": {k: v for k, v in config["licensing"].items() if k != "public_n"},
            "exe": str(exe), "size": exe.stat().st_size}
    (out / f"{name}.decint-build.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    shutil.rmtree(staging / "build", ignore_errors=True)
    log(f"Built {exe} ({exe.stat().st_size / 1e6:.1f} MB) in {time.time() - t0:.0f}s")
    return BuildResult(True, exe_path=str(exe), staging=str(staging), log_path=str(log_path),
                       seconds=time.time() - t0)


def _q(s: str) -> str:
    return f'"{s}"' if " " in s else s

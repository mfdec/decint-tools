"""Slow: actually builds examples/hello_console with PyInstaller and runs it.

On Linux this produces an ELF binary rather than an .exe, but it exercises the
exact same launcher, license lookup, activation prompt and runpy handoff.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from decint_exe_maker import builder, toolchain, vendor
from decint_exe_maker.runtime import rsa_lite

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PY = os.environ.get("DECINT_TEST_PYTHON") or toolchain.find_python()

pytestmark = pytest.mark.skipif(
    not PY or not toolchain.python_info(PY)["pyinstaller"],
    reason="PyInstaller not available (set DECINT_TEST_PYTHON to an interpreter that has it)")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    cfg = tmp_path_factory.mktemp("cfg")
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
    os.environ["APPDATA"] = os.environ["LOCALAPPDATA"] = str(cfg)
    # small key to keep the test fast
    orig = rsa_lite.generate_keypair
    rsa_lite.generate_keypair = lambda bits=2048, e=65537: orig(1024)
    try:
        spec = builder.BuildSpec(project_dir=str(EXAMPLES / "hello_console"), entry_file="app.py",
                                 product_name="Hello Console", version="0.9.1", app_type="console",
                                 splash=False, licensing=True, trial_days=0, vendor_contact="sales@example.com",
                                 output_dir=str(out), python=PY, install_missing=False)
        lines = []
        res = builder.build(spec, lines.append)
    finally:
        rsa_lite.generate_keypair = orig
    assert res.ok, "\n".join(lines[-40:]) + f"\n{res.error}"
    return spec, res, cfg


@pytest.fixture(autouse=True)
def _same_config_as_build(isolated_config, built, monkeypatch):
    """The conftest fixture isolates config per test; point it back at the
    directory the module-scoped build wrote the product key into."""
    cfg = built[2]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("APPDATA", str(cfg))
    monkeypatch.setenv("LOCALAPPDATA", str(cfg))


def _run(exe, env_extra=None, stdin=""):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([exe], input=stdin, capture_output=True, text=True, timeout=120, env=env)


def test_exe_exists_with_sidecars(built):
    spec, res, _ = built
    exe = Path(res.exe_path)
    assert exe.is_file() and exe.stat().st_size > 1_000_000
    assert (exe.parent / "Hello-Console-README.txt").is_file()
    assert (exe.parent / "Hello-Console.decint-build.json").is_file()


def test_unlicensed_run_prompts_and_exits(built):
    spec, res, _ = built
    r = _run(res.exe_path, stdin="\n")
    assert r.returncode == 3
    assert "Machine ID" in r.stdout and "sales@example.com" in r.stdout
    assert "hello_console running" not in r.stdout


def test_bad_key_is_refused(built):
    spec, res, _ = built
    r = _run(res.exe_path, stdin="DECINT1.bogus.bogus\n\n")
    assert r.returncode == 3
    assert "not a valid DECINT license" in r.stdout


def test_licensed_run_executes_app(built):
    spec, res, _ = built
    key, payload = vendor.issue_license(spec.product_id, customer="Test Buyer", months=6, features=["pro"])
    r = _run(res.exe_path, env_extra={"DECINT_LICENSE": key})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "hello_console running" in r.stdout
    assert "license mode: licensed" in r.stdout
    assert "customer   : Test Buyer" in r.stdout
    assert "features   : pro" in r.stdout
    assert "helper says: OK!" in r.stdout


def test_expired_key_is_refused(built):
    import time
    spec, res, _ = built
    key, _ = vendor.issue_license(spec.product_id, customer="Late", expires=time.time() - 60)
    r = _run(res.exe_path, env_extra={"DECINT_LICENSE": key}, stdin="\n")
    assert r.returncode == 3
    assert "expired" in r.stdout


def test_activation_via_stdin_saves_license(built):
    spec, res, cfg = built
    key, _ = vendor.issue_license(spec.product_id, customer="Stdin Buyer", months=1)
    r = _run(res.exe_path, stdin=key + "\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "hello_console running" in r.stdout
    # second run needs no key at all — it was saved to the per-user store
    r2 = _run(res.exe_path)
    assert r2.returncode == 0 and "Stdin Buyer" in r2.stdout

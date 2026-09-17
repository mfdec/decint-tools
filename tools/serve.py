#!/usr/bin/env python3
"""Run the DECINT services.

    python tools/serve.py dev       # both in the foreground, Ctrl-C stops
    python tools/serve.py start     # both detached
    python tools/serve.py stop
    python tools/serve.py restart
    python tools/serve.py status
    python tools/serve.py logs [api|web] [-n 40]

Replaces deploy/dev.sh, start-bg.sh, stop-bg.sh, status.sh, build-frontend.sh.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    API_LOG, API_PID, API_URL, BACKEND, FRONTEND, ROOT, WEB_LOG, WEB_PID, WEB_URL,
    die, head, http, info, node_bin, node_env, ok, out, read_pid, run,
    venv_python, venv_ready, wait_for, warn,
)


def _uvicorn_cmd() -> list[str]:
    return [str(venv_python()), "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", "8000"]


def _next_cmd(dev: bool) -> list[str]:
    npm = node_bin("npm")
    if not npm:
        die("npm not found — python tools/decint.py install --node")
    return [npm, "run", "dev" if dev else "start"]


def _preflight() -> None:
    if not venv_ready():
        die("backend venv missing — python tools/decint.py install --backend")
    if not (FRONTEND / "node_modules").is_dir():
        die("node_modules missing — python tools/decint.py install --frontend")


def build() -> None:
    head("building frontend")
    npm = node_bin("npm")
    if not npm:
        die("npm not found")
    run([npm, "run", "build"], cwd=FRONTEND)
    ok("build complete")


def dev() -> int:
    _preflight()
    head("starting DECINT (foreground)")
    if out(["systemctl", "is-active", "tor"]) != "active":
        warn("Tor is not active — dark-web 'tor' mode will fail (sudo systemctl start tor)")

    api = subprocess.Popen(_uvicorn_cmd(), cwd=BACKEND)
    web = subprocess.Popen(_next_cmd(dev=True), cwd=FRONTEND,
                           env=node_env({"BACKEND_URL": API_URL}))
    ok(f"api  {API_URL}")
    ok(f"web  http://localhost:3000")
    info("Ctrl-C to stop both")
    try:
        while True:
            if api.poll() is not None or web.poll() is not None:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (web, api):
            if p.poll() is None:
                p.terminate()
        for p in (web, api):
            try:
                p.wait(timeout=6)
            except subprocess.TimeoutExpired:
                p.kill()
        print()
        ok("stopped")
    return 0


def start() -> int:
    _preflight()
    if read_pid(API_PID) or read_pid(WEB_PID):
        warn("already running — use restart")
        return status()
    head("starting DECINT (detached)")
    with API_LOG.open("w") as lf:
        api = subprocess.Popen(_uvicorn_cmd(), cwd=BACKEND, stdout=lf, stderr=lf,
                               start_new_session=True)
    API_PID.write_text(str(api.pid))
    with WEB_LOG.open("w") as lf:
        web = subprocess.Popen(_next_cmd(dev=False), cwd=FRONTEND, stdout=lf, stderr=lf,
                               start_new_session=True,
                               env=node_env({"BACKEND_URL": API_URL}))
    WEB_PID.write_text(str(web.pid))

    api_up = wait_for(f"{API_URL}/api/v1/health", 40)
    web_up = wait_for(WEB_URL, 60)
    (ok if api_up else warn)(f"api pid={api.pid} up={api_up}  ({API_LOG})")
    (ok if web_up else warn)(f"web pid={web.pid} up={web_up}  ({WEB_LOG})")
    return 0 if (api_up and web_up) else 1


def stop() -> int:
    head("stopping DECINT")
    stopped = 0
    for path in (WEB_PID, API_PID):
        pid = read_pid(path)
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            stopped += 1
        path.unlink(missing_ok=True)
    # Sweep anything the pidfiles missed (a crashed start, a manual run).
    for pattern in ("uvicorn app.main:app", "next start", "next-server", "next dev"):
        run(["pkill", "-f", pattern], check=False, quiet=True)
    ok(f"stopped ({stopped} tracked process(es))")
    return 0


def status() -> int:
    head("DECINT status")
    npm = node_bin("npm")
    info(f"root         {ROOT}")
    info(f"python       {out([str(venv_python()), '--version']) or 'n/a'}")
    info(f"node         {out([node_bin('node') or 'node', '--version']) or 'NOT INSTALLED'}")
    info(f"venv         {'present' if venv_ready() else 'MISSING'}")
    info(f"node_modules {'present' if (FRONTEND / 'node_modules').is_dir() else 'MISSING'}")
    info(f"next build   {'present' if (FRONTEND / '.next').is_dir() else 'MISSING'}")
    info(f"backend/.env {'present' if (BACKEND / '.env').exists() else 'MISSING'}")
    info(f"tor          {out(['systemctl', 'is-active', 'tor']) or 'unknown'}")

    print()
    api_code, api_body = http(f"{API_URL}/api/v1/health")
    web_code, _ = http(WEB_URL)
    (ok if api_code == 200 else warn)(f"api  {API_URL} -> {api_code or 'down'}")
    (ok if web_code == 200 else warn)(f"web  {WEB_URL} -> {web_code or 'down'}")

    if api_code == 200:
        import json
        try:
            h = json.loads(api_body)
            info(f"     version={h['version']} tor={h['tor']} sniffer={h['sniffer_enabled']} "
                 f"discord={h['discord_enabled']} auth={h['auth_enabled']}")
        except (ValueError, KeyError):
            pass
    return 0 if (api_code == 200 and web_code == 200) else 1


def logs(which: str, n: int) -> int:
    path = API_LOG if which == "api" else WEB_LOG
    head(f"{which} log ({path})")
    if not path.exists():
        warn("no log yet")
        return 1
    lines = path.read_text(errors="replace").splitlines()
    for line in lines[-n:]:
        print("   " + line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the DECINT services")
    ap.add_argument("action",
                    choices=["dev", "start", "stop", "restart", "status", "build", "logs"])
    ap.add_argument("which", nargs="?", default="api", choices=["api", "web"])
    ap.add_argument("-n", type=int, default=40)
    a = ap.parse_args()

    if a.action == "dev":
        return dev()
    if a.action == "start":
        return start()
    if a.action == "stop":
        return stop()
    if a.action == "restart":
        stop(); time.sleep(1); return start()
    if a.action == "status":
        return status()
    if a.action == "build":
        build(); return 0
    if a.action == "logs":
        return logs(a.which, a.n)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

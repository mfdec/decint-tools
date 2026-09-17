#!/usr/bin/env python3
"""Account management from the shell.

    python tools/accounts.py list
    python tools/accounts.py create-admin --email you@decint.tools
    python tools/accounts.py create-user  --email client@example.com --tier professional
    python tools/accounts.py set-role  --email x@y.com --role operator
    python tools/accounts.py set-tier  --email x@y.com --tier professional
    python tools/accounts.py set-password --email x@y.com
    python tools/accounts.py reset-mfa --email x@y.com
    python tools/accounts.py audit -n 30

Thin front-end over `app.cli` so it runs with the backend venv automatically —
you don't have to remember to activate it or cd into backend/. Passwords are
always read from a prompt, never argv, so they stay out of shell history.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BACKEND, die, head, info, venv_python, venv_ready  # noqa: E402


def main() -> int:
    if not venv_ready():
        die("backend venv missing — python tools/decint.py install --backend")

    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    # `audit` isn't in app.cli — read it straight from the database.
    if args[0] == "audit":
        n = 20
        if "-n" in args:
            try:
                n = int(args[args.index("-n") + 1])
            except (IndexError, ValueError):
                pass
        code = (
            "import sys; sys.path.insert(0,'.');"
            "from app import db; from app.services import users; db.get_conn();"
            f"rows=users.audit_log({n});"
            "print(f'{\"time\":20} {\"action\":24} {\"actor\":28} {\"target\":24} ip') or None;"
            "[print(f'{r[\"ts\"][:19]:20} {r[\"action\"]:24} {str(r[\"actor\"]):28} "
            "{str(r[\"target\"]):24} {r[\"ip\"] or \"\"}') for r in rows]"
        )
        import subprocess
        head(f"last {n} audit entries")
        return subprocess.run([str(venv_python()), "-c", code], cwd=str(BACKEND)).returncode

    import subprocess
    return subprocess.run(
        [str(venv_python()), "-m", "app.cli", *args], cwd=str(BACKEND)
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())

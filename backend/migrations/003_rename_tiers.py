"""Rename plan key: essentials→starter (the 2026-09-17 re-pricing).

The entry tier went back to its original name when it dropped to $2.95/mo, so
this is the inverse of the first rename in 002 for that one key. Same shape,
same reason:

`plans.Plan.key` doubles as the tier written to `users.tier`, so renaming a key
in the catalogue orphans every row still holding the old value — the account
keeps a tier no plan matches, and `plans.get()` returns None for it.

Idempotent: re-running finds nothing left to rename.

    cd backend && .venv/bin/python migrations/003_rename_tiers.py [--dry-run]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

RENAMES = {
    "essentials": "starter",
}

# (table, column) pairs holding a plan key.
TARGETS = [
    ("users", "tier"),
    ("entitlements", "tier"),
    ("billing_orders", "plan"),
    ("subscriptions", "plan"),
]


def db_path() -> Path:
    p = Path(settings.analytics_db).expanduser()
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def main() -> int:
    dry = "--dry-run" in sys.argv
    path = db_path()
    if not path.exists():
        print(f"no database at {path} — nothing to migrate")
        return 0

    conn = sqlite3.connect(path)
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}

    total = 0
    for table, col in TARGETS:
        if table not in have:
            print(f"  {table:18} — table absent, skipped")
            continue
        for old, new in RENAMES.items():
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (old,)
            ).fetchone()[0]
            if not n:
                continue
            total += n
            print(f"  {table}.{col}: {n} × {old} → {new}")
            if not dry:
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (new, old)
                )

    # The column default still names the old tier.
    if not dry:
        conn.commit()

    print(f"{'would rename' if dry else 'renamed'} {total} row(s)")

    left = []
    for table, col in TARGETS:
        if table not in have:
            continue
        for old in RENAMES:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (old,)
            ).fetchone()[0]
            if n:
                left.append(f"{table}.{col}={old} ({n})")
    if left and not dry:
        print("STILL PRESENT: " + ", ".join(left))
        return 1
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

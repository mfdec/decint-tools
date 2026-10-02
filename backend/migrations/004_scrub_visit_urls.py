"""Scrub credentials out of URLs already stored in the visitor-analytics table.

Until the collector learned to filter, `visits.query` held the raw query string
of every pageview — including `/activate?token=…&email=…` and `/reset?token=…`
links, whose tokens are still valid until they expire or are used. `referrer`
(a same-origin referrer is the previous page's full URL) and the `href` inside
click `meta` could carry the same things.

This rewrites those columns through the collector's own sanitisers
(`app.services.analytics`): `query` keeps only the allowlisted params,
`path`/`referrer`/`meta.href` lose their query string and fragment. Nothing
else in a row is touched, and no row is deleted.

Idempotent: a second run finds every row already clean and changes nothing.

    cd backend && .venv/bin/python migrations/004_scrub_visit_urls.py --dry-run
    cd backend && .venv/bin/python migrations/004_scrub_visit_urls.py [--db PATH]

Before running it on the live database:

* Run it as the user that owns the database (`sudo -u decint …`). As root it
  can leave a root-owned -wal/-shm beside the file, which the service then
  cannot open.
* Take a backup first, e.g. `sqlite3 data/analytics.db ".backup /safe/place.db"`.
  Any older copy of the file (backups, `*.pre-cleanup-*.db`) still holds the
  tokens; this script does not reach them.
* It is safe with the API running: rows are rewritten in small batches, each
  its own short write transaction.

Freed pages are zero-filled (`secure_delete`) and the WAL is checkpointed and
truncated afterwards, so the old values don't linger in the file. It does not
VACUUM; that takes an exclusive lock for the whole rewrite.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services import analytics  # noqa: E402

COLUMNS = ("path", "query", "referrer", "meta")
BATCH = 500


def db_path(override: str | None = None) -> Path:
    p = Path(override or settings.analytics_db).expanduser()
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def _scan(conn: sqlite3.Connection, apply: bool) -> tuple[int, int, dict[str, int]]:
    """Walk the table in id order. Returns (rows seen, rows changed, per column)."""
    seen = changed = 0
    per_col = {c: 0 for c in COLUMNS}
    last = 0
    while True:
        rows = conn.execute(
            f"SELECT id, {', '.join(COLUMNS)} FROM visits WHERE id > ? ORDER BY id LIMIT ?",
            (last, BATCH),
        ).fetchall()
        if not rows:
            break
        last = rows[-1][0]
        seen += len(rows)
        updates = []
        for rid, *vals in rows:
            fix = analytics.scrub_row(dict(zip(COLUMNS, vals)))
            if not fix:
                continue
            changed += 1
            for c in fix:
                per_col[c] += 1
            updates.append((fix, rid))
        if apply and updates:
            with conn:  # one short write transaction per batch
                for fix, rid in updates:
                    sets = ", ".join(f"{c} = ?" for c in fix)
                    conn.execute(
                        f"UPDATE visits SET {sets} WHERE id = ?", (*fix.values(), rid)
                    )
    return seen, changed, per_col


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--db", help="database file (default: ANALYTICS_DB from the settings)")
    args = ap.parse_args(argv)

    path = db_path(args.db)
    if not path.exists():
        print(f"no database at {path} — nothing to scrub")
        return 0

    if args.dry_run:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA secure_delete = ON")
    print(f"{'dry run on' if args.dry_run else 'scrubbing'} {path}")

    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='visits'"
        ).fetchone():
            print("  no visits table — nothing to scrub")
            return 0

        seen, changed, per_col = _scan(conn, apply=not args.dry_run)
        detail = ", ".join(f"{c} {n}" for c, n in per_col.items() if n) or "-"
        print(f"  {seen} row(s) examined; "
              f"{'would rewrite' if args.dry_run else 'rewrote'} {changed} ({detail})")

        if args.dry_run:
            return 0

        busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            print("  WAL not fully truncated (a reader held it); old values may "
                  "remain in the -wal file until the next checkpoint")

        _, left, _ = _scan(conn, apply=False)
        if left:
            print(f"STILL DIRTY: {left} row(s) would still change on a re-run")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

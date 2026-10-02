"""Admin-uploaded leak datasets — storage, ingest and search.

The leak search fans out to free public sources. This module adds the
operator's own: an admin uploads a .txt / .csv / .json file, it is parsed into
a normalised index, and the aggregator searches it alongside the public
providers until the admin pauses or removes it.

Design notes worth knowing before changing anything:

* **Own SQLite file** (`LEAKS_DB`), not the app database. The app DB has one
  connection and one lock shared by every request, so a bulk import into it
  would stall page loads and sign-ins. Here, WAL lets searches read while an
  import writes, and the whole thing can be backed up, moved or wiped alone.
* **Chunked upload.** The reverse proxies cap a request body at 10 MB and real
  files are bigger, so the browser sends 4 MB pieces to an upload session (the
  dataset row itself) and then says "finish". Nothing is held in memory.
* **Minimal index.** Only what search needs is kept — email, username, domain
  and the secret — never other columns, which are recorded as *names* only.
* **Secrets follow the existing policy.** They come back through the same
  mask-by-default path as every other provider (services/leaks/service.py).
* **Removal is real.** `secure_delete` overwrites freed pages and the WAL is
  truncated afterwards, so a removed dataset is not left readable in the file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from ...config import settings
from .base import mask_secret

log = logging.getLogger("decint.leaks.local")

FORMATS = ("txt", "csv", "json")
CHUNK_BYTES = 4 * 1024 * 1024          # what the browser sends per request
CHUNK_MAX = 8 * 1024 * 1024            # what the server accepts per request
BATCH = 10_000                          # rows per insert transaction
SEARCH_LIMIT = 50
COUNT_CAP = 10_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    description    TEXT,
    filename       TEXT    NOT NULL,
    format         TEXT    NOT NULL,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    sha256         TEXT,
    status         TEXT    NOT NULL DEFAULT 'uploading',  -- uploading|processing|ready|failed|removing
    error          TEXT,
    records        INTEGER NOT NULL DEFAULT 0,
    skipped        INTEGER NOT NULL DEFAULT 0,
    fields         TEXT,                                   -- JSON list of field names seen
    enabled        INTEGER NOT NULL DEFAULT 1,
    uploaded_by    TEXT,
    created_at     TEXT    NOT NULL,
    ready_at       TEXT
);
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL,
    email       TEXT,
    username    TEXT,
    domain      TEXT,
    secret      TEXT,
    secret_kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_rec_email    ON records (email);
CREATE INDEX IF NOT EXISTS idx_rec_username ON records (username);
CREATE INDEX IF NOT EXISTS idx_rec_domain   ON records (domain);
CREATE INDEX IF NOT EXISTS idx_rec_dataset  ON records (dataset_id);
"""

# Set by services/leaks/service.py: cached search results must be dropped the
# moment a dataset appears, pauses or is removed, or a removed dataset would
# keep answering queries from cache for minutes.
on_change: Callable[[], None] = lambda: None


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ─────────────────────────── storage ───────────────────────────

_schema_lock = threading.Lock()
_schema_ready: str | None = None


def _path() -> Path:
    p = Path(settings.leaks_db).expanduser()
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent.parent.parent / p
    return p


def _upload_dir() -> Path:
    d = _path().parent / "leak_uploads"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    global _schema_ready
    path = _path()
    with _schema_lock:
        if _schema_ready != str(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(path, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            con.commit()
            con.close()
            os.chmod(path, 0o600)
            _schema_ready = str(path)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA secure_delete=ON")
    try:
        yield con
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    try:
        d["fields"] = json.loads(d["fields"]) if d.get("fields") else []
    except ValueError:
        d["fields"] = []
    d["enabled"] = bool(d["enabled"])
    return d


def limits() -> dict:
    return {"max_bytes": settings.leaks_upload_max_mb * 1024 * 1024,
            "chunk_bytes": CHUNK_BYTES, "formats": list(FORMATS)}


def list_datasets() -> list[dict]:
    with _db() as con:
        return [_row(r) for r in con.execute("SELECT * FROM datasets ORDER BY id DESC")]


def get(dataset_id: int) -> dict | None:
    with _db() as con:
        return _row(con.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone())


def has_searchable() -> bool:
    try:
        with _db() as con:
            return con.execute(
                "SELECT 1 FROM datasets WHERE enabled = 1 AND status = 'ready' LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


# ─────────────────────────── upload session ───────────────────────────

def create_upload(filename: str, size: int, name: str, description: str,
                  uploaded_by: str) -> dict:
    fname = os.path.basename(filename or "").strip()[:200]
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in FORMATS:
        raise UploadError("Only .txt, .csv and .json files can be added.")
    if size <= 0:
        raise UploadError("That file is empty.")
    if size > limits()["max_bytes"]:
        raise UploadError(
            f"That file is larger than the {settings.leaks_upload_max_mb} MB limit.", 413)
    with _db() as con:
        cur = con.execute(
            "INSERT INTO datasets (name, description, filename, format, size_bytes, "
            "uploaded_by, created_at) VALUES (?,?,?,?,?,?,?)",
            ((name or fname).strip()[:120], (description or "").strip()[:500], fname,
             ext, size, uploaded_by, _now()))
        con.commit()
        ds_id = cur.lastrowid
    (_upload_dir() / f"{ds_id}.part").write_bytes(b"")
    os.chmod(_upload_dir() / f"{ds_id}.part", 0o600)
    return get(ds_id)  # type: ignore[return-value]


_append_lock = threading.Lock()


def append_chunk(dataset_id: int, offset: int, data: bytes) -> dict:
    if len(data) > CHUNK_MAX:
        raise UploadError("Chunk too large.", 413)
    with _append_lock:
        ds = get(dataset_id)
        if not ds:
            raise UploadError("No such dataset.", 404)
        if ds["status"] != "uploading":
            raise UploadError("This upload is no longer accepting data.", 409)
        if offset != ds["received_bytes"]:
            raise UploadError(f"Out of order: server has {ds['received_bytes']} bytes.", 409)
        if offset + len(data) > ds["size_bytes"]:
            raise UploadError("More data than the file size declared.", 400)
        part = _upload_dir() / f"{dataset_id}.part"
        with open(part, "ab") as f:
            f.write(data)
        with _db() as con:
            con.execute("UPDATE datasets SET received_bytes = ? WHERE id = ?",
                        (offset + len(data), dataset_id))
            con.commit()
        return {"received": offset + len(data)}


def finish_upload(dataset_id: int) -> dict:
    ds = get(dataset_id)
    if not ds:
        raise UploadError("No such dataset.", 404)
    if ds["status"] != "uploading":
        raise UploadError("This upload was already finished.", 409)
    if ds["received_bytes"] != ds["size_bytes"]:
        raise UploadError(
            f"Upload incomplete: {ds['received_bytes']} of {ds['size_bytes']} bytes.", 400)
    with _db() as con:
        con.execute("UPDATE datasets SET status = 'processing' WHERE id = ?", (dataset_id,))
        con.commit()
    threading.Thread(target=_run_ingest, args=(dataset_id,), daemon=True,
                     name=f"leak-ingest-{dataset_id}").start()
    return get(dataset_id)  # type: ignore[return-value]


# ─────────────────────────── management ───────────────────────────

def update(dataset_id: int, *, enabled: bool | None = None, name: str | None = None,
           description: str | None = None) -> dict | None:
    sets, params = [], []
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if name is not None and name.strip():
        sets.append("name = ?"); params.append(name.strip()[:120])
    if description is not None:
        sets.append("description = ?"); params.append(description.strip()[:500])
    if sets:
        with _db() as con:
            con.execute(f"UPDATE datasets SET {', '.join(sets)} WHERE id = ?", (*params, dataset_id))
            con.commit()
        on_change()
    return get(dataset_id)


def remove(dataset_id: int) -> bool:
    """Take a dataset out of service now and erase its records in the
    background (a large one is millions of rows)."""
    ds = get(dataset_id)
    if not ds:
        return False
    if ds["status"] == "processing":
        # The import thread is still writing rows; erasing underneath it would
        # leave orphans behind once the dataset row is gone.
        raise UploadError("It is still being processed — remove it once it finishes.", 409)
    with _db() as con:
        con.execute("UPDATE datasets SET status = 'removing', enabled = 0 WHERE id = ?", (dataset_id,))
        con.commit()
    on_change()
    threading.Thread(target=_run_remove, args=(dataset_id,), daemon=True,
                     name=f"leak-remove-{dataset_id}").start()
    return True


def _run_remove(dataset_id: int) -> None:
    try:
        _purge_records(dataset_id)
        with _db() as con:
            con.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
            con.commit()
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        (_upload_dir() / f"{dataset_id}.part").unlink(missing_ok=True)
    except Exception:
        log.exception("removing leak dataset %s failed", dataset_id)
    finally:
        on_change()


def _purge_records(dataset_id: int) -> None:
    with _db() as con:
        while True:
            cur = con.execute(
                "DELETE FROM records WHERE id IN "
                "(SELECT id FROM records WHERE dataset_id = ? LIMIT 20000)", (dataset_id,))
            con.commit()
            if cur.rowcount <= 0:
                break


def recover() -> None:
    """At service start: an upload or import cut off by a restart can never
    finish, and a removal can be resumed."""
    try:
        with _db() as con:
            stuck = [r["id"] for r in con.execute(
                "SELECT id FROM datasets WHERE status IN ('uploading','processing')")]
            removing = [r["id"] for r in con.execute("SELECT id FROM datasets WHERE status = 'removing'")]
        for ds_id in stuck:
            _purge_records(ds_id)
            with _db() as con:
                con.execute("UPDATE datasets SET status='failed', error=?, records=0 WHERE id=?",
                            ("Interrupted by a service restart — upload it again.", ds_id))
                con.commit()
            (_upload_dir() / f"{ds_id}.part").unlink(missing_ok=True)
        for ds_id in removing:
            threading.Thread(target=_run_remove, args=(ds_id,), daemon=True).start()
    except Exception:
        log.exception("leak dataset recovery failed")


# ─────────────────────────── parsing ───────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s:;|,]+@[^@\s:;|,]+\.[^@\s:;|,]+$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I)
_HASH_RE = re.compile(
    r"^(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{56}|[a-fA-F0-9]{64}|[a-fA-F0-9]{96}|[a-fA-F0-9]{128}"
    r"|\$(?:2[abxy]?|argon2\w*|\d\w?|pbkdf2[\w-]*|scrypt)\$.+)$")
# `https://site.com[:port][/path]:user:pass` — the stealer-log layout. The path
# may not contain ':', which is what lets the user:pass part be found after it.
_URL_PREFIX = re.compile(r"^https?://[^\s:/]+(?::\d+)?(?:/[^\s:]*)?:")

_ALIASES: dict[str, set[str]] = {
    "email": {"email", "e_mail", "mail", "email_address", "emailaddress", "user_email", "useremail"},
    "username": {"username", "user_name", "user", "usr", "login", "handle", "nick", "nickname",
                 "uname", "userid", "user_id", "account"},
    "password": {"password", "pass", "passwd", "pwd", "plaintext", "plain", "cleartext", "password_plain"},
    "hash": {"hash", "password_hash", "passwordhash", "pass_hash", "pw_hash", "hashed_password",
             "md5", "sha1", "sha256", "bcrypt"},
    "domain": {"domain", "site", "website", "host", "hostname", "url", "service", "origin"},
}
_ALIAS_KIND = {a: kind for kind, names in _ALIASES.items() for a in names}
_FIELD_LABEL = {"hash": "password hash"}


def _norm_key(k: object) -> str:
    return re.sub(r"[\s\-]+", "_", str(k).strip().lower())


def _host(value: str) -> str | None:
    v = value.strip().lower()
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v)
    v = re.split(r"[/?#]", v, 1)[0]
    v = v.rsplit("@", 1)[-1].split(":", 1)[0].removeprefix("www.")
    return v if _DOMAIN_RE.match(v) else None


def _record(email: str | None, username: str | None, domain: str | None,
            secret: str | None, kind: str | None, fields: set[str]) -> tuple | None:
    email = (email or "").strip().lower()[:320] or None
    username = (username or "").strip().lower()[:128] or None
    if email and not _EMAIL_RE.match(email):
        username, email = username or email[:128], None
    if domain:
        domain = _host(domain)
    if email and not domain:
        domain = email.rsplit("@", 1)[1]
    if not (email or username or domain):
        return None
    secret = (secret or "").strip()[:256] or None
    if secret and not kind:
        kind = "hash" if _HASH_RE.match(secret) else "plain"
    if email:
        fields.add("email")
    if username:
        fields.add("username")
    if domain:
        fields.add("domain")
    if secret:
        fields.add("password hash" if kind == "hash" else "password")
    return (email, username, domain, secret, kind if secret else None)


def _parse_line(line: str, fields: set[str]) -> tuple | None:
    domain = None
    m = _URL_PREFIX.match(line)
    if m:
        domain = _host(m.group(0)[:-1])
        line = line[m.end():]
    cuts = [i for i in (line.find(s) for s in (":", ";", "|", "\t")) if i >= 0]
    if cuts:
        i = min(cuts)
        ident, secret = line[:i], line[i + 1:]
    else:
        ident, secret = line, None
    ident = ident.strip()
    if not ident:
        return None
    if _EMAIL_RE.match(ident):
        return _record(ident, None, domain, secret, None, fields)
    rec = _record(None, ident, domain, secret, None, fields)
    # A bare dotted token might be a domain list rather than a username list;
    # index it as both so either search finds it.
    if rec and not secret and not domain and _DOMAIN_RE.match(ident):
        rec = (None, rec[1], ident.lower(), None, None)
        fields.add("domain")
    return rec


def _from_mapping(d: dict, fields: set[str]) -> tuple | None:
    got: dict[str, str] = {}
    for k, v in d.items():
        kind = _ALIAS_KIND.get(_norm_key(k))
        if kind is None:
            if len(fields) < 40 and isinstance(k, str):
                fields.add(f"col:{k.strip()[:30]}")
            continue
        if isinstance(v, (str, int, float)) and str(v).strip() and kind not in got:
            got[kind] = str(v)
    secret = got.get("password") or got.get("hash")
    kind = "plain" if got.get("password") else ("hash" if got.get("hash") else None)
    return _record(got.get("email"), got.get("username"), got.get("domain"), secret, kind, fields)


def _iter_txt(path: Path, fields: set[str]) -> Iterator[tuple | None]:
    with open(path, "rb") as f:
        while True:
            raw = f.readline(4001)
            if not raw:
                return
            if len(raw) == 4001 and not raw.endswith(b"\n"):
                while True:                       # swallow the rest of an absurd line
                    rest = f.readline(65536)
                    if not rest or rest.endswith(b"\n"):
                        break
                yield None
                continue
            line = raw.decode("utf-8", "replace").strip().lstrip("\ufeff")
            if not line or line.startswith("#"):
                continue
            yield _parse_line(line, fields)


def _iter_csv(path: Path, fields: set[str]) -> Iterator[tuple | None]:
    csv.field_size_limit(1 << 20)
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(65536)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        first = next(reader, None)
        if first is None:
            return
        names = [_norm_key(c) for c in first]
        colmap = {i: _ALIAS_KIND[n] for i, n in enumerate(names) if n in _ALIAS_KIND}
        if colmap:                                 # has a header row
            for i, n in enumerate(names):
                if i not in colmap and n and len(fields) < 40:
                    fields.add(f"col:{first[i].strip()[:30]}")
            rows: Iterator[list[str]] = reader
        else:                                      # headerless: ident, secret, …
            head = [first] + [r for _, r in zip(range(19), reader)]
            looks_like_emails = sum(1 for r in head if r and _EMAIL_RE.match(r[0].strip()))
            if looks_like_emails * 2 < len(head):
                # Without a header, only a first column of emails is safe to
                # trust. Anything else is almost certainly a header we can't
                # read, and indexing its values as usernames would be junk.
                raise ValueError(
                    "Couldn't find an email or username column. Add a header row "
                    "such as: email,password")
            colmap = {0: "ident", 1: "secret"}
            rows = _chain_all(head, reader)
        for row in rows:
            if not row:
                continue
            got: dict[str, str] = {}
            for i, kind in colmap.items():
                if i < len(row) and row[i].strip() and kind not in got:
                    got[kind] = row[i]
            if "ident" in got:
                ident = got["ident"].strip()
                got["email" if _EMAIL_RE.match(ident) else "username"] = ident
            secret = got.get("password") or got.get("hash") or got.get("secret")
            kind = "plain" if got.get("password") else ("hash" if got.get("hash") else None)
            yield _record(got.get("email"), got.get("username"), got.get("domain"), secret, kind, fields)


def _chain_all(head: list[list[str]], rest: Iterator[list[str]]) -> Iterator[list[str]]:
    yield from head
    yield from rest


_WS_COMMA = re.compile(r"[\s,]*")
_JSON_BUFFER_MAX = 32 * 1024 * 1024


def _iter_json_values(path: Path) -> Iterator[object]:
    """Top-level values of a JSON array, NDJSON, or concatenated objects,
    decoded incrementally so a 300 MB array is never held whole."""
    dec = json.JSONDecoder()
    buf, state = "", "start"
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        while True:
            chunk = f.read(1 << 20)
            eof = not chunk
            buf += chunk
            pos = 0
            while True:
                pos = _WS_COMMA.match(buf, pos).end()  # type: ignore[union-attr]
                if pos >= len(buf):
                    break
                ch = buf[pos]
                if state == "start":
                    state = "array" if ch == "[" else "stream"
                    if state == "array":
                        pos += 1
                        continue
                if state == "array" and ch == "]":
                    return
                try:
                    obj, end = dec.raw_decode(buf, pos)
                except json.JSONDecodeError as e:
                    if eof:
                        raise ValueError(f"Invalid JSON: {e.msg} (character {e.pos})") from e
                    break                              # value continues in the next chunk
                if end >= len(buf) and not eof:
                    break                              # a number/string may be cut short
                yield obj
                pos = end
            buf = buf[pos:]
            if len(buf) > _JSON_BUFFER_MAX:
                raise ValueError(
                    "A single JSON value is larger than 32 MB. Use an array of records "
                    "or one record per line.")
            if eof:
                return


def _from_json_value(obj: object, fields: set[str], depth: int = 0) -> Iterator[tuple | None]:
    if isinstance(obj, str):
        yield _parse_line(obj.strip(), fields) if obj.strip() else None
    elif isinstance(obj, list):
        for item in obj:
            yield from _from_json_value(item, fields, depth + 1)
    elif isinstance(obj, dict):
        rec = _from_mapping(obj, fields)
        if rec is not None:
            yield rec
            return
        nested = [v for v in obj.values() if isinstance(v, list)]
        if depth < 2 and nested:                       # {"data": [ ... ]} wrappers
            for v in nested:
                yield from _from_json_value(v, fields, depth + 1)
        else:
            yield None
    else:
        yield None


def _iter_json(path: Path, fields: set[str]) -> Iterator[tuple | None]:
    for value in _iter_json_values(path):
        yield from _from_json_value(value, fields)


_PARSERS = {"txt": _iter_txt, "csv": _iter_csv, "json": _iter_json}


# ─────────────────────────── ingest ───────────────────────────

def _run_ingest(dataset_id: int) -> None:
    part = _upload_dir() / f"{dataset_id}.part"
    records = skipped = 0
    try:
        ds = get(dataset_id)
        if not ds:
            return
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        digest = h.hexdigest()
        with _db() as con:
            dup = con.execute(
                "SELECT name FROM datasets WHERE sha256 = ? AND id != ? AND status = 'ready'",
                (digest, dataset_id)).fetchone()
            con.execute("UPDATE datasets SET sha256 = ? WHERE id = ?", (digest, dataset_id))
            con.commit()
        if dup:
            raise ValueError(f"This exact file is already loaded as “{dup['name']}”.")

        fields: set[str] = set()
        batch: list[tuple] = []
        with _db() as con:
            def flush() -> None:
                nonlocal batch
                if batch:
                    con.executemany(
                        "INSERT INTO records (dataset_id, email, username, domain, secret, secret_kind) "
                        "VALUES (?,?,?,?,?,?)", batch)
                    batch = []
                con.execute("UPDATE datasets SET records = ?, skipped = ? WHERE id = ?",
                            (records, skipped, dataset_id))
                con.commit()

            for rec in _PARSERS[ds["format"]](part, fields):
                if rec is None:
                    skipped += 1
                    continue
                records += 1
                batch.append((dataset_id, *rec))
                if len(batch) >= BATCH:
                    flush()
            flush()
        if records == 0:
            raise ValueError(
                "No searchable records found. Looked for email, username and domain "
                "columns (or lines like email:password).")
        ordered = sorted(fields, key=lambda x: (x.startswith("col:"), x))
        with _db() as con:
            con.execute(
                "UPDATE datasets SET status='ready', error=NULL, records=?, skipped=?, fields=?, ready_at=? "
                "WHERE id = ?",
                (records, skipped, json.dumps([x.removeprefix("col:") for x in ordered][:40]),
                 _now(), dataset_id))
            con.commit()
    except Exception as e:
        log.warning("leak dataset %s failed: %s", dataset_id, e)
        try:
            _purge_records(dataset_id)
            with _db() as con:
                con.execute("UPDATE datasets SET status='failed', error=?, records=0 WHERE id=?",
                            (str(e)[:300], dataset_id))
                con.commit()
        except Exception:
            log.exception("could not record failure for leak dataset %s", dataset_id)
    finally:
        part.unlink(missing_ok=True)
        on_change()


# ─────────────────────────── preview & search ───────────────────────────

def preview(dataset_id: int, n: int = 8) -> list[dict]:
    """A few rows so the admin can check the file was read the way they meant.
    Secrets stay masked here; this is not a way to read a dataset."""
    with _db() as con:
        rows = con.execute(
            "SELECT email, username, domain, secret, secret_kind FROM records "
            "WHERE dataset_id = ? ORDER BY id LIMIT ?", (dataset_id, n)).fetchall()
    return [{"email": r["email"], "username": r["username"], "domain": r["domain"],
             "secret": mask_secret(r["secret"]), "secret_kind": r["secret_kind"]} for r in rows]


_SEARCHABLE = "FROM records r JOIN datasets d ON d.id = r.dataset_id AND d.enabled = 1 AND d.status = 'ready' "


def search(query: str, kind: str) -> tuple[list[dict], int]:
    """Matches across every enabled, ready dataset. Returns (rows, total) where
    total is capped so a domain with millions of rows can't make a search slow."""
    q = query.strip().lower()
    if kind == "email":
        where, params = "r.email = ?", (q,)
    elif kind == "domain":
        where, params = "r.domain = ?", (q.removeprefix("www."),)
    else:  # username — also the local part of an email, via an index range
        where, params = "(r.username = ? OR (r.email >= ? AND r.email < ?))", (q, q + "@", q + "@\uffff")
    with _db() as con:
        rows = con.execute(
            f"SELECT r.email, r.username, r.domain, r.secret, r.secret_kind, d.id AS dataset_id, "
            f"d.name AS dataset, d.fields AS fields {_SEARCHABLE} WHERE {where} "
            f"ORDER BY r.id LIMIT {SEARCH_LIMIT}", params).fetchall()
        total = con.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 {_SEARCHABLE} WHERE {where} LIMIT {COUNT_CAP + 1})",
            params).fetchone()[0]
    return [dict(r) for r in rows], total

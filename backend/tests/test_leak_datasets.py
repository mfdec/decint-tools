"""Admin-uploaded leak datasets: parsing, chunked upload, search, removal.

What matters: .txt / .csv / .json files in the layouts leak files actually come
in are read into the right fields; an upload survives being split into pieces
and refuses out-of-order, oversized or wrongly-typed ones; an uploaded dataset
is searched alongside the other providers with secrets masked by default; and
pausing or removing one stops it answering *immediately*, including from the
result cache. Only admins can touch any of it.

Run on its own — it points ANALYTICS_DB and LEAKS_DB at temp files before
importing the app, which is what keeps it off the live databases.
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ["ANALYTICS_DB"] = os.path.join(tempfile.mkdtemp(), "app.db")
os.environ["LEAKS_DB"] = os.path.join(tempfile.mkdtemp(), "leaks.db")
os.environ["LEAKS_PROVIDERS"] = ""          # no network: only uploaded datasets answer
os.environ["LEAKS_UPLOAD_MAX_MB"] = "1"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_FROM"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
import app.config as cfg  # noqa: E402

cfg.settings = get_settings()

from app import db  # noqa: E402
from app.services import users  # noqa: E402
from app.services.leaks import local, search_leaks  # noqa: E402
from app.services.leaks import service as leak_service  # noqa: E402

_seq = [0]


def _admin():
    _seq[0] += 1
    db.get_conn()
    return users.create(f"leakadmin{_seq[0]}@example.test", "a-long-enough-password",
                        role="admin", tier="enterprise")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    c.cookies.set(cfg.settings.session_cookie, users.create_session(_admin()["id"]))
    return c


def _wait(ds_id, want=("ready", "failed"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        ds = local.get(ds_id)
        if ds is None or ds["status"] in want:
            return ds
        time.sleep(0.02)
    raise AssertionError(f"dataset {ds_id} stuck: {local.get(ds_id)}")


def _ingest(tmp_path: Path, filename: str, content: str | bytes, **kw) -> dict:
    """Straight through the service, no HTTP."""
    data = content.encode() if isinstance(content, str) else content
    ds = local.create_upload(filename, len(data), kw.get("name", ""), kw.get("description", ""), "t@example.test")
    local.append_chunk(ds["id"], 0, data)
    local.finish_upload(ds["id"])
    return _wait(ds["id"])


def _search(q, kind="auto", reveal=False):
    return asyncio.run(search_leaks(q, kind=kind, reveal=reveal))


# ─────────────────────────── parsing ───────────────────────────

def test_txt_combolist_and_bare_lists(tmp_path):
    ds = _ingest(tmp_path, "combo.txt",
                 "alice@corp.test:hunter2\n"
                 "bob:letmein\n"
                 "carol@corp.test;semi;colon\n"
                 "dave@corp.test|pipe\n"
                 "# a comment\n"
                 "\n"
                 "erin@corp.test\n")
    assert ds["status"] == "ready" and ds["records"] == 5
    assert "password" in ds["fields"] and "email" in ds["fields"]
    got = {r["email"] or r["username"]: r["secret"] for r in local.search("corp.test", "domain")[0]}
    assert got["alice@corp.test"] == "hunter2"
    assert got["carol@corp.test"] == "semi;colon"                 # only the first separator splits
    assert local.search("bob", "username")[0][0]["secret"] == "letmein"
    assert local.search("erin@corp.test", "email")[0][0]["secret"] is None


def test_txt_stealer_log_lines_keep_the_site(tmp_path):
    ds = _ingest(tmp_path, "logs.txt",
                 "https://shop.example.net:8443/login:zed@example.net:p:a:ss\n"
                 "https://www.forum.test/u:yan:pw\n")
    assert ds["records"] == 2
    row = local.search("zed@example.net", "email")[0][0]
    assert row["secret"] == "p:a:ss" and row["domain"] == "shop.example.net"   # the site, not the mailbox
    assert local.search("forum.test", "domain")[0][0]["username"] == "yan"


def test_csv_with_header_maps_columns_and_records_the_rest_by_name(tmp_path):
    ds = _ingest(tmp_path, "users.csv",
                 "E-Mail,Password,Phone,Full Name\n"
                 "fay@hosting.test,s3cret,555-0100,Fay Zed\n"
                 "gus@hosting.test,,555-0101,Gus Zed\n")
    assert ds["records"] == 2
    # "Full Name" is a recognised column now: it feeds the name index, so it is
    # reported as first/last name instead of as an unrecognised column.
    assert {"email", "password", "Phone", "first name", "last name"} <= set(ds["fields"])
    assert "Full Name" not in ds["fields"]
    # unrecognised columns are named, never stored
    stored = db_dump()
    assert "555-0100" not in stored and "Fay Zed" not in stored


def test_csv_headerless_and_semicolon_delimited(tmp_path):
    ds = _ingest(tmp_path, "nohead.csv", "hal@x.test;pw1\nivy@x.test;pw2\n")
    assert ds["records"] == 2
    assert local.search("ivy@x.test", "email")[0][0]["secret"] == "pw2"


def test_password_hashes_are_classified(tmp_path):
    _ingest(tmp_path, "hashes.csv",
            "email,hash\njon@h.test,5f4dcc3b5aa765d61d8327deb882cf99\n")
    row = local.search("jon@h.test", "email")[0][0]
    assert row["secret_kind"] == "hash"


def test_json_array_ndjson_and_wrapper(tmp_path):
    arr = json.dumps([{"email": "kim@j.test", "password": "a"}, {"username": "lee", "pass": "b"}, {"nothing": 1}])
    ds = _ingest(tmp_path, "arr.json", arr)
    assert ds["records"] == 2 and ds["skipped"] == 1

    nd = '{"email":"max@j.test","password":"c"}\n{"email":"ned@j.test"}\n'
    assert _ingest(tmp_path, "nd.json", nd)["records"] == 2

    wrapped = json.dumps({"meta": {"v": 1}, "data": [{"email": "oz@j.test"}, {"email": "pat@j.test"}]})
    assert _ingest(tmp_path, "wrap.json", wrapped)["records"] == 2

    strings = json.dumps(["quin@j.test:pw", "ray@j.test"])
    assert _ingest(tmp_path, "strs.json", strings)["records"] == 2


def test_json_streams_across_chunk_boundaries(tmp_path):
    big = json.dumps([{"email": f"u{i}@big.test", "password": f"pw{i}"} for i in range(30000)])
    assert len(big) > (1 << 20)                                   # spans several 1 MiB reads
    ds = _ingest(tmp_path, "big.json", big.encode()) if len(big) <= 1 << 20 else None
    # the size cap in this module is 1 MB, so parse through the iterator directly
    p = tmp_path / "big.json"
    p.write_text(big)
    fields: set = set()
    recs = [r for r in local._iter_json(p, fields) if r]
    assert len(recs) == 30000 and recs[-1][0] == "u29999@big.test"


def test_invalid_json_fails_cleanly_and_leaves_nothing_behind(tmp_path):
    ds = _ingest(tmp_path, "bad.json", '[{"email": "a@b.test"}, {"email": ')
    assert ds["status"] == "failed" and "Invalid JSON" in ds["error"]
    assert ds["records"] == 0
    assert local.search("a@b.test", "email")[0] == []


def test_csv_without_a_readable_header_is_refused_not_guessed(tmp_path):
    ds = _ingest(tmp_path, "junk.csv", "foo,bar\n1,2\n3,4\n")
    assert ds["status"] == "failed" and "Add a header row" in ds["error"]
    assert ds["records"] == 0


def test_file_with_nothing_searchable_fails_with_a_reason(tmp_path):
    ds = _ingest(tmp_path, "empty.txt", "# only a comment\n\n   \n")
    assert ds["status"] == "failed" and "No searchable records" in ds["error"]


def test_the_same_file_twice_is_refused(tmp_path):
    content = "once@dup.test:pw\n"
    assert _ingest(tmp_path, "a.txt", content)["status"] == "ready"
    second = _ingest(tmp_path, "b.txt", content)
    assert second["status"] == "failed" and "already loaded" in second["error"]


def test_absurdly_long_line_is_skipped_not_fatal(tmp_path):
    ds = _ingest(tmp_path, "long.txt", "x" * 20000 + "\nok@long.test:pw\n")
    assert ds["records"] == 1 and ds["skipped"] == 1


# ─────────────────────────── upload protocol ───────────────────────────

def test_upload_rejects_wrong_type_empty_and_oversized():
    with pytest.raises(local.UploadError):
        local.create_upload("evil.exe", 10, "", "", "t")
    with pytest.raises(local.UploadError):
        local.create_upload("a.txt", 0, "", "", "t")
    with pytest.raises(local.UploadError) as e:
        local.create_upload("a.txt", 2 * 1024 * 1024, "", "", "t")
    assert e.value.status == 413


def test_chunks_must_arrive_in_order_and_within_the_declared_size():
    ds = local.create_upload("c.txt", 10, "", "", "t")
    with pytest.raises(local.UploadError) as e:
        local.append_chunk(ds["id"], 5, b"12345")
    assert e.value.status == 409
    local.append_chunk(ds["id"], 0, b"12345")
    with pytest.raises(local.UploadError):
        local.append_chunk(ds["id"], 5, b"123456")                # would exceed the declared size
    with pytest.raises(local.UploadError):
        local.finish_upload(ds["id"])                             # incomplete
    local.remove(ds["id"])


def test_http_upload_in_pieces_end_to_end(client):
    content = "".join(f"user{i}@pieces.test:pw{i}\n" for i in range(400)).encode()
    r = client.post("/api/v1/admin/leak-datasets", json={
        "filename": "pieces.txt", "size": len(content), "name": "Pieces", "description": "split in three"})
    assert r.status_code == 201
    ds_id = r.json()["id"]
    cut = len(content) // 3
    for off, part in ((0, content[:cut]), (cut, content[cut:2 * cut]), (2 * cut, content[2 * cut:])):
        assert client.put(f"/api/v1/admin/leak-datasets/{ds_id}/chunk?offset={off}", content=part).status_code == 200
    assert client.post(f"/api/v1/admin/leak-datasets/{ds_id}/finish").status_code == 200
    ds = _wait(ds_id)
    assert ds["status"] == "ready" and ds["records"] == 400 and ds["name"] == "Pieces"
    assert db.one("SELECT 1 AS x FROM audit_log WHERE action='leak_dataset.uploaded'") is not None

    listed = client.get("/api/v1/admin/leak-datasets").json()
    assert any(d["id"] == ds_id for d in listed["datasets"]) and listed["limits"]["chunk_bytes"] > 0
    rows = client.get(f"/api/v1/admin/leak-datasets/{ds_id}/preview").json()["rows"]
    assert rows and rows[0]["secret"] != "pw0"                    # preview masks secrets


def test_endpoints_are_admin_only(client):
    from fastapi.testclient import TestClient
    from app.main import app

    anon = TestClient(app)
    assert anon.get("/api/v1/admin/leak-datasets").status_code in (401, 403)
    _seq[0] += 1
    plain = users.create(f"plain{_seq[0]}@example.test", "a-long-enough-password")
    anon.cookies.set(cfg.settings.session_cookie, users.create_session(plain["id"]))
    assert anon.get("/api/v1/admin/leak-datasets").status_code == 403
    assert anon.post("/api/v1/admin/leak-datasets", json={"filename": "a.txt", "size": 3}).status_code == 403
    assert anon.delete("/api/v1/admin/leak-datasets/1").status_code == 403


# ─────────────────────────── search & lifecycle ───────────────────────────

def test_uploaded_dataset_is_searched_with_secrets_masked_by_default(tmp_path):
    _ingest(tmp_path, "s.txt", "vic@find.test:correcthorse\n", name="Find set")
    res = _search("vic@find.test")
    assert any(s.key == "local" and s.ok and s.count == 1 for s in res.sources)
    hit = res.hits[0]
    assert hit.breach == "Find set" and hit.email == "vic@find.test"
    assert hit.password != "correcthorse" and hit.password.startswith("c") and "•" in hit.password
    assert _search("vic@find.test", reveal=True).hits[0].password == "correcthorse"


def test_username_search_also_finds_the_local_part_of_an_email(tmp_path):
    _ingest(tmp_path, "u.txt", "wendy@local.test:pw\nwendyx@local.test:pw\n")
    emails = {h.email for h in _search("wendy", kind="username").hits}
    assert emails == {"wendy@local.test"}                         # prefix 'wendy@', not 'wendyx@'


def test_pausing_and_removing_take_effect_immediately_even_when_cached(tmp_path):
    ds = _ingest(tmp_path, "p.txt", "xena@gone.test:pw\n")
    assert _search("xena@gone.test").total == 1                   # now cached

    local.update(ds["id"], enabled=False)
    assert _search("xena@gone.test").total == 0                   # cache dropped, dataset excluded
    local.update(ds["id"], enabled=True)
    assert _search("xena@gone.test").total == 1

    assert local.remove(ds["id"]) is True
    assert _search("xena@gone.test").total == 0
    ds2 = _wait(ds["id"], want=())                                # until the row is gone
    assert ds2 is None


def test_removal_erases_the_records(tmp_path):
    ds = _ingest(tmp_path, "r.txt", "yves@erase.test:pw\n")
    local.remove(ds["id"])
    _wait(ds["id"], want=())
    assert "yves@erase.test" not in db_dump()


def test_removing_while_processing_is_refused():
    ds = local.create_upload("proc.txt", 3, "", "", "t")
    with local._db() as con:
        con.execute("UPDATE datasets SET status='processing' WHERE id=?", (ds["id"],))
        con.commit()
    with pytest.raises(local.UploadError) as e:
        local.remove(ds["id"])
    assert e.value.status == 409
    with local._db() as con:
        con.execute("DELETE FROM datasets WHERE id=?", (ds["id"],))
        con.commit()


def test_recover_fails_stuck_uploads():
    ds = local.create_upload("stuck.txt", 5, "", "", "t")
    local.recover()
    after = local.get(ds["id"])
    assert after["status"] == "failed" and "restart" in after["error"]


def test_http_remove_and_pause(client, tmp_path):
    ds = _ingest(tmp_path, "h.txt", "zoe@http.test:pw\n")
    assert client.patch(f"/api/v1/admin/leak-datasets/{ds['id']}", json={"enabled": False}).json()["enabled"] is False
    assert _search("zoe@http.test").total == 0
    assert client.delete(f"/api/v1/admin/leak-datasets/{ds['id']}").status_code == 200
    _wait(ds["id"], want=())
    assert client.delete(f"/api/v1/admin/leak-datasets/{ds['id']}").status_code == 404
    actions = {r["action"] for r in db.query("SELECT action FROM audit_log")}
    assert {"leak_dataset.paused", "leak_dataset.removed"} <= actions


# ─────────────────────────── names ───────────────────────────

def test_name_columns_are_indexed_and_found_in_any_order_or_case(tmp_path):
    _ingest(tmp_path, "n.csv",
            "first_name,last_name,email,password\n"
            "Jane,Doe,jane@names.test,pw1\n"
            "John,Doe,john@names.test,pw2\n"
            "Mary Ann,O'Neil,mary@names.test,pw3\n", name="People")
    both = {h.email for h in _search("Jane Doe", kind="name").hits}
    assert both == {"jane@names.test"}
    assert {h.email for h in _search("doe jane", kind="name").hits} == {"jane@names.test"}   # swapped
    assert {h.email for h in _search("doe", kind="name").hits} == {"jane@names.test", "john@names.test"}
    assert {h.email for h in _search("john", kind="name").hits} == {"john@names.test"}
    hit = _search("jane doe", kind="name").hits[0]
    assert (hit.first_name, hit.last_name) == ("jane", "doe")
    assert {"first name", "last name"} <= set(hit.fields)
    assert _search("jane smith", kind="name").total == 0                     # both must match


def test_a_full_name_column_is_split_and_auto_detection_picks_name(tmp_path):
    _ingest(tmp_path, "f.json",
            json.dumps([{"full_name": "Alex Quincy Rivera", "email": "alex@full.test"}]), name="Full")
    assert {h.email for h in _search("alex rivera").hits} == {"alex@full.test"}   # auto → name, middle ignored
    assert _search("alex rivera").kind == "name"
    assert _search("alex@full.test").kind == "email"


def test_bare_name_column_is_not_guessed_and_placeholders_are_dropped(tmp_path):
    ds = _ingest(tmp_path, "b.csv",
                 "name,email,first_name\nSome Site,b@bare.test,-\n", name="Bare")
    assert ds["status"] == "ready"
    assert _search("some site", kind="name").total == 0
    row = local.search("b@bare.test", "email")[0][0]
    assert row["first_name"] is None and row["last_name"] is None


def test_name_search_is_never_forwarded_to_other_providers(tmp_path, monkeypatch):
    from app.services.leaks.base import LeakProvider
    from app.services.leaks.providers import REGISTRY

    class External(LeakProvider):
        key, label = "ext", "External"
        supported_kinds = ("email", "username", "domain")
        calls: list = []

        async def search(self, client, query, kind):
            self.calls.append((query, kind))
            return self._ok([])

    ext = External()
    _ingest(tmp_path, "x.csv", "first_name,last_name,email\nPat,Lee,pat@fwd.test\n", name="Fwd")
    monkeypatch.setattr(leak_service, "_enabled_providers", lambda: [ext, REGISTRY["local"]])
    leak_service.invalidate_cache()

    res = _search("pat lee", kind="name")
    assert ext.calls == []                                                    # skipped, not queried
    assert [h.email for h in res.hits] == ["pat@fwd.test"]
    assert next(s for s in res.sources if s.key == "ext").count == 0

    _search("pat@fwd.test")                                                   # a supported kind still goes out
    assert ext.calls == [("pat@fwd.test", "email")]


def test_an_index_from_before_names_is_migrated_in_place(tmp_path, monkeypatch):
    import sqlite3

    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.executescript(local.SCHEMA.replace(",\n    first_name  TEXT,\n    last_name   TEXT", ""))
    con.execute("INSERT INTO datasets (name, filename, format, status, created_at) "
                "VALUES ('old','o.txt','txt','ready','2026-01-01')")
    con.execute("INSERT INTO records (dataset_id, email) VALUES (1, 'kept@old.test')")
    con.commit()
    con.close()

    monkeypatch.setattr(local.settings, "leaks_db", str(old))
    monkeypatch.setattr(local, "_schema_ready", None)
    rows, total = local.search("kept@old.test", "email")
    assert total == 1 and rows[0]["first_name"] is None                      # old rows survive, nameless
    assert local.search("anyone", "name") == ([], 0)
    with local._db() as c:
        assert {"first_name", "last_name"} <= {r[1] for r in c.execute("PRAGMA table_info(records)")}


def db_dump() -> str:
    """Every byte of the leak database as text, to prove what is and isn't stored."""
    path = local._path()
    out = b""
    for suffix in ("", "-wal"):
        f = Path(str(path) + suffix)
        if f.exists():
            out += f.read_bytes()
    return out.decode("utf-8", "ignore")

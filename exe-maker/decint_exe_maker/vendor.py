"""Vendor side of licensing: one RSA keypair per product, license issuing, ledger.

Keys live in ``settings.keys_dir()`` as ``<product_id>.json``. Losing that
file means no more licenses can be issued for EXEs already shipped with the
matching public key — the GUI nags about backups for that reason.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from pathlib import Path

from . import settings
from .runtime import licensing, rsa_lite

MONTH_SECONDS = 30.4375 * 86400  # average month; a 6-month license ≈ 182.6 days


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "app"


def key_path(product_id: str) -> Path:
    return settings.keys_dir() / f"{product_id}.json"


def ledger_path(product_id: str) -> Path:
    return settings.keys_dir() / f"{product_id}.ledger.jsonl"


def list_products() -> list[dict]:
    out = []
    for p in sorted(settings.keys_dir().glob("*.json")):
        try:
            k = json.loads(p.read_text())
            out.append({"product_id": p.stem, "product_name": k.get("product_name", p.stem),
                        "created": k.get("created"), "fingerprint": rsa_lite.fingerprint(k["n"], k["e"])})
        except (OSError, ValueError, KeyError):
            continue
    return out


def load_key(product_id: str) -> dict | None:
    p = key_path(product_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def load_or_create_key(product_id: str, product_name: str = "") -> dict:
    k = load_key(product_id)
    if k:
        if product_name and k.get("product_name") != product_name:
            k["product_name"] = product_name
            _write_key(product_id, k)
        return k
    k = rsa_lite.generate_keypair(2048)
    k.update({"product_id": product_id, "product_name": product_name or product_id,
              "created": int(time.time())})
    _write_key(product_id, k)
    return k


def _write_key(product_id: str, k: dict) -> None:
    p = key_path(product_id)
    p.write_text(json.dumps(k, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def export_key(product_id: str, dest: Path) -> Path:
    dest = Path(dest)
    dest.write_text(key_path(product_id).read_text())
    return dest


def import_key(src: Path) -> str:
    k = json.loads(Path(src).read_text())
    for f in ("n", "e", "d", "product_id"):
        if f not in k:
            raise ValueError(f"not a DECINT product key file (missing '{f}')")
    _write_key(k["product_id"], k)
    return k["product_id"]


def public_part(k: dict) -> dict:
    return {"n": k["n"], "e": k["e"], "fingerprint": rsa_lite.fingerprint(k["n"], k["e"])}


def issue_license(product_id: str, *, customer: str, email: str = "", months: float = 6,
                  expires: float | None = None, machine_id: str | None = None,
                  features: list[str] | None = None, note: str = "") -> tuple[str, dict]:
    """Sign a new license and append it to the product ledger."""
    k = load_key(product_id)
    if not k:
        raise FileNotFoundError(f"no signing key for product '{product_id}' — build it once first")
    now = int(time.time())
    exp = int(expires) if expires else int(now + months * MONTH_SECONDS)
    payload = {
        "v": 1, "pid": product_id, "lid": licensing.new_license_id(),
        "cust": customer.strip(), "email": email.strip(), "iat": now, "exp": exp,
        "mid": (machine_id or "").strip().upper() or None,
        "feat": [f.strip() for f in (features or []) if f.strip()], "note": note.strip(),
    }
    text = licensing.encode_license(payload, k["n"], k["d"])
    with ledger_path(product_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**payload, "key": text}) + "\n")
    return text, payload


def ledger(product_id: str) -> list[dict]:
    p = ledger_path(product_id)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def export_ledger_csv(product_id: str, dest: Path) -> int:
    rows = ledger(product_id)
    with Path(dest).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["license_id", "customer", "email", "issued", "expires", "machine_id", "features", "note", "key"])
        for r in rows:
            w.writerow([r.get("lid"), r.get("cust"), r.get("email"), licensing.fmt_date(r["iat"]),
                        licensing.fmt_date(r["exp"]), r.get("mid") or "", ",".join(r.get("feat") or []),
                        r.get("note", ""), r.get("key", "")])
    return len(rows)


def inspect_license(text: str, product_id: str | None = None) -> dict:
    """Decode + (if we hold the key) verify a license; for the Verify panel."""
    payload, sig, body = licensing.decode_license(text)
    pid = product_id or payload.get("pid")
    k = load_key(pid) if pid else None
    result = {"payload": payload, "product_id": pid, "verified": None, "reason": ""}
    if not k:
        result["reason"] = f"no local key for product '{pid}' — signature not checked"
        return result
    try:
        licensing.verify_license(text, k["n"], k["e"], product_id=pid)
        result["verified"] = True
    except licensing.LicenseError as ex:
        result["verified"] = False
        result["reason"] = str(ex)
    return result

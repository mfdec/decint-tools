"""DECINT license format, verification and on-disk locations.

A license is one line of text::

    DECINT1.<base64url payload>.<base64url RSA signature>

with the payload a compact JSON object::

    {"v": 1, "pid": "my-app", "lid": "L-7F3A...", "cust": "Jane Doe",
     "email": "jane@example.com", "iat": 1757800000, "exp": 1773500000,
     "mid": null, "feat": ["pro"], "note": ""}

``pid`` must match the product the EXE was built for, ``exp`` is a unix time,
and ``mid`` (when set) pins the license to one machine. The signature covers
the exact payload bytes, so nothing in it can be edited without the vendor's
private key.

This module ships inside the customer's EXE, so it stays stdlib-only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import rsa_lite

PREFIX = "DECINT1"
COMPANY = "DECINT"
LICENSE_FILENAME = "license.lic"
EXPIRY_WARNING_DAYS = 14
# Tolerated backwards clock movement before we treat it as tampering.
CLOCK_ROLLBACK_GRACE = 36 * 3600


class LicenseError(Exception):
    """Raised by :func:`verify_license`. ``code`` is one of the ``REASONS`` keys."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or REASONS.get(code, code))
        self.code = code
        self.detail = detail


REASONS = {
    "missing": "No license was found for this product.",
    "malformed": "This is not a valid DECINT license key.",
    "bad_signature": "The license signature does not verify — the key has been "
                     "altered or was issued for a different vendor key.",
    "wrong_product": "This license was issued for a different product.",
    "expired": "This license has expired.",
    "not_yet_valid": "This license is not valid yet (check the system clock).",
    "machine_mismatch": "This license is bound to a different machine.",
    "clock_rollback": "The system clock appears to have been set backwards.",
}


# ────────────────────────────── encoding ──────────────────────────────

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_license_id() -> str:
    return "L-" + secrets.token_hex(4).upper()


def encode_license(payload: dict, n: int, d: int) -> str:
    """Vendor side: sign ``payload`` and return the one-line license text."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = rsa_lite.sign(body, n, d)
    return f"{PREFIX}.{_b64e(body)}.{_b64e(sig)}"


def clean_license_text(text: str) -> str:
    """Accept keys pasted with whitespace, line breaks or a BEGIN/END wrapper."""
    lines = [
        ln.strip() for ln in text.strip().splitlines()
        if ln.strip() and not ln.strip().startswith("-----")
    ]
    return re.sub(r"\s+", "", "".join(lines))


def decode_license(text: str) -> tuple[dict, bytes, bytes]:
    """Return ``(payload, signature, signed_bytes)`` without verifying anything."""
    text = clean_license_text(text)
    parts = text.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenseError("malformed")
    try:
        body = _b64d(parts[1])
        sig = _b64d(parts[2])
        payload = json.loads(body)
    except Exception:
        raise LicenseError("malformed")
    if not isinstance(payload, dict) or "pid" not in payload or "exp" not in payload:
        raise LicenseError("malformed")
    return payload, sig, body


def verify_license(text: str, n: int, e: int, *, product_id: str,
                   machine: str | None = None, now: float | None = None) -> dict:
    """Fully validate a license and return its payload, or raise LicenseError."""
    if not text or not text.strip():
        raise LicenseError("missing")
    payload, sig, body = decode_license(text)
    if not rsa_lite.verify(body, sig, n, e):
        raise LicenseError("bad_signature")
    if payload.get("pid") != product_id:
        raise LicenseError("wrong_product",
                           f"License is for '{payload.get('pid')}', this program is '{product_id}'.")
    now = time.time() if now is None else now
    if payload.get("iat", 0) - 86400 > now:
        raise LicenseError("not_yet_valid")
    if now > float(payload["exp"]):
        raise LicenseError("expired", f"This license expired on {fmt_date(payload['exp'])}.")
    bound = payload.get("mid")
    if bound and machine and bound != machine:
        raise LicenseError("machine_mismatch",
                           f"License is bound to machine {bound}; this machine is {machine}.")
    return payload


# ────────────────────────────── helpers ──────────────────────────────

def fmt_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(float(ts)))


def days_left(payload: dict, now: float | None = None) -> int:
    now = time.time() if now is None else now
    return int((float(payload["exp"]) - now) // 86400)


def describe(payload: dict) -> str:
    who = payload.get("cust") or "unnamed customer"
    feats = ", ".join(payload.get("feat") or []) or "standard"
    bound = f" · bound to {payload['mid']}" if payload.get("mid") else ""
    return (f"{payload.get('lid', '?')} · {who} · {feats} · "
            f"valid until {fmt_date(payload['exp'])}{bound}")


def machine_id() -> str:
    """Stable per-machine identifier, formatted ``XXXX-XXXX-XXXX-XXXX``.

    Uses the OS's own machine GUID where there is one, so reinstalling the app
    or changing the network adapter does not change it.
    """
    raw = ""
    try:
        if sys.platform == "win32":
            import winreg  # noqa: WPS433 — Windows only
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        elif sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            raw = m.group(1) if m else ""
        else:
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if os.path.exists(p):
                    raw = Path(p).read_text().strip()
                    break
    except Exception:
        raw = ""
    if not raw:
        raw = f"{uuid.getnode()}|{platform.node()}"
    h = hashlib.sha256(("DECINT|" + raw).encode()).hexdigest()[:16].upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def store_dir(product_id: str) -> Path:
    """Per-user, per-product state dir (license file, clock marker, trial start)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                    or Path.home() / "AppData" / "Local")
        d = base / COMPANY / product_id
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / COMPANY / product_id
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "decint" / product_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def license_candidates(product_id: str, exe_dir: str | os.PathLike) -> list[Path]:
    exe_dir = Path(exe_dir)
    return [
        exe_dir / f"{product_id}.lic",
        exe_dir / LICENSE_FILENAME,
        store_dir(product_id) / LICENSE_FILENAME,
    ]


def find_license(product_id: str, exe_dir: str | os.PathLike) -> str | None:
    """Env var ``DECINT_LICENSE`` (key text or a path) wins, then files."""
    env = os.environ.get("DECINT_LICENSE", "").strip()
    if env:
        if os.path.isfile(env):
            return Path(env).read_text(encoding="utf-8", errors="ignore")
        return env
    for p in license_candidates(product_id, exe_dir):
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return None


def save_license(product_id: str, text: str) -> Path:
    p = store_dir(product_id) / LICENSE_FILENAME
    p.write_text(clean_license_text(text) + "\n", encoding="utf-8")
    return p


def check_clock(product_id: str, now: float | None = None) -> None:
    """Refuse to run if the clock went back further than the grace window.

    Stores the latest time we have ever seen; the marker is per-user so it
    is only a deterrent, but it stops the trivial "set the date back" trick.
    """
    now = time.time() if now is None else now
    marker = store_dir(product_id) / "clock"
    try:
        last = float(marker.read_text().strip()) if marker.exists() else 0.0
    except (OSError, ValueError):
        last = 0.0
    if last - now > CLOCK_ROLLBACK_GRACE:
        raise LicenseError("clock_rollback")
    if now > last:
        try:
            marker.write_text(f"{now:.0f}")
        except OSError:
            pass


def trial_state(product_id: str, trial_days: int, now: float | None = None) -> tuple[bool, int]:
    """``(active, days_left)`` for a build with a free trial period."""
    if trial_days <= 0:
        return False, 0
    now = time.time() if now is None else now
    marker = store_dir(product_id) / "trial"
    try:
        start = float(marker.read_text().strip())
    except (OSError, ValueError):
        start = now
        try:
            marker.write_text(f"{now:.0f}")
        except OSError:
            pass
    left = int((start + trial_days * 86400 - now) // 86400)
    return left >= 0, max(left, 0)

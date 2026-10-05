"""The admin health report: is everything DECINT depends on working?

Two halves:

* `report()` reads what is already known, without calling anything outside:
  local checks (Tor, the IP databases, API keys and their quotas, the
  database, disk space, background jobs), the per-source tally the tools keep
  in `sourcehealth`, and the dark-web engines' own health record.
* `probe()` sends one canary query to every free outside source, plus
  VeriRoute's non-billable key check, so a source nobody has used since the
  last restart can still be seen to work. Each result lands in the same
  tally. It runs at most once a minute; asking again sooner returns the last
  run. The dark-web engines are not probed: that is ~45 Tor circuits, and
  their record already comes from every search.

The canaries are fixed public test values (test@example.com, example.com,
1.1.1.1, the SHA-1 of "password"), never a customer's query.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import __version__, db
from ..config import settings
from ..jobs import store as job_store
from . import sourcehealth

PROBE_EVERY = 60.0  # seconds between live probes
_probe: dict[str, Any] = {"at": 0.0, "probed": []}
_probe_lock = asyncio.Lock()

CANARY_EMAIL = "test@example.com"
CANARY_DOMAIN = "example.com"
CANARY_IP = "1.1.1.1"
CANARY_SHA1 = hashlib.sha1(b"password").hexdigest()
# Small and stable: certificate-log answers for example.com run long.
CANARY_CT_DOMAIN = "decint.tools"


def _iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _check(key: str, label: str, state: str, detail: str) -> dict[str, str]:
    """state: ok | warn | down | off"""
    return {"key": key, "label": label, "state": state, "detail": detail}


# ─────────────────────────── local checks ───────────────────────────

def _tor() -> dict[str, str]:
    try:
        from .darkweb import tor_status

        ok, detail = tor_status()
    except Exception as e:  # pragma: no cover
        ok, detail = False, f"check failed: {type(e).__name__}"
    return _check("tor", "Tor SOCKS proxy", "ok" if ok else "down", detail)


def _ipdb() -> dict[str, str]:
    from . import iplookup

    parts, state = [], "ok"
    for what, db_ in (("location", iplookup._CITY), ("ASN", iplookup._ASN)):
        try:
            kind = db_.kind()
        except Exception:
            kind = None
        if kind is None:
            parts.append(f"{what}: missing")
            state = "down"
            continue
        name = "GeoLite2" if kind == "geolite2" else "DB-IP Lite"
        edition = ""
        if kind == "dbip":
            file = iplookup.geo_dir() / iplookup.DBIP_FILES["city" if what == "location" else "asn"]
            have = iplookup.installed_edition(file) if file.exists() else None
            if have:
                edition = f" {have[0]}-{have[1]:02d}"
                now = datetime.now(timezone.utc)
                if (now.year * 12 + now.month) - (have[0] * 12 + have[1]) > 2 and state == "ok":
                    state = "warn"  # the monthly update has stopped arriving
        parts.append(f"{what}: {name}{edition}")
    return _check("ipdb", "IP location databases", state, " · ".join(parts))


def _abuseipdb() -> dict[str, str]:
    from . import iplookup

    if not iplookup.reputation_enabled():
        return _check("abuseipdb", "AbuseIPDB key", "off", "no IPLOOKUP_ABUSEIPDB_KEY: IP reputation is off")
    until = iplookup._abuse_pause["until"]
    if until > time.time():
        return _check("abuseipdb", "AbuseIPDB key", "warn",
                      f"daily checks used up; resumes {_iso(until)}")
    return _check("abuseipdb", "AbuseIPDB key", "ok", "set")


def _veriroute() -> dict[str, str]:
    from . import phonelookup

    if not phonelookup.configured():
        return _check("veriroute", "VeriRoute key", "off", "no PHONE_VRI_API_KEY: phone lookup answers 503")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = phonelookup._today["count"] if phonelookup._today.get("day") == today else 0
    cap = settings.phone_daily_limit
    if cap and used >= cap:
        return _check("veriroute", "VeriRoute key", "warn", f"daily cap reached ({used}/{cap}); resumes at midnight UTC")
    return _check("veriroute", "VeriRoute key", "ok",
                  f"set · {used} paid lookup(s) today" + (f" of {cap}" if cap else ""))


def _database() -> dict[str, str]:
    try:
        t = sourcehealth.Timer()
        db.get_conn().execute("SELECT 1").fetchone()
        path = db._db_path()
        size = path.stat().st_size / 1e6 if path.exists() else 0
        return _check("database", "Accounts database", "ok", f"{size:.1f} MB · answered in {t.ms:.0f} ms")
    except Exception as e:
        return _check("database", "Accounts database", "down", f"{type(e).__name__}")


def _disk() -> dict[str, str]:
    try:
        usage = shutil.disk_usage(db._db_path().parent)
    except OSError as e:
        return _check("disk", "Disk space", "warn", f"could not read: {type(e).__name__}")
    free_gb = usage.free / 1e9
    pct = usage.free / usage.total * 100 if usage.total else 0
    state = "down" if free_gb < 0.5 else "warn" if free_gb < 2 or pct < 5 else "ok"
    return _check("disk", "Disk space", state, f"{free_gb:.1f} GB free ({pct:.0f}%)")


def _jobs() -> dict[str, str]:
    counts = job_store.summary()
    busy = counts.get("running", 0) + counts.get("queued", 0)
    detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none held"
    return _check("jobs", "Background jobs", "ok", f"{busy} in flight · {detail}")


def _tor_list() -> dict[str, str]:
    from . import iplookup

    if iplookup._tor["ips"] is None:
        return _check("tor_list", "Tor exit list", "off", "not fetched yet (fetched on the first IP lookup)")
    age = (time.monotonic() - iplookup._tor["fetched"]) / 60
    state = "ok" if age < 180 else "warn"
    return _check("tor_list", "Tor exit list", state, f"{len(iplookup._tor['ips']):,} exits · fetched {age:.0f} min ago")


def _darkweb() -> list[dict[str, Any]]:
    out = []
    try:
        from .darkweb import engine_roster
    except Exception:  # pragma: no cover
        return out
    for mode in ("gateway", "tor"):
        try:
            roster = engine_roster(mode)  # type: ignore[arg-type]
        except Exception as e:
            out.append({"mode": mode, "error": type(e).__name__})
            continue
        tried = [e for e in roster if e["success_rate"] is not None]
        out.append({
            "mode": mode,
            "engines": len(roster),
            "benched": sum(1 for e in roster if e["benched"]),
            "untested": len(roster) - len(tried),
            "healthy": sum(1 for e in tried if e["success_rate"] >= 0.5 and not e["benched"]),
            "benched_names": [e["label"] or e["name"] for e in roster if e["benched"]][:12],
        })
    return out


def report() -> dict[str, Any]:
    checks = [_tor(), _database(), _disk(), _jobs(), _ipdb(), _tor_list(), _abuseipdb(), _veriroute()]
    sources = sourcehealth.snapshot()
    for s in sources:
        s["last_ok"] = _iso(s["last_ok"])
        s["last_failure"] = _iso(s["last_failure"])
    return {
        "version": __version__,
        "started_at": _iso(sourcehealth.STARTED),
        "uptime_s": int(time.time() - sourcehealth.STARTED),
        "checks": checks,
        "sources": sources,
        "darkweb": _darkweb(),
        "probed_at": _iso(_probe["at"]) if _probe["at"] else None,
        "probed": _probe["probed"],
    }


# ─────────────────────────── live probe ───────────────────────────

async def _probe_veriroute(client: httpx.AsyncClient) -> None:
    from . import phonelookup

    if not phonelookup.configured():
        return
    label = "VeriRoute Intel"
    t = sourcehealth.Timer()
    try:
        r = await client.post(
            "https://verirouteintel.com/api/v1/auth/validate-key",
            json={"api_key": settings.phone_vri_api_key.strip()},
        )
        ok = r.status_code == 200 and (r.json() or {}).get("status") == "valid"
        err = None if ok else f"key check: HTTP {r.status_code}"
    except (httpx.HTTPError, ValueError) as e:
        ok, err = False, f"key check: {type(e).__name__}"
    sourcehealth.record("phone", "veriroute", label, ok, latency_ms=t.ms if ok else None, error=err)


def _probes(client: httpx.AsyncClient) -> list[tuple[str, str, Any]]:
    """(tool, key, coroutine) for every source a canary can reach."""
    from . import domainlookup, iplookup, passwords
    from .leaks.providers import REGISTRY
    from .leaks.service import run_provider

    out: list[tuple[str, str, Any]] = []
    for key in settings.leaks_provider_list:
        p = REGISTRY.get(key)
        if p is None or key == "local":
            continue
        kind, q = ("email", CANARY_EMAIL) if p.supports("email") else ("domain", CANARY_DOMAIN)
        out.append(("leaks", key, run_provider(client, p, q, kind)))
    out.append(("passwords", "leakedpassword", passwords._leakedpassword(client, CANARY_SHA1)))
    out.append(("passwords", "hibp_range", passwords._hibp_range(client, CANARY_SHA1[:5].upper())))
    out.append(("ip", "rdap", iplookup._rdap_fetch(client, CANARY_IP)))
    if iplookup.reputation_enabled() and iplookup._abuse_pause["until"] <= time.time():
        out.append(("ip", "abuseipdb", iplookup._abuseipdb(client, CANARY_IP)))
    out.append(("phone", "veriroute", _probe_veriroute(client)))
    for url in settings.domain_doh_list:
        key, _ = domainlookup._doh_key(url)
        out.append(("domain", key, domainlookup._doh_one(client, url, CANARY_DOMAIN, "A")))
    out.append(("domain", "rdap", domainlookup._rdap_fetch(client, CANARY_DOMAIN)))
    out.append(("domain", "crtsh", domainlookup._crtsh(client, CANARY_CT_DOMAIN)))
    out.append(("domain", "certspotter", domainlookup._certspotter(client, CANARY_CT_DOMAIN)))
    if settings.domain_wayback:
        out.append(("domain", "wayback", domainlookup._wayback_edge(client, CANARY_DOMAIN, -1)))
    return out


async def probe() -> dict[str, Any]:
    """Run the canaries (or return the last run, if it was under a minute
    ago), then the report with their results folded in."""
    async with _probe_lock:
        if time.time() - _probe["at"] >= PROBE_EVERY:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
                probes = _probes(client)
                # Each source records its own result; the gather only waits.
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*(c for _, _, c in probes), return_exceptions=True),
                        timeout=45,
                    )
                except asyncio.TimeoutError:
                    pass  # whatever answered in time is recorded; the rest show as before
            _probe["at"] = time.time()
            _probe["probed"] = [f"{tool}.{key}" for tool, key, _ in probes]
    return report()

"""Phone lookup — who carries a US or Canadian number, where it is homed, what
caller ID name it shows, and whether it is reported for spam.

One source, VeriRoute Intel (verirouteintel.com), asked once per number
through its LRN endpoint with the add-ons switched on, which answers in the
objects the console shows:

* **lrn** — the Local Routing Number and `lrn_activated_at`, the date the
  number last ported to the carrier now serving it;
* **enhanced_lrn** — that carrier, its type (wireless, landline, VoIP), and the
  rate center, city, county, state, ZIP and UTC offset the number is homed in;
* **messaging** — the provider that receives its texts;
* **cnam** — the caller ID name carriers publish for it. Businesses and many
  landlines carry a real name; most mobiles read WIRELESS CALLER;
* **trust** — a 0-100 reputation score from complaint data, with the spam,
  robocall and scam verdicts behind it.

VeriRoute is paid per lookup from a prepaid wallet, so this module guards the
spend: a number that cannot exist on the North American plan is refused before
anything is sent, an answer is reused for PHONE_CACHE_TTL, each metered account
has PHONE_MONTHLY_LIMIT lookups a month, and the site stops calling out after
PHONE_DAILY_LIMIT paid lookups in a UTC day.

The number goes to VeriRoute and nowhere else. It is held in memory with its
answer for the cache's lifetime and never written to disk or logged.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import db
from ..config import settings
from ..models import PhoneEnhancedLrn, PhoneLookupResponse, PhoneMessaging, PhoneTrust
from . import usage

log = logging.getLogger("decint.phonelookup")

_UA = {"User-Agent": "decint-tools/1.0 (phone lookup)"}

# Toll-free area codes: no carrier location to speak of, and no CNAM.
TOLL_FREE = {"800", "833", "844", "855", "866", "877", "888"}

_KEYPAD = {c: str(d) for d, letters in {
    2: "ABC", 3: "DEF", 4: "GHI", 5: "JKL", 6: "MNO", 7: "PQRS", 8: "TUV", 9: "WXYZ",
}.items() for c in letters}

_EXTENSION = re.compile(r"\s*(?:ext\.?|extension|x|#)\s*\d{1,6}\s*$", re.I)

_CACHE_MAX = 2000
_cache: dict[str, tuple[float, PhoneLookupResponse]] = {}
# Paid lookups sent today (UTC), against PHONE_DAILY_LIMIT. In memory, so a
# restart starts the day's count again; the monthly per-account cap is the
# one that persists.
_today: dict[str, Any] = {"day": "", "count": 0}


class LookupFailed(Exception):
    """VeriRoute gave no answer. `status` is what the API should return."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class CapReached(Exception):
    """A spend guard is exhausted. The message says which, for the console."""


# ─────────────────────────── the number ───────────────────────────

def parse_number(raw: str) -> str:
    """The 11-digit form VeriRoute takes ("1" + area code + number), or
    ValueError saying what is wrong with what was typed.

    Takes what people paste: +1 (336) 408-6644, 336.408.6644, 13364086644,
    tel:+13364086644, an extension (dropped), and keypad letters
    (1-800-FLOWERS)."""
    v = raw.strip()
    if v.lower().startswith("tel:"):
        v = v[4:]
    v = _EXTENSION.sub("", v)
    if not v:
        raise ValueError("Enter a phone number.")
    if re.search(r"[^0-9A-Za-z+().\-\s/]", v):
        raise ValueError("That doesn't look like a phone number.")

    intl = v.startswith("+") and not re.match(r"^\+\s*1", v)  # +1 is the whole plan
    digits = re.sub(r"\D", "", "".join(_KEYPAD.get(c.upper(), c) for c in v))
    if intl or digits.startswith(("00", "011")):
        raise ValueError(
            "Only US and Canadian (+1) numbers can be looked up: the routing and "
            "caller ID databases behind this tool cover the North American plan."
        )
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or digits[0] != "1":
        raise ValueError("A US or Canadian number has 10 digits: a 3-digit area code and 7 digits.")

    area, exchange, line = digits[1:4], digits[4:7], digits[7:]
    if area[0] in "01":
        raise ValueError(f"{area} is not an area code: area codes never start with 0 or 1.")
    if area[1:] == "11":
        raise ValueError(f"{area} is a service code like 911, not an area code.")
    if exchange[0] in "01":
        raise ValueError(f"No number in ({area}) starts with {exchange}: exchanges never start with 0 or 1.")
    if exchange[1:] == "11":
        raise ValueError(f"{exchange} is a service code like 411, not an exchange.")
    if exchange == "555" and line.startswith("01"):
        raise ValueError("555-01xx numbers are reserved for films and TV; none is ever assigned.")
    return digits


def e164(number: str) -> str:
    return "+" + number


def national(number: str) -> str:
    return f"({number[1:4]}) {number[4:7]}-{number[7:]}"


def line_type(number: str, carrier_type: str | None, given: str | None = None) -> str:
    """mobile / landline / voip / toll_free / unknown, from the carrier's type."""
    if number[1:4] in TOLL_FREE:
        return "toll_free"
    if given in ("mobile", "landline", "voip"):
        return given
    t = (carrier_type or "").upper()
    if any(k in t for k in ("WIRELESS", "WRS", "PCS", "CELL", "MOBILE")):
        return "mobile"
    if any(k in t for k in ("VOIP", "IPES", "VOICE OVER")):
        return "voip"
    if any(k in t for k in ("LANDLINE", "WIRELINE", "ILEC", "CLEC", "RBOC", "FIXED")):
        return "landline"
    return "unknown"


# ─────────────────────────── spend guards ───────────────────────────

def configured() -> bool:
    return bool(settings.phone_vri_api_key.strip())


def _month() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _metered(user: dict) -> bool:
    # Whoever the search allowance leaves unmetered (staff, Enterprise) is
    # left out of this cap too.
    return settings.phone_monthly_limit > 0 and usage.limit_for(user) is not None


def take_monthly(user: dict) -> None:
    """One phone lookup against the account's month, or CapReached. The
    increment is the check, as in usage.consume."""
    if not _metered(user):
        return
    month, now = _month(), datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        "INSERT OR IGNORE INTO phone_counters (user_id, month, count, updated_at) VALUES (?,?,0,?)",
        (user["id"], month, now),
    )
    changed = db.execute(
        "UPDATE phone_counters SET count = count + 1, updated_at = ? "
        "WHERE user_id = ? AND month = ? AND count < ?",
        (now, user["id"], month, settings.phone_monthly_limit),
    )
    if not changed:
        n = settings.phone_monthly_limit
        raise CapReached(
            f"You've used this month's {n} phone lookup{'s' if n != 1 else ''}. "
            "They reset on the 1st."
        )


def refund_monthly(user: dict) -> None:
    if not _metered(user):
        return
    db.execute(
        "UPDATE phone_counters SET count = MAX(count - 1, 0), updated_at = ? "
        "WHERE user_id = ? AND month = ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), user["id"], _month()),
    )


def monthly_used(user: dict) -> int:
    row = db.one(
        "SELECT count FROM phone_counters WHERE user_id = ? AND month = ?",
        (user["id"], _month()),
    )
    return int(row["count"]) if row else 0


def _reserve_daily() -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    if _today["day"] != day:
        _today.update(day=day, count=0)
    if settings.phone_daily_limit > 0 and _today["count"] >= settings.phone_daily_limit:
        raise CapReached("Phone lookups are paused until midnight UTC: today's lookup budget is spent.")
    _today["count"] += 1


def _release_daily() -> None:
    _today["count"] = max(0, _today["count"] - 1)


# ─────────────────────────── the lookup ───────────────────────────

def requested() -> list[str]:
    out = ["lrn", "enhanced_lrn"]
    if settings.phone_include_messaging:
        out.append("messaging")
    if settings.phone_include_cnam:
        out.append("cnam")
    if settings.phone_include_trust:
        out.append("trust")
    return out


def attribution() -> list[str]:
    return [
        "Number data: VeriRoute Intel (verirouteintel.com) — routing, carrier, "
        "caller ID name, messaging provider and reputation for US and Canadian numbers.",
    ]


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _pick(model: type, data: Any):
    if not isinstance(data, dict) or not data:
        return None
    fields = model.model_fields
    clean = {k: v for k, v in data.items() if k in fields and v not in (None, "")}
    return model(**clean) if clean else None


def parse_answer(number: str, query: str, data: dict) -> PhoneLookupResponse:
    """VeriRoute's LRN answer as the console's model. Field names are kept."""
    cnam = data.get("cnam")
    if isinstance(cnam, dict):  # the standalone CNAM endpoint's shape
        cnam = cnam.get("caller_name") or cnam.get("cnam")
    enhanced = _pick(PhoneEnhancedLrn, data.get("enhanced_lrn"))
    trust_raw = data.get("trust")
    trust = _pick(PhoneTrust, trust_raw)
    if trust and isinstance(trust_raw, dict) and isinstance(trust_raw.get("reputation_score"), float):
        trust.reputation_score = round(trust_raw["reputation_score"])
    return PhoneLookupResponse(
        query=query,
        phone_number=number,
        e164=e164(number),
        national=national(number),
        lrn=_text(data.get("lrn")),
        lrn_activated_at=_text(data.get("lrn_activated_at")),
        line_type=line_type(number, enhanced.carrier_type if enhanced else None,
                            _text(data.get("line_type"))),
        cnam=_text(cnam),
        enhanced_lrn=enhanced,
        messaging=_pick(PhoneMessaging, data.get("messaging")),
        trust=trust,
        looked_up_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        requested=requested(),
        raw=data,
        attribution=attribution(),
    )


def _upstream_error(r: httpx.Response) -> LookupFailed:
    try:
        body = r.json()
    except ValueError:
        body = {}
    said = ""
    if isinstance(body, dict):
        errs = body.get("errors")
        said = body.get("error") or body.get("details") or (errs[0] if isinstance(errs, list) and errs else "")
    log.warning("veriroute answered %s: %s", r.status_code, str(said)[:200])
    if r.status_code == 400:
        return LookupFailed(f"VeriRoute refused the number: {said or 'invalid format'}.", 400)
    if r.status_code in (401, 403):
        return LookupFailed("Phone lookup is misconfigured on this server: the provider refused its API key.", 503)
    if r.status_code == 402:
        return LookupFailed("Phone lookup is paused: the lookup credit has run out. You were not charged.", 503)
    if r.status_code == 429:
        return LookupFailed("VeriRoute is rate-limiting lookups right now. Try again in a minute.", 503)
    return LookupFailed(f"VeriRoute did not answer (HTTP {r.status_code}).", 502)


async def lookup(number: str, query: str) -> PhoneLookupResponse:
    """Look the number up, from the cache when it was asked recently."""
    hit = _cache.get(number)
    if hit and time.monotonic() - hit[0] < settings.phone_cache_ttl:
        return hit[1].model_copy(update={"query": query, "cached": True})

    _reserve_daily()
    body: dict[str, Any] = {
        "phone_number": number,
        "include_enhanced_lrn": True,
        "messaging_lookup": settings.phone_include_messaging,
        "include_cnam": settings.phone_include_cnam,
        "include_trust": settings.phone_include_trust,
    }
    headers = {**_UA, "Authorization": f"Bearer {settings.phone_vri_api_key.strip()}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.phone_timeout)) as client:
            r = await client.post(settings.phone_vri_url, json=body, headers=headers)
    except httpx.TimeoutException:
        _release_daily()
        raise LookupFailed("VeriRoute timed out.", 504) from None
    except httpx.HTTPError as e:
        _release_daily()
        raise LookupFailed(f"VeriRoute could not be reached ({type(e).__name__}).") from None

    if r.status_code != 200:
        _release_daily()
        raise _upstream_error(r)
    try:
        data = r.json()
    except ValueError:
        _release_daily()
        raise LookupFailed("VeriRoute sent an answer that isn't JSON.") from None
    if not isinstance(data, dict) or (not data.get("lrn") and data.get("error")):
        _release_daily()
        raise LookupFailed(f"VeriRoute had no answer: {data.get('error') if isinstance(data, dict) else 'empty'}.")

    res = parse_answer(number, query, data)
    if len(_cache) >= _CACHE_MAX:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 4]:
            _cache.pop(k, None)
    _cache[number] = (time.monotonic(), res)
    return res

"""Admin "Data" hub — every dataset the app already holds, in one place.

Nothing here collects anything new about a person. Each dataset is a read over
tables the app already keeps (visits, users, billing, audit, tickets, usage
counters) plus `metrics_daily`, a rollup of daily *aggregates* so the long-run
trend outlives the raw rows that retention prunes. What people search for is
still never stored — see services/usage.py — so there is no "queries" dataset.

Every dataset returns the same shape, which is what lets the admin UI render all
of them with one component and lets a new dataset be a single function here:

    {
      "id", "title", "generated_at", "days", "notes": [str],
      "kpis":   [{"label", "value", "fmt", "sub"?, "tone"?}],
      "charts": [{"title", "kind": "bars"|"line"|"hbars", "fmt", "points", "note"?}],
      "tables": [{"key", "title", "columns": [{"key","label","kind"}], "rows", "note"?}],
    }

`fmt` / column `kind` tell the client how to print a value (int, money = cents,
pct, hours, bytes, ts, date, mono, badge, text). Money is always integer cents.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import statistics
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .. import db
from ..config import settings
from . import analytics
from . import usage as usage_svc
from . import users as users_svc
from .billing import plans
from .billing import store as billing_store

log = logging.getLogger("decint.datahub")


# ─────────────────────────── request parameters ───────────────────────────

@dataclass(frozen=True)
class Params:
    days: int = 30
    q: str = ""
    bots: bool = False
    limit: int = 200


# ─────────────────────────── small helpers ───────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _day_axis(days: int) -> list[str]:
    """The last `days` UTC dates, oldest first, today included."""
    today = _now().date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def _start(days: int) -> str:
    """Window start as an ISO timestamp, so counts and charts cover exactly the
    same days. Stored timestamps are ISO-8601 UTC, which compare correctly as
    strings against this."""
    return _day_axis(days)[0] + "T00:00:00"


def _scalar(sql: str, params: tuple = ()) -> float:
    row = db.one(sql, params)
    if not row:
        return 0
    v = next(iter(row.values()))
    return v if v is not None else 0


def _kpi(label: str, value: Any, fmt: str = "int", sub: str | None = None,
         tone: str | None = None) -> dict:
    k: dict[str, Any] = {"label": label, "value": value, "fmt": fmt}
    if sub:
        k["sub"] = sub
    if tone:
        k["tone"] = tone
    return k


def _col(key: str, label: str, kind: str = "text") -> dict:
    return {"key": key, "label": label, "kind": kind}


def _table(key: str, title: str, columns: list[dict], rows: list[dict],
           note: str | None = None) -> dict:
    t: dict[str, Any] = {"key": key, "title": title, "columns": columns, "rows": rows}
    if note:
        t["note"] = note
    return t


def _series(title: str, kind: str, axis: list[str], rows: list[dict],
            fmt: str = "int", note: str | None = None, gaps: bool = False) -> dict:
    """A value per day. `rows` are {d, n}; days with no row are 0 (a flow) or a
    gap (a point-in-time snapshot, where "no snapshot" is not "zero")."""
    got = {r["d"]: r["n"] for r in rows}
    pts = [{"x": d, "y": got.get(d, None if gaps else 0)} for d in axis]
    c: dict[str, Any] = {"title": title, "kind": kind, "fmt": fmt, "points": pts}
    if note:
        c["note"] = note
    return c


def _hbars(title: str, rows: list[tuple[str, float]], fmt: str = "int",
           total: float | None = None, note: str | None = None) -> dict:
    """A ranked list of categories. When `total` is given the tail is folded
    into a single "Other" row rather than inventing more categories."""
    pts = [{"label": str(k), "value": v} for k, v in rows if v]
    if total is not None:
        rest = total - sum(p["value"] for p in pts)
        if rest > 0:
            pts.append({"label": "Other", "value": rest, "muted": True})
    denom = total if total else (sum(p["value"] for p in pts) or 1)
    for p in pts:
        p["share"] = round(p["value"] / denom * 100, 1) if denom else 0
    c: dict[str, Any] = {"title": title, "kind": "hbars", "fmt": fmt, "points": pts}
    if note:
        c["note"] = note
    return c


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _like(q: str) -> str:
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q}%"


_TOKENISH = re.compile(r"^[A-Za-z0-9_\-.]{24,}$")


def _clean_path(path: str | None) -> str:
    """Paths come from the visitor's browser. A segment that looks like a
    credential (long, unbroken, token-ish) is collapsed so it can never be
    echoed back onto an admin screen."""
    if not path:
        return "(none)"
    segs = [":token" if _TOKENISH.match(s) else s for s in path.split("/")]
    out = "/".join(segs)
    return out[:80]


# ─────────────────────────── paying accounts (shared) ───────────────────────────

def _paying_accounts() -> list[dict]:
    """End-users on a paid plan they actually paid for, with a monthly value.

    The value is what the account's latest paid order works out to per month
    (a yearly order of $49.50 is $4.13), falling back to the plan's list price.
    Same population as billing_store.paying_customers(), so the two agree."""
    marks = ",".join("?" for _ in billing_store._NON_SALE_SOURCES)
    rows = db.query(
        "SELECT u.id, u.email, e.tier, e.source, e.expires_at, "
        " (SELECT o.amount_cents * 1.0 / NULLIF(o.months, 0) FROM billing_orders o "
        "   WHERE o.user_id = e.user_id AND o.status = 'paid' "
        "   ORDER BY o.paid_at DESC, o.id DESC LIMIT 1) AS monthly_cents, "
        " (SELECT 1 FROM subscriptions s WHERE s.user_id = e.user_id "
        "   AND s.status IN ('trialing','active','past_due') "
        "   AND s.cancel_at_period_end = 0 LIMIT 1) AS renews "
        "FROM entitlements e JOIN users u ON u.id = e.user_id "
        f"WHERE u.role = 'user' AND e.tier != ? AND e.source NOT IN ({marks}) "
        "AND (e.expires_at IS NULL OR e.expires_at > ?) "
        "ORDER BY (e.expires_at IS NULL), e.expires_at",
        (plans.FREE_PLAN.key, *billing_store._NON_SALE_SOURCES, _iso(_now())),
    )
    for r in rows:
        if r["monthly_cents"] is None:
            plan = plans.get(r["tier"])
            r["monthly_cents"] = plan.monthly_cents if plan else 0
        r["monthly_cents"] = int(round(r["monthly_cents"]))
        r["renews"] = bool(r["renews"])
    return rows


# ═══════════════════════════ datasets ═══════════════════════════

# ── overview ──

def _overview(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    users_total = int(_scalar("SELECT COUNT(*) FROM users"))
    new_users = int(_scalar(
        "SELECT COUNT(*) FROM users WHERE role='user' AND created_at >= ?", (start,)))
    pv = int(_scalar(
        "SELECT COUNT(*) FROM visits WHERE ts >= ? AND is_bot = 0 AND event='pageview'",
        (start,)))
    visitor_days = int(_scalar(
        "SELECT SUM(n) FROM (SELECT COUNT(DISTINCT visitor_id) AS n FROM visits "
        "WHERE ts >= ? AND is_bot = 0 GROUP BY substr(ts,1,10))", (start,)))
    paying = _paying_accounts()
    mrr = sum(r["monthly_cents"] for r in paying)
    open_tickets = int(_scalar("SELECT COUNT(*) FROM tickets WHERE status = 'open'"))
    blocked = int(_scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action='usage.blocked' AND ts >= ?", (start,)))
    failed = int(_scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action IN ('login.failed','login_token.failed') "
        "AND ts >= ?", (start,)))
    revenue = int(_scalar(
        "SELECT COALESCE(SUM(amount_cents),0) FROM billing_orders "
        "WHERE status='paid' AND paid_at >= ?", (start,)))

    def m(metric: str, title: str, kind: str = "bars", fmt: str = "int") -> dict:
        return _series(title, kind, axis, _metric_rows(metric, axis[0]), fmt)

    return {
        "kpis": [
            _kpi("Accounts", users_total, sub=f"{new_users} new in {p.days}d"),
            _kpi("Visitors per day", round(visitor_days / p.days, 1), "float",
                 sub=f"{pv:,} pageviews · {p.days}d"),
            _kpi("Paying accounts", len(paying), tone="ok" if paying else None),
            _kpi("Est. MRR", mrr, "money", sub=f"{_money(revenue)} collected in {p.days}d"),
            _kpi("Paywall hits", blocked, sub=f"free-trial users turned away · {p.days}d",
                 tone="warn" if blocked else None),
            _kpi("Failed sign-ins", failed, sub=f"{p.days}d", tone="warn" if failed > 25 else None),
            _kpi("Open tickets", open_tickets, tone="warn" if open_tickets else None),
        ],
        "charts": [
            m("visitors", "Visitors per day"),
            m("signups", "New accounts per day"),
            m("revenue_cents", "Revenue per day", fmt="money"),
            m("paywall_hits", "Paywall hits per day"),
        ],
        "tables": [],
        "notes": [],
    }


def _money(cents: float) -> str:
    return f"${cents / 100:,.2f}"


# ── traffic ──

def _traffic(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    base = "ts >= ?" + ("" if p.bots else " AND is_bot = 0")
    bp = (start,)

    pv = int(_scalar(f"SELECT COUNT(*) FROM visits WHERE {base} AND event='pageview'", bp))
    events = int(_scalar(f"SELECT COUNT(*) FROM visits WHERE {base}", bp))
    sessions = int(_scalar(
        f"SELECT COUNT(DISTINCT session_id) FROM visits WHERE {base} AND session_id IS NOT NULL", bp))
    visitor_days = int(_scalar(
        "SELECT SUM(n) FROM (SELECT COUNT(DISTINCT visitor_id) AS n FROM visits "
        f"WHERE {base} GROUP BY substr(ts,1,10))", bp))
    bounce = db.one(
        "SELECT COUNT(*) AS sessions, SUM(c = 1) AS bounced FROM ("
        f" SELECT COUNT(*) AS c FROM visits WHERE {base} AND event='pageview' "
        " AND session_id IS NOT NULL GROUP BY session_id)", bp) or {}
    bounced, sess_pv = int(bounce.get("bounced") or 0), int(bounce.get("sessions") or 0)

    per_day_pv = db.query(
        f"SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM visits WHERE {base} "
        "AND event='pageview' GROUP BY d", bp)
    per_day_vis = db.query(
        f"SELECT substr(ts,1,10) AS d, COUNT(DISTINCT visitor_id) AS n FROM visits "
        f"WHERE {base} GROUP BY d", bp)
    hours = {r["h"]: r["n"] for r in db.query(
        f"SELECT substr(ts,12,2) AS h, COUNT(*) AS n FROM visits WHERE {base} "
        "AND event='pageview' GROUP BY h", bp)}

    # Top pages, with credential-looking path segments collapsed.
    pages: dict[str, int] = {}
    for r in db.query(
            f"SELECT path, COUNT(*) AS n FROM visits WHERE {base} AND event='pageview' "
            "GROUP BY path ORDER BY n DESC LIMIT 60", bp):
        k = _clean_path(r["path"])
        pages[k] = pages.get(k, 0) + r["n"]

    # Acquisition is read from each session's *first* pageview. A single-page
    # app reports the same document.referrer for every later view, so counting
    # every pageview would credit one Reddit click once per page.
    first = db.query(
        "SELECT v.path, v.referrer, v.query FROM visits v JOIN ("
        f" SELECT MIN(id) AS mid FROM visits WHERE {base} AND event='pageview' "
        " AND session_id IS NOT NULL GROUP BY session_id) f ON f.mid = v.id LIMIT 20000", bp)
    own = (urlparse(settings.public_base_url or "").hostname or "").lower()
    entry: dict[str, int] = {}
    refs: dict[str, int] = {}
    camps: dict[tuple, int] = {}
    for r in first:
        k = _clean_path(r["path"])
        entry[k] = entry.get(k, 0) + 1
        label = _referrer_label(r["referrer"], own)
        refs[label] = refs.get(label, 0) + 1
        # Only these three keys are ever read from the query string. The rest
        # of it can hold one-time tokens (activation and reset links) and must
        # never reach this screen.
        qs = parse_qs(r["query"] or "")
        utm = tuple((qs.get(f"utm_{k2}", [""])[0] or "")[:40] for k2 in ("source", "medium", "campaign"))
        if any(utm):
            camps[utm] = camps.get(utm, 0) + 1

    clicks: dict[tuple, int] = {}
    for r in db.query(
            f"SELECT meta, COUNT(*) AS n FROM visits WHERE {base} AND event='click' "
            "AND meta IS NOT NULL AND COALESCE(path,'') NOT LIKE '/admin%' "
            "AND COALESCE(path,'') NOT LIKE '/console%' GROUP BY meta ORDER BY n DESC LIMIT 80", bp):
        try:
            meta = json.loads(r["meta"])
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        text = str(meta.get("text") or "").strip().split("\n")[0][:48]
        href = str(meta.get("href") or "")[:60]
        key = (text or href or "(unlabelled)", href)
        clicks[key] = clicks.get(key, 0) + r["n"]

    ev_types = [(r["label"], r["n"]) for r in db.query(
        f"SELECT event AS label, COUNT(*) AS n FROM visits WHERE {base} "
        "GROUP BY event ORDER BY n DESC", bp)]

    def uniq(path: str) -> int:
        return int(_scalar(
            f"SELECT COUNT(DISTINCT visitor_id) FROM visits WHERE {base} "
            "AND event='pageview' AND path = ?", (*bp, path)))

    signups = int(_scalar(
        "SELECT COUNT(*) FROM users WHERE role='user' AND created_at >= ?", (start,)))
    activated = int(_scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action='account.activated' AND ts >= ?", (start,)))
    paid = int(_scalar(
        "SELECT COUNT(DISTINCT user_id) FROM billing_orders WHERE status='paid' AND paid_at >= ?",
        (start,)))
    # The first two rows are reference audiences, not stages: people reach
    # sign-up without passing pricing, so a "% of previous" there can top 100%.
    # The percentages start at sign-up, where every later step is a true subset.
    steps = [
        ("Home page visitors", uniq("/"), False),
        ("Pricing page visitors", uniq("/pricing"), False),
        ("Opened sign-up", uniq("/signup"), True),
        ("Created an account", signups, True),
        ("Activated it", activated, True),
        ("Paid", paid, True),
    ]
    funnel, prev, head = [], None, None
    for name, n, strict in steps:
        row = {"step": name, "count": n, "of_prev": None, "of_first": None}
        if strict:
            if head is None:
                head = n
            else:
                row["of_prev"] = _pct(n, prev)
                row["of_first"] = _pct(n, head)
            prev = n
        funnel.append(row)

    notes = []
    if settings.analytics_id_mode == "rotating":
        notes.append(
            "Visitor IDs rotate daily by design (no cookie, no cross-day tracking), so "
            "“visitor-days” counts someone who returns on two days twice. "
            "Per-day charts are exact.")
    if not p.bots:
        notes.append("Known bots and crawlers are excluded. Turn on “Include bots” to see everything.")

    return {
        "kpis": [
            _kpi("Pageviews", pv),
            _kpi("Visitor-days", visitor_days, sub=f"≈ {visitor_days / p.days:,.1f} per day"),
            _kpi("Sessions", sessions, sub=f"{pv / sessions:.1f} pages each" if sessions else None),
            _kpi("Bounce rate", _pct(bounced, sess_pv), "pct", sub=f"{bounced} of {sess_pv} sessions"),
            _kpi("All events", events, sub="clicks, outbound, downloads…"),
        ],
        "charts": [
            _series("Pageviews per day", "bars", axis, per_day_pv),
            _series("Visitors per day", "line", axis, per_day_vis),
            {"title": "Pageviews by hour of day (UTC)", "kind": "bars", "fmt": "int",
             "points": [{"x": f"{h:02d}", "y": hours.get(f"{h:02d}", 0)} for h in range(24)]},
            _hbars("Top pages", sorted(pages.items(), key=lambda kv: -kv[1])[:10], total=pv),
            _hbars("Entry pages (sessions)", sorted(entry.items(), key=lambda kv: -kv[1])[:10],
                   total=len(first)),
            _hbars("Where sessions come from", sorted(refs.items(), key=lambda kv: -kv[1])[:10],
                   total=len(first)),
            _hbars("Events by type", ev_types),
        ],
        "tables": [
            _table("funnel", "Acquisition funnel",
                   [_col("step", "Step"), _col("count", "Count", "int"),
                    _col("of_prev", "of previous step", "pct"), _col("of_first", "of sign-up openers", "pct")],
                   funnel,
                   note="Page rows count distinct visitor-days; the last three count accounts."),
            _table("campaigns", "Campaigns (utm_*)",
                   [_col("source", "Source"), _col("medium", "Medium"),
                    _col("campaign", "Campaign"), _col("sessions", "Sessions", "int")],
                   [{"source": s or "—", "medium": m or "—", "campaign": c or "—", "sessions": n}
                    for (s, m, c), n in sorted(camps.items(), key=lambda kv: -kv[1])[:25]],
                   note="Empty until links carry utm_source / utm_medium / utm_campaign."),
            _table("clicks", "Most-clicked elements (public pages)",
                   [_col("element", "Element"), _col("href", "Links to", "mono"),
                    _col("clicks", "Clicks", "int")],
                   [{"element": t, "href": h or "—", "clicks": n}
                    for (t, h), n in sorted(clicks.items(), key=lambda kv: -kv[1])[:20]],
                   note="Console and admin clicks are left out."),
        ],
        "notes": notes,
    }


def _referrer_label(ref: str | None, own_host: str) -> str:
    if not ref:
        return "Direct / none"
    u = urlparse(ref)
    if u.scheme == "android-app":
        return f"{u.netloc} (Android app)"
    host = (u.hostname or "").lower().removeprefix("www.")
    if not host:
        return "Direct / none"
    if own_host and (host == own_host.removeprefix("www.") or host.endswith("." + own_host)):
        return "Internal"
    return host


# ── audience ──

def _audience(p: Params) -> dict:
    start = _start(p.days)
    base = "ts >= ?" + ("" if p.bots else " AND is_bot = 0")
    bp = (start,)
    total = int(_scalar(f"SELECT COUNT(DISTINCT visitor_id) FROM visits WHERE {base}", bp))

    def top(title: str, expr: str, n: int = 8, where: str = "", fmt_label=None,
            note: str | None = None) -> dict:
        rows = db.query(
            f"SELECT {expr} AS label, COUNT(DISTINCT visitor_id) AS n FROM visits "
            f"WHERE {base}{where} GROUP BY label ORDER BY n DESC LIMIT {int(n)}", bp)
        pairs = [((fmt_label(r["label"]) if fmt_label else r["label"]), r["n"]) for r in rows]
        # No rows means "no data", not "everything is Other".
        return _hbars(title, pairs, total=total if pairs else None, note=note)

    known = " AND {c} IS NOT NULL AND {c} != ''"
    unk = "COALESCE(NULLIF({c},''),'unknown')"

    browser_versions = db.query(
        "SELECT COALESCE(browser,'unknown') AS browser, "
        " COALESCE(substr(browser_version,1,instr(browser_version||'.','.')-1),'') AS major, "
        f" COUNT(DISTINCT visitor_id) AS n FROM visits WHERE {base} "
        "GROUP BY browser, major ORDER BY n DESC LIMIT 20", bp)

    geo_on = False
    analytics._load_geo()
    geo_on = analytics._geo_reader is not None
    with_country = int(_scalar(f"SELECT COUNT(*) FROM visits WHERE {base} AND country IS NOT NULL", bp))
    all_ev = int(_scalar(f"SELECT COUNT(*) FROM visits WHERE {base}", bp))

    mobile = int(_scalar(
        f"SELECT COUNT(DISTINCT visitor_id) FROM visits WHERE {base} AND device_type IN ('mobile','tablet')", bp))
    bots_all = int(_scalar("SELECT COUNT(*) FROM visits WHERE ts >= ? AND is_bot = 1", bp))
    ev_all = int(_scalar("SELECT COUNT(*) FROM visits WHERE ts >= ?", bp))
    top_browser = db.one(
        f"SELECT browser AS label, COUNT(DISTINCT visitor_id) AS n FROM visits WHERE {base} "
        "AND browser IS NOT NULL GROUP BY browser ORDER BY n DESC LIMIT 1", bp)
    top_os = db.one(
        f"SELECT os AS label, COUNT(DISTINCT visitor_id) AS n FROM visits WHERE {base} "
        "AND os IS NOT NULL GROUP BY os ORDER BY n DESC LIMIT 1", bp)

    notes = []
    if not geo_on:
        notes.append(
            "No GeoLite2 database is installed, so country, region, city and network "
            "fields are empty. Put GeoLite2-City.mmdb and GeoLite2-ASN.mmdb in "
            "backend/data/geoip/ and restart to fill them. Browser timezone below is a "
            "usable stand-in until then.")
    if settings.analytics_id_mode == "rotating":
        notes.append("Counts are visitor-days: IDs rotate daily, so a returning visitor counts once per day.")

    return {
        "kpis": [
            _kpi("Visitor-days", total),
            _kpi("Mobile / tablet", _pct(mobile, total), "pct", sub=f"{mobile} visitor-days"),
            _kpi("Top browser", top_browser["label"] if top_browser else None, "text",
                 sub=f"{_pct(top_browser['n'], total)}% of visitors" if top_browser and total else None),
            _kpi("Top OS", top_os["label"] if top_os else None, "text",
                 sub=f"{_pct(top_os['n'], total)}% of visitors" if top_os and total else None),
            _kpi("Bot share", _pct(bots_all, ev_all), "pct", sub=f"{bots_all} of {ev_all} events",
                 tone="warn" if ev_all and bots_all / ev_all > 0.25 else None),
            _kpi("Geo coverage", _pct(with_country, all_ev), "pct",
                 sub="events with a country" if geo_on else "geo database not installed",
                 tone=None if geo_on else "warn"),
        ],
        "charts": [
            top("Browsers", unk.format(c="browser")),
            top("Operating systems", unk.format(c="os")),
            top("Device type", unk.format(c="device_type")),
            top("Screen size", "screen_w || ' × ' || screen_h", 10, known.format(c="screen_w")),
            top("Language", unk.format(c="language"), 8),
            top("Browser timezone", unk.format(c="tz_client"), 10),
            top("Countries", "country", 10, known.format(c="country"),
                note=None if geo_on else "Needs the GeoLite2 database."),
            top("Networks (ASN)", "COALESCE(org, asn)", 10, " AND COALESCE(org, asn) IS NOT NULL",
                note=None if geo_on else "Needs the GeoLite2 database."),
            top("CPU cores", "cpu_cores", 8, known.format(c="cpu_cores"), lambda v: f"{v} cores"),
            top("Device memory", "device_memory", 6, known.format(c="device_memory"),
                lambda v: f"{v:g} GB", note="The browser caps this at 8 GB."),
            top("Connection", unk.format(c="connection"), 6),
            top("Touch input", "CASE WHEN touch_points > 0 THEN 'Touch' ELSE 'No touch' END", 2),
        ],
        "tables": [
            _table("browser_versions", "Browser versions (major)",
                   [_col("browser", "Browser"), _col("major", "Version"), _col("n", "Visitor-days", "int")],
                   [{"browser": r["browser"], "major": r["major"] or "—", "n": r["n"]}
                    for r in browser_versions],
                   note="Useful for deciding which browsers the site has to keep working in."),
        ],
        "notes": notes,
    }


# ── users ──

def _users(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    marks = ",".join("?" for _ in billing_store._NON_SALE_SOURCES)
    now_iso = _iso(_now())

    total = int(_scalar("SELECT COUNT(*) FROM users"))
    end_users = int(_scalar("SELECT COUNT(*) FROM users WHERE role='user'"))
    new_users = int(_scalar("SELECT COUNT(*) FROM users WHERE role='user' AND created_at >= ?", (start,)))
    active = int(_scalar("SELECT COUNT(*) FROM users WHERE role='user' AND last_login_at >= ?", (start,)))
    activated = int(_scalar("SELECT COUNT(*) FROM users WHERE role='user' AND status='active'"))
    mfa = int(_scalar(
        "SELECT COUNT(*) FROM users WHERE role='user' AND (totp_enabled OR email_otp_enabled OR sms_otp_enabled)"))
    never = int(_scalar(
        "SELECT COUNT(*) FROM users WHERE role='user' AND status='active' AND last_login_at IS NULL"))

    cohort = db.one(
        "SELECT COUNT(*) AS signed_up, SUM(u.status='active') AS active, "
        " SUM(u.last_login_at IS NOT NULL) AS logged_in, "
        " SUM(EXISTS(SELECT 1 FROM usage_counters c WHERE c.user_id=u.id AND c.count>0)) AS searched, "
        f" SUM(EXISTS(SELECT 1 FROM entitlements e WHERE e.user_id=u.id AND e.tier!='free' "
        f"   AND e.source NOT IN ({marks}) AND (e.expires_at IS NULL OR e.expires_at > ?))) AS paying "
        "FROM users u WHERE u.role='user' AND u.created_at >= ?",
        (*billing_store._NON_SALE_SOURCES, now_iso, start)) or {}
    steps = [
        ("Signed up", int(cohort.get("signed_up") or 0)),
        ("Account active", int(cohort.get("active") or 0)),
        ("Signed in", int(cohort.get("logged_in") or 0)),
        ("Ran a search", int(cohort.get("searched") or 0)),
        ("Paying", int(cohort.get("paying") or 0)),
    ]
    funnel = [{"step": n, "count": c,
               "of_prev": _pct(c, steps[i - 1][1]) if i else None,
               "of_first": _pct(c, steps[0][1]) if i else None}
              for i, (n, c) in enumerate(steps)]

    signups = db.query(
        "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM users "
        "WHERE role='user' AND created_at >= ? GROUP BY d", (start,))
    dau = db.query(
        "SELECT substr(ts,1,10) AS d, COUNT(DISTINCT actor_id) AS n FROM audit_log "
        "WHERE action='login.success' AND ts >= ? GROUP BY d", (start,))
    by_tier = [(r["k"], r["n"]) for r in db.query(
        "SELECT tier AS k, COUNT(*) AS n FROM users WHERE role='user' GROUP BY tier ORDER BY n DESC")]
    by_status = [(r["k"], r["n"]) for r in db.query(
        "SELECT status AS k, COUNT(*) AS n FROM users WHERE role='user' GROUP BY status ORDER BY n DESC")]

    cols_user = [_col("email", "Email"), _col("username", "Username"), _col("tier", "Tier"),
                 _col("status", "Status", "badge"), _col("created_at", "Signed up", "ts"),
                 _col("last_login_at", "Last sign-in", "ts")]
    sel = "SELECT email, COALESCE(username,'') AS username, tier, status, created_at, last_login_at FROM users"
    return {
        "kpis": [
            _kpi("Accounts", end_users, sub=f"{total - end_users} staff · {total} total"),
            _kpi("New", new_users, sub=f"last {p.days} days"),
            _kpi("Signed in", active, sub=f"{_pct(active, end_users) or 0}% of accounts · {p.days}d"),
            _kpi("Activated", _pct(activated, end_users), "pct", sub=f"{activated} of {end_users}"),
            _kpi("With 2FA", _pct(mfa, end_users), "pct", sub=f"{mfa} accounts"),
            _kpi("Never signed in", never, sub="active, no login yet", tone="warn" if never else None),
        ],
        "charts": [
            _series("New accounts per day", "bars", axis, signups),
            _series("Accounts signing in per day", "line", axis, dau),
            _hbars("Accounts by plan", by_tier),
            _hbars("Accounts by status", by_status),
        ],
        "tables": [
            _table("funnel", f"Signup cohort funnel (accounts created in the last {p.days} days)",
                   [_col("step", "Step"), _col("count", "Accounts", "int"),
                    _col("of_prev", "of previous", "pct"), _col("of_first", "of signups", "pct")],
                   funnel),
            _table("recent_signups", "Recent signups", cols_user,
                   db.query(f"{sel} WHERE role='user' ORDER BY id DESC LIMIT 25")),
            _table("never_signed_in", "Active but never signed in", cols_user,
                   db.query(f"{sel} WHERE role='user' AND status='active' AND last_login_at IS NULL "
                            "ORDER BY id DESC LIMIT 25"),
                   note="Activated their account and never came back — worth a nudge."),
            _table("dormant", "Dormant (no sign-in for 30+ days)", cols_user,
                   db.query(f"{sel} WHERE role='user' AND status='active' AND last_login_at < ? "
                            "ORDER BY last_login_at LIMIT 25",
                            (_iso(_now() - timedelta(days=30)),))),
        ],
        "notes": [],
    }


# ── revenue ──

def _revenue(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    paying = _paying_accounts()
    mrr = sum(r["monthly_cents"] for r in paying)
    gross = int(_scalar("SELECT COALESCE(SUM(amount_cents),0) FROM billing_orders WHERE status='paid'"))
    win = db.one(
        "SELECT COALESCE(SUM(amount_cents),0) AS rev, COUNT(*) AS n FROM billing_orders "
        "WHERE status='paid' AND paid_at >= ?", (start,)) or {"rev": 0, "n": 0}
    expired = int(_scalar(
        "SELECT COUNT(*) FROM billing_orders WHERE status='expired' AND created_at >= ?", (start,)))
    pending = int(_scalar(
        "SELECT COUNT(*) FROM billing_orders WHERE status='pending' AND created_at >= ?", (start,)))
    comps = int(_scalar(
        "SELECT COUNT(*) FROM entitlements e JOIN users u ON u.id=e.user_id "
        "WHERE u.role='user' AND e.source='manual' AND e.tier != ?", (plans.FREE_PLAN.key,)))
    currencies = [r["c"] for r in db.query(
        "SELECT DISTINCT lower(currency) AS c FROM billing_orders WHERE status='paid'")]

    per_day = db.query(
        "SELECT substr(paid_at,1,10) AS d, SUM(amount_cents) AS n FROM billing_orders "
        "WHERE status='paid' AND paid_at >= ? GROUP BY d", (start,))

    def by(col: str) -> list[tuple[str, float]]:
        return [(r["k"], r["n"]) for r in db.query(
            f"SELECT {col} AS k, SUM(amount_cents) AS n FROM billing_orders "
            "WHERE status='paid' AND paid_at >= ? GROUP BY k ORDER BY n DESC", (start,))]

    order_states = [(r["k"], r["n"]) for r in db.query(
        "SELECT status AS k, COUNT(*) AS n FROM billing_orders WHERE created_at >= ? "
        "GROUP BY status ORDER BY n DESC", (start,))]

    notes = []
    if any(c and c != "usd" for c in currencies):
        notes.append("Some paid orders are not in USD; totals add the raw amounts without conversion.")
    notes.append("MRR is a run-rate estimate: each paying account's latest paid order, "
                 "divided by the months it bought. Prepaid (crypto) periods are included "
                 "until they expire but do not renew on their own.")

    return {
        "kpis": [
            _kpi("Est. MRR", mrr, "money", sub=f"{len(paying)} paying accounts"),
            _kpi("Collected", int(win["rev"]), "money", sub=f"{win['n']} paid orders · {p.days}d"),
            _kpi("Gross to date", gross, "money"),
            _kpi("Avg order", int(win["rev"] / win["n"]) if win["n"] else None, "money", sub=f"{p.days}d"),
            _kpi("Checkout conversion", _pct(win["n"], win["n"] + expired), "pct",
                 sub=f"{expired} abandoned · {pending} in progress"),
            _kpi("Comped accounts", comps, sub="manual grants — not revenue"),
        ],
        "charts": [
            _series("Revenue per day", "bars", axis, per_day, fmt="money"),
            _hbars("Revenue by plan", by("plan"), "money"),
            _hbars("Revenue by processor", by("provider"), "money"),
            _hbars("Revenue by billing period", by("period"), "money"),
            _hbars("Checkout outcomes", order_states),
        ],
        "tables": [
            _table("accounts", "Paying accounts — soonest expiry first",
                   [_col("email", "Email"), _col("tier", "Plan"), _col("source", "Processor"),
                    _col("monthly_cents", "Per month", "money"), _col("renews", "Auto-renews", "badge"),
                    _col("expires_at", "Access until", "ts")],
                   [{**r, "renews": "yes" if r["renews"] else "no — prepaid"} for r in paying],
                   note="Accounts that don't auto-renew are the renewal-risk list."),
            _table("orders", "Recent orders",
                   [_col("created_at", "Created", "ts"), _col("email", "Email"), _col("plan", "Plan"),
                    _col("period", "Period"), _col("provider", "Processor"),
                    _col("amount_cents", "Amount", "money"), _col("status", "Status", "badge"),
                    _col("paid_at", "Paid", "ts")],
                   db.query(
                       "SELECT o.created_at, COALESCE(u.email,'(deleted)') AS email, o.plan, o.period, "
                       " o.provider, o.amount_cents, o.status, o.paid_at FROM billing_orders o "
                       "LEFT JOIN users u ON u.id = o.user_id ORDER BY o.id DESC LIMIT 50"),
                   note="Expired orders are abandoned checkouts."),
            _table("webhooks", "Payment webhooks received",
                   [_col("provider", "Processor"), _col("event_type", "Event"),
                    _col("n", "Count", "int"), _col("last", "Last seen", "ts")],
                   db.query(
                       "SELECT provider, COALESCE(event_type,'—') AS event_type, COUNT(*) AS n, "
                       "MAX(received_at) AS last FROM webhook_events WHERE received_at >= ? "
                       "GROUP BY provider, event_type ORDER BY n DESC", (start,)),
                   note="A processor that goes quiet here is a payment integration that broke."),
        ],
        "notes": notes,
    }


# ── usage & conversion ──

def _usage(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    window = _now().strftime("%Y-%m")
    free_quota = plans.FREE_PLAN.quota or 0

    month = db.one(
        "SELECT COALESCE(SUM(count),0) AS searches, COUNT(*) AS accounts FROM usage_counters "
        "WHERE window = ? AND count > 0", (window,)) or {"searches": 0, "accounts": 0}
    trial = db.one(
        "SELECT COALESCE(SUM(count),0) AS searches, COUNT(*) AS accounts FROM usage_counters "
        "WHERE window = 'all' AND count > 0") or {"searches": 0, "accounts": 0}
    exhausted = int(_scalar(
        "SELECT COUNT(*) FROM usage_counters c JOIN users u ON u.id=c.user_id "
        "WHERE c.window='all' AND u.role='user' AND u.tier='free' AND c.count >= ?", (free_quota,)))
    blocked = db.one(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT actor_id) AS users FROM audit_log "
        "WHERE action='usage.blocked' AND ts >= ?", (start,)) or {"n": 0, "users": 0}

    blocked_by_day = db.query(
        "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
        "WHERE action='usage.blocked' AND ts >= ? GROUP BY d", (start,))

    # Searches per day, from consecutive daily snapshots of the lifetime total.
    snaps = {r["day"]: r["value"] for r in db.query(
        "SELECT day, value FROM metrics_daily WHERE metric='searches_total' AND day >= ? ORDER BY day",
        ((_now() - timedelta(days=p.days + 1)).date().isoformat(),))}
    deltas, prev = [], None
    for d in sorted(snaps):
        if prev is not None:
            deltas.append({"d": d, "n": max(snaps[d] - snaps[prev], 0)})
        prev = d

    by_plan = [(r["k"], r["n"]) for r in db.query(
        "SELECT u.tier AS k, SUM(c.count) AS n FROM usage_counters c JOIN users u ON u.id=c.user_id "
        "WHERE c.window = ? GROUP BY u.tier ORDER BY n DESC", (window,))]

    trial_rows = db.query(
        "SELECT MIN(COALESCE(c.count,0), ?) AS used, COUNT(*) AS n FROM users u "
        "LEFT JOIN usage_counters c ON c.user_id=u.id AND c.window='all' "
        "WHERE u.role='user' AND u.tier='free' GROUP BY used ORDER BY used", (free_quota,))
    trial_bars = [(f"{r['used']} of {free_quota} used" + (" — exhausted" if r["used"] >= free_quota else ""), r["n"])
                  for r in trial_rows]

    candidates = db.query(
        "SELECT u.email, c.count AS used, "
        " (SELECT COUNT(*) FROM audit_log a WHERE a.actor_id=u.id AND a.action='usage.blocked') AS blocked, "
        " (SELECT MAX(ts) FROM audit_log a WHERE a.actor_id=u.id AND a.action='usage.blocked') AS last_blocked, "
        " u.created_at FROM users u JOIN usage_counters c ON c.user_id=u.id AND c.window='all' "
        "WHERE u.role='user' AND u.tier='free' AND c.count >= ? "
        "ORDER BY (last_blocked IS NULL), last_blocked DESC, u.id DESC LIMIT 50", (free_quota,))

    heavy = db.query(
        "SELECT u.id, u.email, u.tier, c.count AS used FROM usage_counters c JOIN users u ON u.id=c.user_id "
        "WHERE c.window = ? ORDER BY c.count DESC LIMIT 25", (window,))
    for r in heavy:
        # allowance(), not the plan's number: a grandfathered account's limit is larger.
        lim = usage_svc.allowance(r["id"], plans.get(r["tier"]) or plans.FREE_PLAN)
        r["limit"] = lim
        r["pct"] = _pct(r["used"], lim) if lim else None

    return {
        "kpis": [
            _kpi("Searches this month", int(month["searches"]), sub=f"{month['accounts']} accounts · {window}"),
            _kpi("Free-trial searches", int(trial["searches"]), sub=f"{trial['accounts']} accounts, lifetime"),
            _kpi("Trials used up", exhausted, sub=f"free accounts at {free_quota}/{free_quota}",
                 tone="warn" if exhausted else None),
            _kpi("Paywall hits", int(blocked["n"]), sub=f"{p.days} days"),
            _kpi("People turned away", int(blocked["users"]), sub="distinct accounts"),
        ],
        "charts": [
            _series("Paywall hits per day", "bars", axis, blocked_by_day),
            _series("Searches per day", "bars", axis, deltas,
                    note="Derived from daily snapshots; fills in as days accumulate.") if deltas else
            {"title": "Searches per day", "kind": "bars", "fmt": "int", "points": [],
             "note": "Needs two daily snapshots — appears tomorrow."},
            _hbars("Searches this month by plan", by_plan),
            _hbars("Free-trial progress", trial_bars),
        ],
        "tables": [
            _table("upgrade_candidates", "Upgrade candidates — free accounts that used up their trial",
                   [_col("email", "Email"), _col("used", "Searches used", "int"),
                    _col("blocked", "Times blocked", "int"), _col("last_blocked", "Last blocked", "ts"),
                    _col("created_at", "Signed up", "ts")],
                   candidates,
                   note="People who tried the product, wanted more, and hit the paywall. "
                        "The warmest leads in the app."),
            _table("heavy", f"Heaviest users — {window}",
                   [_col("email", "Email"), _col("tier", "Plan"), _col("used", "Searches", "int"),
                    _col("limit", "Allowance", "int"), _col("pct", "Used", "pct")],
                   heavy,
                   note="Paid accounts near their allowance are upsell candidates."),
        ],
        "notes": ["Counts only. What anyone searched for is never stored, so there is no query-level data here."],
    }


# ── security ──

_FAIL_ACTIONS = ("login.failed", "login_token.failed", "login.blocked", "login.locked",
                 "login_token.locked", "mfa.failed", "password.check_failed")


def _security(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    now_iso = _iso(_now())
    marks = ",".join("?" for _ in _FAIL_ACTIONS)

    failed = int(_scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action IN ('login.failed','login_token.failed') AND ts >= ?",
        (start,)))
    blocked = int(_scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action IN ('login.blocked','login.locked','login_token.locked') "
        "AND ts >= ?", (start,)))
    locked_now = int(_scalar(
        "SELECT COUNT(*) FROM users WHERE locked_until IS NOT NULL AND locked_until > ?", (now_iso,)))
    staff_no_mfa = int(_scalar(
        "SELECT COUNT(*) FROM users WHERE role IN ('admin','operator') "
        "AND NOT (totp_enabled OR email_otp_enabled OR sms_otp_enabled)"))
    sessions = int(_scalar(
        "SELECT COUNT(*) FROM sessions WHERE revoked = 0 AND expires_at > ?", (now_iso,)))
    resets = int(_scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action='password.reset_requested' AND ts >= ?", (start,)))

    per_day = db.query(
        "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
        "WHERE action IN ('login.failed','login_token.failed') AND ts >= ? GROUP BY d", (start,))
    kinds = [(r["k"], r["n"]) for r in db.query(
        f"SELECT action AS k, COUNT(*) AS n FROM audit_log WHERE action IN ({marks}) AND ts >= ? "
        "GROUP BY action ORDER BY n DESC", (*_FAIL_ACTIONS, start))]

    return {
        "kpis": [
            _kpi("Failed sign-ins", failed, sub=f"{p.days} days", tone="warn" if failed > 25 else None),
            _kpi("Blocked / locked out", blocked, sub=f"{p.days} days"),
            _kpi("Locked right now", locked_now, tone="bad" if locked_now else None),
            _kpi("Staff without 2FA", staff_no_mfa, tone="bad" if staff_no_mfa else "ok",
                 sub="admins and operators"),
            _kpi("Live sessions", sessions),
            _kpi("Password resets asked", resets, sub=f"{p.days} days"),
        ],
        "charts": [
            _series("Failed sign-ins per day", "bars", axis, per_day),
            _hbars("Security events by type", kinds),
        ],
        "tables": [
            _table("noisy_ips", "Noisiest addresses",
                   [_col("ip", "IP", "mono"), _col("n", "Failures", "int"),
                    _col("accounts", "Different accounts tried", "int"), _col("last", "Last seen", "ts")],
                   db.query(
                       f"SELECT ip, COUNT(*) AS n, COUNT(DISTINCT target) AS accounts, MAX(ts) AS last "
                       f"FROM audit_log WHERE action IN ({marks}) AND ts >= ? "
                       "AND ip IS NOT NULL AND ip != '' GROUP BY ip ORDER BY n DESC LIMIT 15",
                       (*_FAIL_ACTIONS, start)),
                   note="One address trying many accounts is credential stuffing."),
            _table("staff", "Staff accounts and their second factor",
                   [_col("email", "Email"), _col("role", "Role", "badge"), _col("mfa", "2FA", "badge"),
                    _col("last_login_at", "Last sign-in", "ts"), _col("last_login_ip", "From", "mono")],
                   [{**r, "mfa": "on" if r["mfa"] else "OFF"} for r in db.query(
                       "SELECT email, role, (totp_enabled OR email_otp_enabled OR sms_otp_enabled) AS mfa, "
                       "last_login_at, last_login_ip FROM users WHERE role IN ('admin','operator') "
                       "ORDER BY role, email")]),
            _table("locked", "Locked or struggling accounts",
                   [_col("email", "Email"), _col("failed_logins", "Failed in a row", "int"),
                    _col("locked_until", "Locked until", "ts")],
                   db.query(
                       "SELECT email, failed_logins, locked_until FROM users "
                       "WHERE (locked_until IS NOT NULL AND locked_until > ?) OR failed_logins >= 3 "
                       "ORDER BY failed_logins DESC LIMIT 25", (now_iso,))),
            _table("privileged", "Privileged actions",
                   [_col("ts", "When", "ts"), _col("actor", "Actor"), _col("action", "Action", "badge"),
                    _col("target", "Target"), _col("detail", "Detail")],
                   db.query(
                       "SELECT ts, COALESCE(actor,'—') AS actor, action, COALESCE(target,'') AS target, "
                       "COALESCE(detail,'') AS detail FROM audit_log WHERE ts >= ? AND "
                       "(action LIKE 'user.%' OR action LIKE 'billing.%' OR action LIKE 'mfa.%' "
                       " OR action LIKE 'password.%') ORDER BY id DESC LIMIT 40", (start,)),
                   note="Everything an admin or the billing system changed about an account."),
        ],
        "notes": [],
    }


# ── audit ──

def _audit(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    where, params = "ts >= ?", [start]
    if p.q:
        where += (" AND (actor LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\' "
                  "OR detail LIKE ? ESCAPE '\\' OR ip LIKE ? ESCAPE '\\')")
        params += [_like(p.q)] * 5
    n_events = int(_scalar(f"SELECT COUNT(*) FROM audit_log WHERE {where}", tuple(params)))
    n_actors = int(_scalar(f"SELECT COUNT(DISTINCT actor) FROM audit_log WHERE {where}", tuple(params)))
    n_actions = int(_scalar(f"SELECT COUNT(DISTINCT action) FROM audit_log WHERE {where}", tuple(params)))
    per_day = db.query(
        f"SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log WHERE {where} GROUP BY d", tuple(params))
    by_action = db.query(
        f"SELECT action, COUNT(*) AS n, MAX(ts) AS last FROM audit_log WHERE {where} "
        "GROUP BY action ORDER BY n DESC", tuple(params))
    by_actor = db.query(
        f"SELECT COALESCE(actor,'(system / anonymous)') AS actor, COUNT(*) AS n, MAX(ts) AS last "
        f"FROM audit_log WHERE {where} GROUP BY actor ORDER BY n DESC LIMIT 15", tuple(params))
    entries = db.query(
        f"SELECT id, ts, COALESCE(actor,'') AS actor, action, COALESCE(target,'') AS target, "
        f"COALESCE(detail,'') AS detail, COALESCE(ip,'') AS ip FROM audit_log WHERE {where} "
        "ORDER BY id DESC LIMIT ?", (*params, p.limit))
    return {
        "kpis": [
            _kpi("Events", n_events, sub=f"last {p.days} days" + (f" matching “{p.q}”" if p.q else "")),
            _kpi("Distinct actions", n_actions),
            _kpi("Distinct actors", n_actors, sub="accounts + system"),
        ],
        "charts": [
            _series("Events per day", "bars", axis, per_day),
            _hbars("Most frequent actions", [(r["action"], r["n"]) for r in by_action[:10]], total=n_events),
        ],
        "tables": [
            _table("entries", f"Entries (newest {p.limit})",
                   [_col("ts", "When", "ts"), _col("actor", "Actor"), _col("action", "Action", "badge"),
                    _col("target", "Target"), _col("detail", "Detail"), _col("ip", "IP", "mono")],
                   entries),
            _table("by_action", "By action",
                   [_col("action", "Action", "badge"), _col("n", "Count", "int"), _col("last", "Last seen", "ts")],
                   by_action),
            _table("by_actor", "By actor",
                   [_col("actor", "Actor"), _col("n", "Events", "int"), _col("last", "Last seen", "ts")],
                   by_actor),
        ],
        "notes": [],
    }


# ── support ──

def _support(p: Params) -> dict:
    axis = _day_axis(p.days)
    start = _start(p.days)
    now = _now()
    open_n = int(_scalar("SELECT COUNT(*) FROM tickets WHERE status='open'"))
    opened = int(_scalar("SELECT COUNT(*) FROM tickets WHERE created_at >= ?", (start,)))
    done = int(_scalar(
        "SELECT COUNT(*) FROM tickets WHERE status IN ('resolved','closed') AND updated_at >= ?", (start,)))

    timing = db.query(
        "SELECT t.id, t.created_at, t.updated_at, t.status, "
        " (SELECT MIN(m.created_at) FROM ticket_messages m WHERE m.ticket_id=t.id AND m.is_staff=1) AS first_staff "
        "FROM tickets t WHERE t.created_at >= ?", (start,))

    def hrs(a: str, b: str) -> float:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600

    first = [hrs(r["created_at"], r["first_staff"]) for r in timing if r["first_staff"]]
    solved = [hrs(r["created_at"], r["updated_at"]) for r in timing if r["status"] in ("resolved", "closed")]
    oldest = db.one("SELECT MIN(created_at) AS t FROM tickets WHERE status='open'")
    oldest_age = hrs(oldest["t"], _iso(now)) / 24 if oldest and oldest["t"] else None

    per_day = db.query(
        "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM tickets WHERE created_at >= ? GROUP BY d",
        (start,))
    reasons = [(r["k"], r["n"]) for r in db.query(
        "SELECT reason AS k, COUNT(*) AS n FROM tickets WHERE created_at >= ? GROUP BY reason ORDER BY n DESC",
        (start,))]
    statuses = [(r["k"], r["n"]) for r in db.query(
        "SELECT status AS k, COUNT(*) AS n FROM tickets GROUP BY status ORDER BY n DESC")]

    return {
        "kpis": [
            _kpi("Open", open_n, tone="warn" if open_n else None),
            _kpi("Opened", opened, sub=f"{p.days} days"),
            _kpi("Resolved", done, sub=f"{p.days} days"),
            _kpi("Median first reply", statistics.median(first) if first else None, "hours"),
            _kpi("Median time to resolve", statistics.median(solved) if solved else None, "hours"),
            _kpi("Oldest open", round(oldest_age, 1) if oldest_age is not None else None, "days"),
        ],
        "charts": [
            _series("Tickets opened per day", "bars", axis, per_day),
            _hbars("By reason", reasons),
            _hbars("By status (all time)", statuses),
        ],
        "tables": [
            _table("open", "Open tickets — oldest first",
                   [_col("id", "#", "int"), _col("email", "Customer"), _col("reason", "Reason", "badge"),
                    _col("subject", "Subject"), _col("created_at", "Opened", "ts"),
                    _col("updated_at", "Last activity", "ts"), _col("messages", "Messages", "int")],
                   db.query(
                       "SELECT t.id, COALESCE(u.email,'(deleted)') AS email, t.reason, t.subject, "
                       " t.created_at, t.updated_at, "
                       " (SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id=t.id) AS messages "
                       "FROM tickets t LEFT JOIN users u ON u.id=t.user_id "
                       "WHERE t.status='open' ORDER BY t.created_at LIMIT 50")),
        ],
        "notes": [],
    }


# ── storage ──

# What each table is and what it holds about people — a data map you can hand
# to anyone who asks "what do you store about me".
_TABLE_INFO: dict[str, tuple[str, str, str | None]] = {
    "visits": ("Visitor analytics", "Pseudonymous: IP (per ANALYTICS_IP_MODE), browser, device, page, referrer", "ts"),
    "users": ("Accounts", "Email, username, phone, password hash, 2FA secrets, notes", "created_at"),
    "sessions": ("Sign-in sessions", "Session id, IP, browser", "created_at"),
    "audit_log": ("Audit trail", "Who did what: account email, action, IP", "ts"),
    "login_tokens": ("Login tokens", "Keyed hash of each token — never the token", "created_at"),
    "password_resets": ("Password-reset links", "Digest of each link, requesting IP", "created_at"),
    "otp_codes": ("One-time codes", "Hashed codes for email/SMS 2FA", "created_at"),
    "billing_customers": ("Processor customer ids", "Stripe/NOWPayments customer reference", "created_at"),
    "billing_orders": ("Checkout orders", "Plan, amount, processor reference", "created_at"),
    "subscriptions": ("Card subscriptions", "Plan, period, renewal state", "created_at"),
    "entitlements": ("What each account has paid for", "Tier, source, expiry", "updated_at"),
    "usage_counters": ("Search metering", "Counts per account and window — never the queries", "updated_at"),
    "phone_counters": ("Phone lookup metering", "Paid phone lookups per account and month — never the numbers", "updated_at"),
    "webhook_events": ("Webhook idempotency", "Processor event ids", "received_at"),
    "tickets": ("Support tickets", "Subject, reason, status", "created_at"),
    "ticket_messages": ("Support messages", "Message text from customers and staff", "created_at"),
    "metrics_daily": ("Daily rollups", "Aggregate numbers only — no personal data", "day"),
}


def _file_size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _storage(p: Params) -> dict:
    path = str(db._db_path())
    main, wal = _file_size(path), _file_size(path + "-wal")
    names = [r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    rows = []
    for name in names:
        what, holds, tscol = _TABLE_INFO.get(name, ("(undocumented)", "", None))
        n = int(_scalar(f'SELECT COUNT(*) FROM "{name}"'))
        oldest = newest = None
        if tscol:
            r = db.one(f'SELECT MIN("{tscol}") AS a, MAX("{tscol}") AS b FROM "{name}"') or {}
            oldest, newest = r.get("a"), r.get("b")
        rows.append({"table": name, "what": what, "holds": holds, "rows": n,
                     "oldest": oldest, "newest": newest})
    rows.sort(key=lambda r: -r["rows"])
    total_rows = sum(r["rows"] for r in rows)

    oldest_visit = next((r["oldest"] for r in rows if r["table"] == "visits"), None)
    span = None
    if oldest_visit:
        span = (_now() - datetime.fromisoformat(oldest_visit)).days
    snap_days = int(_scalar("SELECT COUNT(DISTINCT day) FROM metrics_daily"))
    analytics._load_geo()

    posture = [
        {"setting": "Visitor analytics", "value": "on" if settings.analytics_enabled else "off",
         "meaning": "Whether the site's beacon is recorded at all."},
        {"setting": "IP storage", "value": settings.analytics_ip_mode,
         "meaning": "full = whole address · anonymized = last octet zeroed · none = not stored."},
        {"setting": "Visitor identity", "value": settings.analytics_id_mode,
         "meaning": "rotating = a hash that changes daily · persistent = follows a visitor across days."},
        {"setting": "Bots recorded", "value": "yes" if settings.analytics_track_bots else "no",
         "meaning": "Crawlers are stored and filtered at read time."},
        {"setting": "Raw visit retention", "value": f"{settings.analytics_retention_days} days"
         if settings.analytics_retention_days > 0 else "forever",
         "meaning": "Older visits are deleted at service start. Daily rollups are kept."},
        {"setting": "Geo database", "value": "installed" if analytics._geo_reader is not None else "missing",
         "meaning": "Local GeoLite2 lookup — no visitor IP ever leaves this server."},
    ]
    return {
        "kpis": [
            _kpi("Database", main, "bytes", sub=os.path.basename(path)),
            _kpi("Write-ahead log", wal, "bytes", sub="checkpointed automatically"),
            _kpi("Rows stored", total_rows, sub=f"{len(rows)} tables"),
            _kpi("Raw visit history", span, "days", sub=f"retention {settings.analytics_retention_days}d"
                 if settings.analytics_retention_days > 0 else "no retention limit"),
            _kpi("Days of rollups", snap_days, sub="kept after raw rows are pruned"),
        ],
        "charts": [
            _hbars("Rows by table", [(r["table"], r["rows"]) for r in rows[:10]], total=total_rows),
        ],
        "tables": [
            _table("tables", "Data map — every table, what it holds",
                   [_col("table", "Table", "mono"), _col("what", "What it is"), _col("holds", "Holds about people"),
                    _col("rows", "Rows", "int"), _col("oldest", "Oldest", "ts"), _col("newest", "Newest", "ts")],
                   rows),
            _table("posture", "Collection & retention settings",
                   [_col("setting", "Setting"), _col("value", "Value", "badge"), _col("meaning", "Meaning")],
                   posture,
                   note="Set in backend/.env. Changing them needs a service restart."),
        ],
        "notes": [],
    }


# ── history (daily rollups) ──

# metric → (label, fmt, kind). "flow" is a total for the day and can be rebuilt
# from source rows while they exist; "stock" is a level at that moment and is
# only ever captured, never reconstructed.
METRICS: dict[str, tuple[str, str, str]] = {
    "visitors": ("Visitors", "int", "flow"),
    "pageviews": ("Pageviews", "int", "flow"),
    "sessions": ("Sessions", "int", "flow"),
    "bot_events": ("Bot events", "int", "flow"),
    "signups": ("New accounts", "int", "flow"),
    "activations": ("Activations", "int", "flow"),
    "logins": ("Sign-ins", "int", "flow"),
    "login_failures": ("Failed sign-ins", "int", "flow"),
    "paywall_hits": ("Paywall hits", "int", "flow"),
    "orders_paid": ("Paid orders", "int", "flow"),
    "revenue_cents": ("Revenue", "money", "flow"),
    "tickets_opened": ("Tickets opened", "int", "flow"),
    "audit_events": ("Audit events", "int", "flow"),
    "users_total": ("Accounts", "int", "stock"),
    "users_paying": ("Paying accounts", "int", "stock"),
    "mrr_cents": ("Est. MRR", "money", "stock"),
    "active_sessions": ("Live sessions", "int", "stock"),
    "open_tickets": ("Open tickets", "int", "stock"),
    "searches_total": ("Searches to date", "int", "stock"),
    "db_bytes": ("Database size", "bytes", "stock"),
}

# Each flow query yields (d, n) rows; `{since}` bounds it so the hourly refresh
# reads two days, not the whole table.
_FLOW_SQL: dict[str, str] = {
    "visitors": "SELECT substr(ts,1,10) AS d, COUNT(DISTINCT visitor_id) AS n FROM visits "
                "WHERE is_bot=0 AND ts >= {since} GROUP BY d",
    "pageviews": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM visits "
                 "WHERE is_bot=0 AND event='pageview' AND ts >= {since} GROUP BY d",
    "sessions": "SELECT substr(ts,1,10) AS d, COUNT(DISTINCT session_id) AS n FROM visits "
                "WHERE is_bot=0 AND session_id IS NOT NULL AND ts >= {since} GROUP BY d",
    "bot_events": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM visits "
                  "WHERE is_bot=1 AND ts >= {since} GROUP BY d",
    "signups": "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM users "
               "WHERE role='user' AND created_at >= {since} GROUP BY d",
    "activations": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
                   "WHERE action='account.activated' AND ts >= {since} GROUP BY d",
    "logins": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
              "WHERE action='login.success' AND ts >= {since} GROUP BY d",
    "login_failures": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
                      "WHERE action IN ('login.failed','login_token.failed') AND ts >= {since} GROUP BY d",
    "paywall_hits": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
                    "WHERE action='usage.blocked' AND ts >= {since} GROUP BY d",
    "orders_paid": "SELECT substr(paid_at,1,10) AS d, COUNT(*) AS n FROM billing_orders "
                   "WHERE status='paid' AND paid_at >= {since} GROUP BY d",
    "revenue_cents": "SELECT substr(paid_at,1,10) AS d, SUM(amount_cents) AS n FROM billing_orders "
                     "WHERE status='paid' AND paid_at >= {since} GROUP BY d",
    "tickets_opened": "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM tickets "
                      "WHERE created_at >= {since} GROUP BY d",
    "audit_events": "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM audit_log "
                    "WHERE ts >= {since} GROUP BY d",
}


def _metric_rows(metric: str, since_day: str) -> list[dict]:
    return db.query(
        "SELECT day AS d, value AS n FROM metrics_daily WHERE metric = ? AND day >= ? ORDER BY day",
        (metric, since_day))


_last_refresh = 0.0


def refresh(full: bool = False) -> int:
    """Bring `metrics_daily` up to date. Returns rows written.

    Flow metrics are recomputed for the last two UTC days and written over
    whatever is there. With `full=True` every earlier day is also filled in,
    but only where no row exists yet — a day whose raw visits were partly
    pruned must never overwrite the complete figure stored while they existed.
    Stock metrics are written for today only; the last write of the day stands.
    """
    global _last_refresh
    today = _now().date()
    recent_from = (today - timedelta(days=1)).isoformat()
    since_sql = "'0000'" if full else f"'{recent_from}'"
    replace, ignore = [], []
    for metric, sql in _FLOW_SQL.items():
        for r in db.query(sql.format(since=since_sql)):
            if not r["d"] or r["n"] is None:
                continue
            row = (r["d"], metric, float(r["n"]))
            (replace if r["d"] >= recent_from else ignore).append(row)

    paying = _paying_accounts()
    now_iso = _iso(_now())
    stock = {
        "users_total": _scalar("SELECT COUNT(*) FROM users"),
        "users_paying": len(paying),
        "mrr_cents": sum(r["monthly_cents"] for r in paying),
        "active_sessions": _scalar(
            "SELECT COUNT(*) FROM sessions WHERE revoked=0 AND expires_at > ?", (now_iso,)),
        "open_tickets": _scalar("SELECT COUNT(*) FROM tickets WHERE status='open'"),
        "searches_total": _scalar("SELECT COALESCE(SUM(count),0) FROM usage_counters"),
        "db_bytes": _file_size(str(db._db_path())),
    }
    replace += [(today.isoformat(), k, float(v)) for k, v in stock.items()]

    n = db.executemany("INSERT OR REPLACE INTO metrics_daily (day, metric, value) VALUES (?,?,?)", replace)
    n += db.executemany("INSERT OR IGNORE INTO metrics_daily (day, metric, value) VALUES (?,?,?)", ignore)
    _last_refresh = _now().timestamp()
    return n


def ensure_fresh(max_age_s: int = 3600) -> None:
    """Refresh lazily when the admin opens the Data tab, so the page is never
    older than an hour even if the background loop has not run."""
    if _now().timestamp() - _last_refresh > max_age_s:
        try:
            refresh(full=_last_refresh == 0.0)
        except Exception:
            log.exception("datahub refresh failed")


async def refresh_loop(interval_s: int = 3600) -> None:
    """Keep the daily snapshot current while the service runs."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(refresh)
        except Exception:
            log.exception("datahub refresh failed")


def _history(p: Params) -> dict:
    axis = _day_axis(p.days)
    since = axis[0]
    data: dict[str, dict[str, float]] = {}
    for r in db.query("SELECT day, metric, value FROM metrics_daily WHERE day >= ?", (since,)):
        data.setdefault(r["day"], {})[r["metric"]] = r["value"]

    def ch(metric: str, kind: str = "bars", gaps: bool = False) -> dict:
        label, fmt, _ = METRICS[metric]
        return _series(label, kind, axis,
                       [{"d": d, "n": v[metric]} for d, v in data.items() if metric in v],
                       fmt, gaps=gaps)

    span = db.one("SELECT MIN(day) AS lo FROM metrics_daily") or {}
    stock_span = db.one(
        "SELECT MIN(day) AS lo, COUNT(DISTINCT day) AS n FROM metrics_daily WHERE metric='users_total'") or {}
    cols = [_col("day", "Day", "date")] + [_col(m, METRICS[m][0], METRICS[m][1]) for m in METRICS]
    rows = [{"day": d, **{m: data[d].get(m) for m in METRICS}} for d in sorted(data, reverse=True)]

    return {
        "kpis": [
            _kpi("Days stored", int(_scalar("SELECT COUNT(DISTINCT day) FROM metrics_daily")),
                 sub=f"since {span.get('lo') or '—'}"),
            _kpi("Snapshots taken", int(stock_span.get("n") or 0),
                 sub=f"point-in-time metrics since {stock_span.get('lo') or '—'}"),
            _kpi("Metrics tracked", len(METRICS), sub=f"{sum(1 for v in METRICS.values() if v[2]=='flow')} daily totals · "
                 f"{sum(1 for v in METRICS.values() if v[2]=='stock')} snapshots"),
        ],
        "charts": [
            ch("visitors"), ch("pageviews"), ch("signups"), ch("revenue_cents"),
            ch("users_total", "line", gaps=True), ch("users_paying", "line", gaps=True),
            ch("mrr_cents", "line", gaps=True), ch("active_sessions", "line", gaps=True),
        ],
        "tables": [_table("daily", "Daily metrics", cols, rows,
                          note="Export this table to keep a permanent copy outside the database.")],
        "notes": [
            "Totals (visitors, signups, revenue…) were back-filled from the raw rows. "
            "Snapshots (accounts, MRR, live sessions…) can't be reconstructed, so they only "
            "exist from the first day they were taken — which is why this runs every day.",
        ],
    }


# ═══════════════════════════ catalogue ═══════════════════════════

@dataclass(frozen=True)
class Dataset:
    id: str
    title: str
    group: str
    blurb: str
    build: Callable[[Params], dict]
    filters: tuple[str, ...] = ("days",)
    source: tuple[str, str] | None = None   # (table, timestamp column) for "records / newest"


DATASETS: tuple[Dataset, ...] = (
    Dataset("overview", "Overview", "Start", "The numbers that matter, side by side.",
            _overview),
    Dataset("traffic", "Traffic", "Product",
            "Pageviews, sessions, where visitors come from, what they click, and how many convert.",
            _traffic, ("days", "bots"), ("visits", "ts")),
    Dataset("audience", "Audience", "Product",
            "Browsers, devices, screens, languages, networks and countries — what to build for.",
            _audience, ("days", "bots"), ("visits", "ts")),
    Dataset("users", "Users", "Product",
            "Growth, activation funnel, who never came back, who went quiet.",
            _users, ("days",), ("users", "created_at")),
    Dataset("usage", "Usage & conversion", "Product",
            "Searches against allowance, paywall hits, and the free accounts ready to upgrade.",
            _usage, ("days",), ("usage_counters", "updated_at")),
    Dataset("revenue", "Revenue", "Money",
            "MRR, collected revenue, checkout conversion, renewal risk, abandoned checkouts.",
            _revenue, ("days",), ("billing_orders", "created_at")),
    Dataset("security", "Security", "Trust",
            "Failed sign-ins, noisy addresses, locked accounts, staff 2FA, privileged actions.",
            _security, ("days",), ("audit_log", "ts")),
    Dataset("audit", "Audit log", "Trust",
            "Every recorded action — who did what to whom, from where. Searchable.",
            _audit, ("days", "q", "limit"), ("audit_log", "ts")),
    Dataset("support", "Support", "Trust",
            "Ticket volume, response and resolution times, what's waiting.",
            _support, ("days",), ("tickets", "created_at")),
    Dataset("storage", "Data store", "System",
            "A map of everything stored, what it holds about people, and the retention settings.",
            _storage, ()),
    Dataset("history", "Daily history", "System",
            "Daily rollups kept after raw rows are pruned, plus snapshots that can't be rebuilt.",
            _history, ("days",), ("metrics_daily", "day")),
)
BY_ID = {d.id: d for d in DATASETS}


def catalog() -> list[dict]:
    out = []
    for d in DATASETS:
        item: dict[str, Any] = {"id": d.id, "title": d.title, "group": d.group,
                                "blurb": d.blurb, "filters": list(d.filters)}
        if d.source:
            table, col = d.source
            r = db.one(f'SELECT COUNT(*) AS n, MAX("{col}") AS newest FROM "{table}"') or {}
            item["records"], item["newest"] = r.get("n", 0), r.get("newest")
        out.append(item)
    return out


def build(dataset_id: str, p: Params) -> dict | None:
    d = BY_ID.get(dataset_id)
    if not d:
        return None
    out = d.build(p)
    out.update({"id": d.id, "title": d.title, "days": p.days, "generated_at": _iso(_now())})
    return out


# ─────────────────────────── CSV export ───────────────────────────

def _csv_cell(v: Any) -> str:
    """Spreadsheet-safe: a cell that starts with = + - @ would run as a formula
    when the file is opened, and several of these columns hold text a customer
    typed (ticket subjects, usernames)."""
    if v is None:
        return ""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def table_csv(data: dict, table_key: str) -> str | None:
    for t in data.get("tables", []):
        if t["key"] == table_key:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([c["label"] for c in t["columns"]])
            for row in t["rows"]:
                w.writerow([_csv_cell(row.get(c["key"])) for c in t["columns"]])
            return buf.getvalue()
    return None


def chart_csv(data: dict, index: int) -> str | None:
    charts = data.get("charts", [])
    if not 0 <= index < len(charts):
        return None
    c = charts[index]
    buf = io.StringIO()
    w = csv.writer(buf)
    if c["kind"] == "hbars":
        w.writerow(["label", "value", "share_pct"])
        for pt in c["points"]:
            w.writerow([_csv_cell(pt["label"]), pt["value"], pt.get("share", "")])
    else:
        w.writerow(["x", "y"])
        for pt in c["points"]:
            w.writerow([pt["x"], "" if pt["y"] is None else pt["y"]])
    return buf.getvalue()

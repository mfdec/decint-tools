"""SQLite store for visitor analytics.

This is the first persistent storage in the app — everything else is stateless
by design. Kept deliberately separate from the OSINT tools: nothing a user
*searches for* is written here, only who visited the site. That distinction
matters, because the marketing copy promises queries are never persisted.

WAL mode so a long analytics query can't block a write from an incoming
pageview.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    event           TEXT    NOT NULL DEFAULT 'pageview',

    -- identity (see services/analytics.visitor_id for how this is derived)
    visitor_id      TEXT,
    session_id      TEXT,

    -- network
    ip              TEXT,
    ip_hash         TEXT,
    country         TEXT,
    country_code    TEXT,
    region          TEXT,
    city            TEXT,
    latitude        REAL,
    longitude       REAL,
    timezone_geo    TEXT,
    asn             TEXT,
    org             TEXT,

    -- client
    user_agent      TEXT,
    browser         TEXT,
    browser_version TEXT,
    os              TEXT,
    os_version      TEXT,
    device_type     TEXT,
    device_model    TEXT,
    is_bot          INTEGER NOT NULL DEFAULT 0,

    -- page
    path            TEXT,
    query           TEXT,
    referrer        TEXT,
    title           TEXT,

    -- hardware / environment signals reported by the beacon
    screen_w        INTEGER,
    screen_h        INTEGER,
    viewport_w      INTEGER,
    viewport_h      INTEGER,
    pixel_ratio     REAL,
    color_depth     INTEGER,
    cpu_cores       INTEGER,
    device_memory   REAL,
    gpu             TEXT,
    tz_client       TEXT,
    language        TEXT,
    languages       TEXT,
    touch_points    INTEGER,
    connection      TEXT,

    -- interaction detail (click target, etc.)
    meta            TEXT
);

CREATE INDEX IF NOT EXISTS idx_visits_ts         ON visits (ts DESC);
CREATE INDEX IF NOT EXISTS idx_visits_visitor    ON visits (visitor_id);
CREATE INDEX IF NOT EXISTS idx_visits_country    ON visits (country_code);
CREATE INDEX IF NOT EXISTS idx_visits_path       ON visits (path);
CREATE INDEX IF NOT EXISTS idx_visits_bot        ON visits (is_bot);

-- ─────────────────────────── accounts ───────────────────────────
-- Users are grouped along three independent axes, so a change to what someone
-- has *paid for* never silently changes what they're *allowed to administer*:
--   role   — admin | operator | user   (what they can do)
--   tier   — free | starter | pro | enterprise  (what they've bought)
--   status — active | suspended | pending     (whether they may sign in)
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    email             TEXT    NOT NULL UNIQUE,
    username          TEXT    UNIQUE,
    password_hash     TEXT    NOT NULL,
    role              TEXT    NOT NULL DEFAULT 'user',
    tier              TEXT    NOT NULL DEFAULT 'free',
    status            TEXT    NOT NULL DEFAULT 'active',
    email_verified    INTEGER NOT NULL DEFAULT 0,
    phone             TEXT,

    -- second factor
    totp_secret       TEXT,
    totp_enabled      INTEGER NOT NULL DEFAULT 0,
    email_otp_enabled INTEGER NOT NULL DEFAULT 0,
    sms_otp_enabled   INTEGER NOT NULL DEFAULT 0,
    recovery_codes    TEXT,              -- JSON list of argon2 hashes

    failed_logins     INTEGER NOT NULL DEFAULT 0,
    locked_until      TEXT,

    created_at        TEXT    NOT NULL,
    updated_at        TEXT,
    last_login_at     TEXT,
    last_login_ip     TEXT,
    notes             TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_role   ON users (role);
CREATE INDEX IF NOT EXISTS idx_users_tier   ON users (tier);
CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);

-- Server-side sessions so an admin can actually revoke one. The cookie holds
-- only this row's id; nothing about the user is trusted from the client.
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    last_seen   TEXT,
    ip          TEXT,
    user_agent  TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

-- One-time codes for email/SMS second factor. Stored hashed.
CREATE TABLE IF NOT EXISTS otp_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    method     TEXT    NOT NULL,
    code_hash  TEXT    NOT NULL,
    expires_at TEXT    NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_otp_user ON otp_codes (user_id, method);

-- Who did what to whom. Admin actions on accounts should never be silent.
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor_id  INTEGER,
    actor     TEXT,
    action    TEXT NOT NULL,
    target    TEXT,
    detail    TEXT,
    ip        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);

-- Login tokens: per-account bearer credentials (an alternative to email +
-- password). Only a keyed hash is stored, never the token itself; the plaintext
-- is shown once at creation and is unrecoverable after. High-entropy, so a fast
-- keyed hash is the right store — and it gives an O(1) indexed lookup on login.
CREATE TABLE IF NOT EXISTS login_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    label        TEXT,
    created_at   TEXT    NOT NULL,
    last_used_at TEXT,
    revoked      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_login_tokens_user ON login_tokens (user_id);

-- Password-reset links. Same storage reasoning as login_tokens: the token is
-- 256 bits of urandom, so a keyed digest is both sufficient and O(1) to look
-- up. Only the digest is stored, the link is emailed once, and `used` makes it
-- single-use even before it expires.
CREATE TABLE IF NOT EXISTS password_resets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    expires_at   TEXT    NOT NULL,
    used         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    requested_ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets (user_id);

-- ─────────────────────────── billing ───────────────────────────
-- Two rails, one entitlement. Cards go through Stripe as real subscriptions —
-- the processor pulls the money on renewal. Crypto cannot be pulled: nothing
-- on-chain lets a merchant debit a wallet later, so the crypto rail sells a
-- PREPAID period that pushes `expires_at` forward instead. Everything
-- downstream reads `entitlements` and never asks which processor paid.

CREATE TABLE IF NOT EXISTS billing_customers (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider     TEXT    NOT NULL,
    customer_ref TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (user_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_billing_customers_ref ON billing_customers (customer_ref);

-- One row per checkout attempt, written BEFORE the customer leaves for the
-- processor. `reference` is the provider's session/invoice id; the webhook
-- looks the order up by it, which is what lets us ignore the plan and the
-- amount in the callback body — a webhook says only "reference X was paid",
-- and what X entitles someone to was decided here, on our side.
CREATE TABLE IF NOT EXISTS billing_orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider     TEXT    NOT NULL,
    plan         TEXT    NOT NULL,
    period       TEXT    NOT NULL,          -- monthly | yearly
    months       INTEGER NOT NULL DEFAULT 1,
    amount_cents INTEGER NOT NULL,
    currency     TEXT    NOT NULL DEFAULT 'usd',
    reference    TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending',
    pay_currency TEXT,                      -- crypto: the coin actually sent
    created_at   TEXT    NOT NULL,
    updated_at   TEXT,
    paid_at      TEXT,
    detail       TEXT
);
-- NULLs compare distinct in SQLite, so many pending orders may hold a NULL
-- reference; once a reference is set it is unique per provider.
CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_orders_ref
    ON billing_orders (provider, reference);
CREATE INDEX IF NOT EXISTS idx_billing_orders_user
    ON billing_orders (user_id, created_at DESC);

-- Provider-managed recurring plans. Only the card rail populates this.
CREATE TABLE IF NOT EXISTS subscriptions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider             TEXT    NOT NULL,
    subscription_ref     TEXT    NOT NULL UNIQUE,
    plan                 TEXT    NOT NULL,
    period               TEXT    NOT NULL DEFAULT 'monthly',   -- monthly | semiannual | yearly
    status               TEXT    NOT NULL,
    current_period_end   TEXT,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id);

-- The single read model for "what has this account actually paid for".
-- expires_at NULL means it never lapses (an admin grant, or the free tier);
-- otherwise the sweep in services/billing/store.py drops the account back to
-- free once it passes. users.tier is kept in step with this so every existing
-- feature gate keeps reading the column it already reads.
CREATE TABLE IF NOT EXISTS entitlements (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    tier           TEXT    NOT NULL,
    source         TEXT    NOT NULL,       -- stripe | nowpayments | manual
    expires_at     TEXT,
    updated_at     TEXT    NOT NULL,
    -- When we last warned this account its prepaid access was running out.
    -- Cleared on every new grant, so each purchased period gets one warning.
    notice_sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entitlements_expiry ON entitlements (expires_at);

-- Search metering: how many searches an account has made in a window
-- ("2026-09" for a monthly allowance, "all" for the free tier's fixed trial).
-- Counts only, never what was searched for — the marketing copy promises
-- queries are not persisted, and this table is where that promise is kept.
CREATE TABLE IF NOT EXISTS usage_counters (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    window     TEXT    NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, window)
);

-- Webhook idempotency. Every processor retries, and a retried "payment
-- finished" must not buy the customer a second month.
CREATE TABLE IF NOT EXISTS webhook_events (
    id          TEXT PRIMARY KEY,          -- "<provider>:<event id>"
    provider    TEXT NOT NULL,
    event_type  TEXT,
    received_at TEXT NOT NULL
);

-- ─────────────────────────── support tickets ───────────────────────────
-- One ticket per issue, with a `reason` fixed at creation so triage never
-- requires opening the thread to know what kind of problem it is.
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason      TEXT    NOT NULL,                  -- billing | technical | other
    subject     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open',    -- open | resolved | closed
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_user   ON tickets (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status, updated_at DESC);

-- Every message in the thread, customer and staff alike — there is no
-- internal-notes feature, so nothing written here is ever hidden from the
-- account that opened the ticket. `author_label` snapshots the poster's
-- name at post time so the thread still reads correctly if the account is
-- later renamed or deleted.
CREATE TABLE IF NOT EXISTS ticket_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_label TEXT,
    is_staff     INTEGER NOT NULL DEFAULT 0,
    body         TEXT    NOT NULL,
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages (ticket_id, id);

-- ─────────────────────────── daily rollups ───────────────────────────
-- Aggregates only — one number per (day, metric), never a row about a person.
-- Visits are pruned after ANALYTICS_RETENTION_DAYS, so without this the
-- long-run trend (traffic, signups, revenue) would be deleted along with the
-- raw rows. Flow metrics (visitors, signups, revenue…) are re-derivable while
-- their source rows exist; stock metrics (users total, MRR, active sessions…)
-- are point-in-time and can never be reconstructed later, which is the reason
-- to snapshot them every day from now on. See services/datahub.py.
CREATE TABLE IF NOT EXISTS metrics_daily (
    day    TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL,
    PRIMARY KEY (day, metric)
) WITHOUT ROWID;
"""


def _db_path() -> Path:
    p = Path(settings.analytics_db).expanduser()
    if not p.is_absolute():
        # relative to the backend/ package root, not the CWD
        p = Path(__file__).resolve().parent.parent / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# Columns added to a table that already exists in the wild. CREATE TABLE IF NOT
# EXISTS silently does nothing for a table that is already there, so a new
# column needs saying twice: once in SCHEMA above for fresh databases, and once
# here for the ones already running.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("entitlements", "notice_sent_at", "TEXT"),
    ("subscriptions", "period", "TEXT NOT NULL DEFAULT 'monthly'"),
)


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue  # table not created yet on this schema version
        if cols and column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(
                _db_path(), check_same_thread=False, timeout=10.0
            )
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.executescript(SCHEMA)
            _apply_column_migrations(_conn)
            _conn.commit()
        return _conn


def insert_visit(row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn = get_conn()
    with _lock:
        conn.execute(f"INSERT INTO visits ({cols}) VALUES ({marks})", tuple(row.values()))
        conn.commit()


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    with _lock:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write. Returns lastrowid for INSERTs, rowcount otherwise.

    Decided by the statement, not by whether lastrowid is set: sqlite keeps
    the connection's last inserted rowid across cursors, so after the first
    INSERT every UPDATE and DELETE would otherwise report that stale id as
    if it were a rowcount — and a conditional UPDATE that matched nothing
    would look like it matched something.
    """
    conn = get_conn()
    with _lock:
        cur = conn.execute(sql, params)
        conn.commit()
        if sql.lstrip()[:6].upper() in ("INSERT", "REPLAC"):
            return cur.lastrowid or 0
        return cur.rowcount


def executemany(sql: str, rows: list[tuple]) -> int:
    """Run one write for many parameter sets in a single transaction."""
    if not rows:
        return 0
    conn = get_conn()
    with _lock:
        cur = conn.executemany(sql, rows)
        conn.commit()
        return cur.rowcount


def prune(days: int) -> int:
    """Delete rows older than `days`. Returns rows removed."""
    conn = get_conn()
    with _lock:
        cur = conn.execute(
            "DELETE FROM visits WHERE ts < datetime('now', ?)", (f"-{int(days)} days",)
        )
        conn.commit()
        return cur.rowcount

"""Support tickets: billing/technical/other, threaded, user- and staff-facing.

A ticket belongs to one account and carries a `reason` fixed at creation so
triage never requires opening the thread to know what kind of problem it is.
Every message — customer or staff — lands in the same thread; there is no
internal-notes feature, so nothing written here is ever hidden from the
account that opened it. Staff need no separate inbox: replying to the mailed
link lands them on the same thread the customer sees.

`reply()` only ever flips status on a customer message, and only back to
`open`: a customer following up on a ticket staff marked `resolved` means it
wasn't, and that should not also require them to notice and change a status
control. Staff are the only way a ticket becomes `resolved` or `closed`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import db

REASONS = ("billing", "technical", "other")
STATUSES = ("open", "resolved", "closed")

# Every read joins the owning account in, because the staff-facing views need
# whose ticket it is and the cost of carrying two extra columns for the
# account's own view of its own ticket is nothing.
_SELECT = (
    "SELECT t.*, u.email AS user_email, u.username AS user_username "
    "FROM tickets t JOIN users u ON u.id = t.user_id"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


STAFF_LABEL = "Admin"


def _label(user: dict) -> str:
    return user.get("username") or user.get("email") or f"user#{user.get('id')}"


def _with_count(row: dict) -> dict:
    n = db.one("SELECT COUNT(*) AS n FROM ticket_messages WHERE ticket_id = ?", (row["id"],))
    return {**row, "message_count": n["n"] if n else 0}


def create(user: dict, reason: str, subject: str, message: str) -> dict:
    subject = subject.strip()[:200]
    now = _now()
    ticket_id = db.execute(
        "INSERT INTO tickets (user_id, reason, subject, status, created_at, updated_at) "
        "VALUES (?,?,?,'open',?,?)",
        (user["id"], reason, subject, now, now),
    )
    db.execute(
        "INSERT INTO ticket_messages (ticket_id, author_id, author_label, is_staff, body, created_at) "
        "VALUES (?,?,?,0,?,?)",
        (ticket_id, user["id"], _label(user), message.strip(), now),
    )
    return get(ticket_id)


def get(ticket_id: int) -> dict | None:
    row = db.one(f"{_SELECT} WHERE t.id = ?", (ticket_id,))
    return _with_count(row) if row else None


def list_for_user(user_id: int) -> list[dict]:
    rows = db.query(f"{_SELECT} WHERE t.user_id = ? ORDER BY t.updated_at DESC", (user_id,))
    return [_with_count(r) for r in rows]


def list_all(status: str | None = None, limit: int = 200) -> list[dict]:
    sql = _SELECT
    params: tuple = ()
    if status:
        sql += " WHERE t.status = ?"
        params = (status,)
    sql += " ORDER BY t.updated_at DESC LIMIT ?"
    rows = db.query(sql, (*params, limit))
    return [_with_count(r) for r in rows]


def messages(ticket_id: int) -> list[dict]:
    return db.query(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id", (ticket_id,)
    )


def detail(ticket_id: int) -> dict | None:
    t = get(ticket_id)
    return {**t, "messages": messages(ticket_id)} if t else None


def reply(ticket_id: int, author: dict, body: str, *, is_staff: bool) -> dict | None:
    now = _now()
    db.execute(
        "INSERT INTO ticket_messages (ticket_id, author_id, author_label, is_staff, body, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (ticket_id, author["id"], STAFF_LABEL if is_staff else _label(author),
         int(is_staff), body.strip(), now),
    )
    if is_staff:
        db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
    else:
        db.execute(
            "UPDATE tickets SET status = 'open', updated_at = ? WHERE id = ?",
            (now, ticket_id),
        )
    return detail(ticket_id)


def set_status(ticket_id: int, status: str) -> dict | None:
    db.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), ticket_id),
    )
    return detail(ticket_id)

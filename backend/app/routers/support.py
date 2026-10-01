"""Support tickets: open one, reply, and the staff side of the same thread.

Every message in a ticket is visible to both the account and staff — there
is no internal-notes feature — so the only thing that differs between the
customer routes and the staff ones below is who may call them, plus the two
extra actions (list everything, set status) staff get that a customer has no
use for.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import require_operator, require_session
from ..models import TicketCreate, TicketReply, TicketStatusUpdate
from ..services import mail, tickets, users
from ..services.analytics import client_ip

router = APIRouter(prefix="/support", tags=["support"])


def _is_staff(user: dict) -> bool:
    return user.get("role") in ("admin", "operator")


def _visible_or_404(ticket_id: int, user: dict) -> dict:
    t = tickets.detail(ticket_id)
    if not t or (t["user_id"] != user["id"] and not _is_staff(user)):
        raise HTTPException(404, "No such ticket.")
    return t


# ─────────────────────────── the account's own tickets ───────────────────────────

@router.get("/reasons")
async def reasons() -> dict:
    return {"reasons": list(tickets.REASONS)}


@router.get("/tickets")
async def my_tickets(user: dict = Depends(require_session)) -> dict:
    if user.get("break_glass"):
        return {"tickets": []}
    return {"tickets": tickets.list_for_user(user["id"])}


@router.post("/tickets")
async def open_ticket(
    body: TicketCreate, request: Request, user: dict = Depends(require_session)
) -> dict:
    if user.get("break_glass"):
        raise HTTPException(400, "Create a real account before opening a ticket.")
    if body.reason not in tickets.REASONS:
        raise HTTPException(400, f"reason must be one of {tickets.REASONS}")
    if not body.subject.strip() or not body.message.strip():
        raise HTTPException(400, "Subject and message are required.")

    t = tickets.create(user, body.reason, body.subject, body.message)
    users.audit(
        "support.ticket_open", actor=user, target=f"ticket#{t['id']}",
        detail=body.reason, ip=client_ip(request),
    )
    mail.notify_admins(*mail.support_ticket_opened(t["id"], body.reason, t["subject"], user))
    return tickets.detail(t["id"])


@router.get("/tickets/{ticket_id}")
async def ticket_detail(ticket_id: int, user: dict = Depends(require_session)) -> dict:
    return _visible_or_404(ticket_id, user)


@router.post("/tickets/{ticket_id}/reply")
async def ticket_reply(
    ticket_id: int, body: TicketReply, request: Request,
    user: dict = Depends(require_session),
) -> dict:
    t = _visible_or_404(ticket_id, user)
    if not body.message.strip():
        raise HTTPException(400, "Message is required.")
    if t["status"] == "closed":
        raise HTTPException(
            400, "This ticket is closed. Open a new ticket if you still need help."
        )

    # An admin or operator replying to their OWN ticket is a customer here,
    # not staff — only a reply on someone else's ticket counts as support.
    staff = _is_staff(user) and t["user_id"] != user["id"]
    updated = tickets.reply(ticket_id, user, body.message, is_staff=staff)
    users.audit(
        "support.ticket_reply", actor=user, target=f"ticket#{ticket_id}",
        detail="staff" if staff else "customer", ip=client_ip(request),
    )
    if staff:
        owner = users.get(t["user_id"])
        if owner:
            mail.send_soon(owner["email"], *mail.support_ticket_reply(ticket_id, t["subject"]))
    else:
        mail.notify_admins(*mail.support_ticket_followup(ticket_id, t["subject"], user))
    return updated


# ─────────────────────────── staff ───────────────────────────

@router.get("/admin/tickets")
async def admin_list(
    status: str | None = Query(default=None), staff: dict = Depends(require_operator)
) -> dict:
    if status and status not in tickets.STATUSES:
        raise HTTPException(400, f"status must be one of {tickets.STATUSES}")
    return {"tickets": tickets.list_all(status=status)}


@router.post("/admin/tickets/{ticket_id}/status")
async def admin_set_status(
    ticket_id: int, body: TicketStatusUpdate, request: Request,
    staff: dict = Depends(require_operator),
) -> dict:
    t = tickets.get(ticket_id)
    if not t:
        raise HTTPException(404, "No such ticket.")
    if body.status not in tickets.STATUSES:
        raise HTTPException(400, f"status must be one of {tickets.STATUSES}")

    updated = tickets.set_status(ticket_id, body.status)
    users.audit(
        "support.ticket_status", actor=staff, target=f"ticket#{ticket_id}",
        detail=body.status, ip=client_ip(request),
    )
    owner = users.get(t["user_id"])
    if owner:
        mail.send_soon(
            owner["email"], *mail.support_ticket_status(ticket_id, t["subject"], body.status)
        )
    return updated

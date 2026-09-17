"""Admin: account management, sessions, audit trail.

Every route requires role=admin. Actions that change someone else's account are
written to the audit log with the acting admin's identity.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..auth import require_admin
from ..services.billing import plans as _plans
from ..services import mail, twofactor, users
from ..services.analytics import client_ip

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class CreateUser(BaseModel):
    email: str
    password: str
    role: str = "user"
    tier: str = "free"
    status: str = "active"
    username: str | None = None
    phone: str | None = None
    notes: str | None = None


class UpdateUser(BaseModel):
    email: str | None = None
    username: str | None = None
    role: str | None = None
    tier: str | None = None
    status: str | None = None
    phone: str | None = None
    notes: str | None = None
    email_verified: int | None = None


class SetPassword(BaseModel):
    password: str


def _self_demotion_guard(actor: dict, target_id: int, changes: dict) -> None:
    """Stop an admin locking everyone (including themselves) out."""
    if actor.get("id") == target_id:
        if changes.get("role") and changes["role"] != "admin":
            raise HTTPException(400, "You cannot remove your own admin role.")
        if changes.get("status") and changes["status"] != "active":
            raise HTTPException(400, "You cannot suspend your own account.")
    if changes.get("role") and changes["role"] != "admin":
        remaining = users.stats()["by_role"].get("admin", 0)
        target = users.get(target_id)
        if target and target["role"] == "admin" and remaining <= 1:
            raise HTTPException(400, "That's the last administrator account.")


@router.get("/users")
async def list_users(
    q: str | None = None,
    role: str | None = None,
    tier: str | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    return users.listing(q=q, role=role, tier=tier, status=status,
                         limit=limit, offset=offset)


@router.get("/stats")
async def stats() -> dict:
    s = users.stats()
    s["roles"] = list(users.ROLES)
    s["tiers"] = list(users.TIERS)
    s["statuses"] = list(users.STATUSES)
    s["tier_quota"] = users.TIER_QUOTA
    # Whether that number is per month or for the life of the account (the
    # free tier's fixed trial), so the tier picker can say which.
    s["tier_quota_window"] = {p.key: p.quota_window for p in _plans.PLANS}
    return s


@router.post("/users", status_code=201)
async def create_user(body: CreateUser, request: Request,
                      actor: dict = Depends(require_admin)) -> dict:
    try:
        u = users.create(
            body.email, body.password, role=body.role, tier=body.tier,
            status=body.status, username=body.username, phone=body.phone,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    users.audit("user.created", actor=actor, target=body.email,
                detail=f"role={body.role} tier={body.tier}", ip=client_ip(request))
    return u


@router.get("/users/{user_id}")
async def get_user(user_id: int) -> dict:
    u = users.get_public(user_id)
    if not u:
        raise HTTPException(status_code=404, detail="No such user.")
    u["sessions"] = users.sessions_for(user_id)
    u["recovery_codes_left"] = users.recovery_codes_left(user_id)
    return u


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UpdateUser, request: Request,
                      actor: dict = Depends(require_admin)) -> dict:
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    before = users.get(user_id)
    if not before:
        raise HTTPException(status_code=404, detail="No such user.")
    _self_demotion_guard(actor, user_id, changes)
    try:
        u = users.update(user_id, **changes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Suspending someone should kick them out now, not at cookie expiry.
    if changes.get("status") in ("suspended", "pending"):
        users.revoke_all_sessions(user_id)
    # Approval is invisible to the person waiting on it — nothing else in the
    # app ever tells them their account went live, so this email is it.
    if changes.get("status") == "active" and before["status"] != "active":
        subject, text = mail.account_approved(
            before.get("username") or before["email"]
        )
        mail.send_soon(before["email"], subject, text)
    users.audit("user.updated", actor=actor, target=str(user_id),
                detail=str(changes), ip=client_ip(request))
    return u  # type: ignore[return-value]


@router.post("/users/{user_id}/password")
async def admin_set_password(user_id: int, body: SetPassword, request: Request,
                             actor: dict = Depends(require_admin)) -> dict:
    target = users.get(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such user.")
    try:
        users.set_password(user_id, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    users.revoke_all_sessions(user_id)
    # An admin changing someone else's password is exactly the event the owner
    # should hear about, whether it was arranged or not.
    subject, text = mail.password_changed(client_ip(request))
    mail.send_soon(target["email"], subject, text)
    users.audit("user.password_reset", actor=actor, target=str(user_id),
                ip=client_ip(request))
    return {"ok": True, "note": "All of that user's sessions were signed out."}


@router.post("/users/{user_id}/reset-mfa")
async def reset_mfa(user_id: int, request: Request,
                    actor: dict = Depends(require_admin)) -> dict:
    if not users.get(user_id):
        raise HTTPException(status_code=404, detail="No such user.")
    twofactor.disable_all(user_id)
    users.audit("user.mfa_reset", actor=actor, target=str(user_id), ip=client_ip(request))
    return {"ok": True, "note": "Second factor cleared; the user can re-enrol."}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request,
                      actor: dict = Depends(require_admin)) -> dict:
    target = users.get(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such user.")
    if actor.get("id") == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    if target["role"] == "admin" and users.stats()["by_role"].get("admin", 0) <= 1:
        raise HTTPException(status_code=400, detail="That's the last administrator account.")
    users.delete(user_id)
    users.audit("user.deleted", actor=actor, target=target["email"], ip=client_ip(request))
    return {"ok": True}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_sessions(user_id: int, request: Request,
                          actor: dict = Depends(require_admin)) -> dict:
    n = users.revoke_all_sessions(user_id)
    users.audit("user.sessions_revoked", actor=actor, target=str(user_id),
                detail=f"{n} session(s)", ip=client_ip(request))
    return {"revoked": n}


@router.get("/audit")
async def audit(limit: int = Query(200, ge=1, le=1000)) -> dict:
    return {"entries": users.audit_log(limit)}

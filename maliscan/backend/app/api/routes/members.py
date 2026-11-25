"""Office membership: invitations, role changes, removal."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text

from app.api.deps import DB, MANAGERS, require_firm_role
from app.api.errors import ApiError, forbidden, not_found
from app.api.util import audit, update_row
from app.core.config import get_settings

router = APIRouter(tags=["members"])

InviteRole = Literal["admin", "accountant", "clerk", "viewer"]


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    role: InviteRole
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreated(InvitationOut):
    token: str           # shown once; only its SHA-256 is stored
    accept_url: str


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["owner", "admin", "accountant", "clerk", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/firms/{firm_id}/invitations", response_model=InvitationCreated, status_code=201,
             summary="Invite a user by e-mail (owner/admin). Returns a one-time token/link.")
def create_invitation(firm_id: uuid.UUID, body: InvitationCreate, conn: DB) -> InvitationCreated:
    require_firm_role(conn, firm_id, MANAGERS)
    already = conn.execute(
        text("""SELECT 1 FROM public.firm_members m JOIN public.users u ON u.id = m.user_id
                 WHERE m.firm_id = :f AND lower(u.email) = lower(:e) AND m.status = 'active'"""),
        {"f": firm_id, "e": body.email},
    ).first()
    if already:
        raise ApiError(409, "already_member", "user is already a member of this office")
    token = secrets.token_urlsafe(32)
    row = conn.execute(
        text("""INSERT INTO public.firm_invitations (firm_id, email, role, token_hash, invited_by, expires_at)
                VALUES (:f, lower(:e), :r, :h, auth.uid(), :x)
                RETURNING id, email, role, expires_at, accepted_at, revoked_at, created_at"""),
        {"f": firm_id, "e": body.email, "r": body.role, "h": _hash(token),
         "x": datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)},
    ).mappings().one()
    audit(conn, firm_id, "invitation.created", "invitation", row["id"], {"role": body.role})
    base = get_settings().public_app_url.rstrip("/")
    return InvitationCreated(**row, token=token, accept_url=f"{base}/invite?token={token}")


@router.get("/firms/{firm_id}/invitations", response_model=list[InvitationOut], summary="Invitations (owner/admin)")
def list_invitations(firm_id: uuid.UUID, conn: DB) -> list[InvitationOut]:
    require_firm_role(conn, firm_id, MANAGERS)
    rows = conn.execute(
        text("""SELECT id, email, role, expires_at, accepted_at, revoked_at, created_at
                  FROM public.firm_invitations WHERE firm_id = :f ORDER BY created_at DESC LIMIT 200"""),
        {"f": firm_id},
    ).mappings().all()
    return [InvitationOut(**r) for r in rows]


@router.delete("/firms/{firm_id}/invitations/{invitation_id}", status_code=204, summary="Revoke invitation")
def revoke_invitation(firm_id: uuid.UUID, invitation_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, MANAGERS)
    n = conn.execute(
        text("""UPDATE public.firm_invitations SET revoked_at = now()
                 WHERE id = :i AND firm_id = :f AND accepted_at IS NULL AND revoked_at IS NULL"""),
        {"i": invitation_id, "f": firm_id},
    ).rowcount
    if not n:
        raise not_found("invitation")
    audit(conn, firm_id, "invitation.revoked", "invitation", invitation_id)
    return Response(status_code=204)


@router.post("/invitations/accept", summary="Accept an invitation (must be signed in with the invited e-mail)")
def accept_invitation(body: InvitationAccept, conn: DB) -> dict:
    firm_id = conn.execute(text("SELECT app.accept_invitation(:h)"), {"h": _hash(body.token)}).scalar_one()
    return {"firm_id": str(firm_id)}


@router.patch("/firms/{firm_id}/members/{user_id}", summary="Change role or disable a member (owner/admin)")
def update_member(firm_id: uuid.UUID, user_id: uuid.UUID, body: MemberUpdate, conn: DB) -> dict:
    my_role = require_firm_role(conn, firm_id, MANAGERS)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if body.role == "owner" and my_role != "owner":
        raise forbidden("only owners can grant the owner role")
    n = update_row(conn, "firm_members", changes, "firm_id = :f AND user_id = :u", {"f": firm_id, "u": user_id})
    if not n:
        # Either not a member, or RLS hid it (admin touching an owner).
        raise not_found("member")
    audit(conn, firm_id, "member.updated", "user", user_id, {k: v for k, v in changes.items()})
    return {"user_id": str(user_id), **changes}


@router.delete("/firms/{firm_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a member (owner/admin). The last owner cannot be removed.")
def remove_member(firm_id: uuid.UUID, user_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, MANAGERS)
    n = conn.execute(text("DELETE FROM public.firm_members WHERE firm_id = :f AND user_id = :u"),
                     {"f": firm_id, "u": user_id}).rowcount
    if not n:
        raise not_found("member")
    audit(conn, firm_id, "member.removed", "user", user_id)
    return Response(status_code=204)

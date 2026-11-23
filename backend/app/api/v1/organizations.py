"""Organizations and their members (per-organization RBAC).

Roles: owner > admin > manager > analyst > viewer (see app.core.tenancy for
what each may do). Memberships are revoked, never deleted. Organization
control tables (organizations / organization_members) are not under RLS: every
query here is scoped by the authenticated user's own memberships.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user
from app.core.tenancy import (
    ROLES,
    OrgContext,
    bind_org_to_session,
    build_context,
    create_organization,
)
from app.database import get_db
from app.models import Organization, OrganizationMember, User

router = APIRouter()


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str


class MemberRoleUpdate(BaseModel):
    role: str


def _member_out(m: OrganizationMember) -> dict:
    return {
        "user_id": m.user_id,
        "email": m.user.email,
        "name": m.user.name,
        "role": m.role,
        "status": m.status,
        "created_at": m.created_at,
        "revoked_at": m.revoked_at,
    }


def path_org_permission(permission: str):
    """Like get_current_org but the organization comes from the URL path."""
    async def checker(
        org_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
    ) -> OrgContext:
        ctx = build_context(db, user, org_id)  # same 403 for missing / foreign / revoked
        bind_org_to_session(db, ctx.org.id)
        if not ctx.can(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Insufficient permissions in this organization")
        return ctx
    return checker


def _validate_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role. Allowed: {list(ROLES)}")


def _active_owner_count(db: Session, org_id: str) -> int:
    return (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == org_id,
                OrganizationMember.role == "owner",
                OrganizationMember.status == "active")
        .count()
    )


def _only_owners_touch_owners(ctx: OrgContext, target_role: str, new_role: str = None) -> None:
    if ctx.role != "owner" and ("owner" in (target_role, new_role)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only an owner can grant, change or revoke the owner role")


@router.get("")
def list_my_organizations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(OrganizationMember, Organization)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .filter(OrganizationMember.user_id == user.id,
                OrganizationMember.status == "active",
                Organization.is_active.is_(True))
        .order_by(Organization.created_at)
        .all()
    )
    return [{"id": o.id, "name": o.name, "slug": o.slug, "role": m.role} for m, o in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_new_organization(
    body: OrganizationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    org = create_organization(db, body.name, user)
    db.commit()
    return {"id": org.id, "name": org.name, "slug": org.slug, "role": "owner"}


@router.get("/{org_id}/members")
def list_members(
    ctx: OrgContext = Depends(path_org_permission("members:read")),
    db: Session = Depends(get_db),
) -> List[dict]:
    members = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == ctx.org.id)
        .order_by(OrganizationMember.created_at)
        .all()
    )
    return [_member_out(m) for m in members]


@router.post("/{org_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    body: MemberAdd,
    ctx: OrgContext = Depends(path_org_permission("members:manage")),
    db: Session = Depends(get_db),
):
    _validate_role(body.role)
    _only_owners_touch_owners(ctx, body.role)

    target = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    member = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == ctx.org.id,
                OrganizationMember.user_id == target.id)
        .first()
    )
    if member is not None and member.status == "active":
        raise HTTPException(status_code=409, detail="User is already a member")
    if member is None:
        member = OrganizationMember(organization_id=ctx.org.id, user_id=target.id,
                                    role=body.role, status="active")
        db.add(member)
    else:  # re-adding a revoked member is an explicit new grant
        member.role = body.role
        member.status = "active"
        member.revoked_at = None
    db.commit()
    db.refresh(member)
    return _member_out(member)


def _get_member_or_404(db: Session, ctx: OrgContext, user_id: str) -> OrganizationMember:
    member = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == ctx.org.id,
                OrganizationMember.user_id == user_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


@router.patch("/{org_id}/members/{user_id}")
def change_member_role(
    user_id: str,
    body: MemberRoleUpdate,
    ctx: OrgContext = Depends(path_org_permission("members:manage")),
    db: Session = Depends(get_db),
):
    _validate_role(body.role)
    member = _get_member_or_404(db, ctx, user_id)
    if member.status != "active":
        raise HTTPException(status_code=409, detail="Member is revoked; add the user again")
    _only_owners_touch_owners(ctx, member.role, body.role)
    if member.role == "owner" and body.role != "owner" and _active_owner_count(db, ctx.org.id) <= 1:
        raise HTTPException(status_code=409, detail="An organization must keep at least one owner")
    member.role = body.role
    db.commit()
    db.refresh(member)
    return _member_out(member)


@router.delete("/{org_id}/members/{user_id}")
def revoke_member(
    user_id: str,
    ctx: OrgContext = Depends(path_org_permission("members:read")),
    db: Session = Depends(get_db),
):
    """Revoke a membership. Members may always leave on their own; revoking
    someone else needs members:manage."""
    member = _get_member_or_404(db, ctx, user_id)
    if user_id != ctx.user.id and not ctx.can("members:manage"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Insufficient permissions in this organization")
    if member.status != "active":
        raise HTTPException(status_code=409, detail="Member is already revoked")
    if user_id != ctx.user.id:
        _only_owners_touch_owners(ctx, member.role)
    if member.role == "owner" and _active_owner_count(db, ctx.org.id) <= 1:
        raise HTTPException(status_code=409, detail="An organization must keep at least one owner")
    member.status = "revoked"
    member.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True}

"""Organizations and their members (per-organization RBAC).

Roles: owner > admin > manager > analyst > viewer (see app.core.tenancy for
what each may do). Memberships are revoked, never deleted. Organization
control tables (organizations / organization_members) are not under RLS: every
query here is scoped by the authenticated user's own memberships.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import audit_denied, audit_event
from app.core.deps import get_current_active_user
from app.core.tenancy import (
    ROLES,
    OrgContext,
    bind_org_to_session,
    build_context,
    create_organization,
)
from app.database import get_db
from app.models import ApiKey, Organization, OrganizationMember, User

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
        request: Request,
        org_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
    ) -> OrgContext:
        try:
            ctx = build_context(db, user, org_id)  # same 403 for missing / foreign / revoked
        except HTTPException:
            audit_denied(action="access.denied", actor_user_id=user.id, organization_id=org_id[:64],
                         target=f"{request.method} {request.url.path}",
                         detail={"reason": "not_an_active_member"}, request=request)
            raise
        bind_org_to_session(db, ctx.org.id)
        if not ctx.can(permission):
            audit_denied(action="permission.denied", actor_user_id=user.id, organization_id=ctx.org.id,
                         target=permission, detail={"role": ctx.role,
                                                    "path": f"{request.method} {request.url.path}"},
                         request=request)
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
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    org = create_organization(db, body.name, user)
    bind_org_to_session(db, org.id)
    audit_event(db, action="org.created", organization_id=org.id, actor_user_id=user.id, target=org.id)
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
    request: Request,
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
    previous_status = member.status if member is not None else None
    if member is None:
        member = OrganizationMember(organization_id=ctx.org.id, user_id=target.id,
                                    role=body.role, status="active")
        db.add(member)
    else:  # re-adding a revoked member is an explicit new grant
        member.role = body.role
        member.status = "active"
        member.revoked_at = None
    audit_event(db, action="member.added", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                target=target.id, detail={"role": body.role, "previous_status": previous_status}, request=request)
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
    request: Request,
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
    previous_role = member.role
    member.role = body.role
    audit_event(db, action="member.role_changed", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                target=user_id, detail={"from": previous_role, "to": body.role}, request=request)
    db.commit()
    db.refresh(member)
    return _member_out(member)


@router.delete("/{org_id}/members/{user_id}")
def revoke_member(
    user_id: str,
    request: Request,
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
    # Machine credentials minted by someone who loses access die with that access.
    revoked_keys = (
        db.query(ApiKey)
        .filter(ApiKey.organization_id == ctx.org.id, ApiKey.created_by_user_id == user_id,
                ApiKey.revoked_at.is_(None))
        .update({ApiKey.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    audit_event(db, action="member.revoked" if user_id != ctx.user.id else "member.left",
                organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=user_id,
                detail={"role": member.role, "api_keys_revoked": revoked_keys}, request=request)
    db.commit()
    return {"success": True}


@router.get("/{org_id}/audit-log")
def list_audit_log(
    limit: int = 100,
    ctx: OrgContext = Depends(path_org_permission("audit:read")),
    db: Session = Depends(get_db),
) -> List[dict]:
    """Security audit trail of this organization (admins and owners)."""
    from app.models import AuditLog

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.organization_id == ctx.org.id)
        .order_by(AuditLog.created_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [
        {"id": r.id, "created_at": r.created_at, "action": r.action, "outcome": r.outcome,
         "actor_user_id": r.actor_user_id, "target": r.target, "detail": r.detail,
         "ip_address": r.ip_address}
        for r in rows
    ]


# ----------------------------------------------------------------------------
# Machine API keys (bots). Owned by the organization; only ads:read / ads:draft.
# ----------------------------------------------------------------------------
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: List[str] = Field(default_factory=lambda: ["ads:read"])


def _key_out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "scopes": k.scopes, "created_at": k.created_at,
            "last_used_at": k.last_used_at, "revoked_at": k.revoked_at,
            "created_by_user_id": k.created_by_user_id}  # never the hash


@router.post("/{org_id}/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: ApiKeyCreate,
    request: Request,
    ctx: OrgContext = Depends(path_org_permission("apikeys:manage")),
    db: Session = Depends(get_db),
):
    """Mint a key for THIS organization. The plaintext is returned once and never stored."""
    import hashlib
    import secrets
    from app.core.deps import BOT_SCOPES

    scopes = sorted(set(body.scopes))
    if not scopes or any(sc not in BOT_SCOPES for sc in scopes):
        raise HTTPException(status_code=422, detail=f"Allowed scopes: {sorted(BOT_SCOPES)} (keys can never publish or spend)")
    raw = f"ads_studio_{secrets.token_urlsafe(32)}"
    key = ApiKey(organization_id=ctx.org.id, name=body.name.strip(), key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                 scopes=scopes, created_by_user_id=ctx.user.id)
    db.add(key)
    db.flush()
    audit_event(db, action="apikey.created", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                target=key.id, detail={"scopes": scopes}, request=request)
    db.commit()
    return {**_key_out(key), "key": raw}


@router.get("/{org_id}/api-keys")
def list_api_keys(
    ctx: OrgContext = Depends(path_org_permission("apikeys:manage")),
    db: Session = Depends(get_db),
) -> List[dict]:
    rows = db.query(ApiKey).filter(ApiKey.organization_id == ctx.org.id).order_by(ApiKey.created_at).all()
    return [_key_out(k) for k in rows]


@router.delete("/{org_id}/api-keys/{key_id}")
def revoke_api_key(
    key_id: str,
    request: Request,
    ctx: OrgContext = Depends(path_org_permission("apikeys:manage")),
    db: Session = Depends(get_db),
):
    key = db.query(ApiKey).filter(ApiKey.organization_id == ctx.org.id, ApiKey.id == key_id).first()
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        audit_event(db, action="apikey.revoked", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                    target=key.id, request=request)
        db.commit()
    return {"success": True}

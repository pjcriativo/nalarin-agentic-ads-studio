"""Organization (tenant) context, RBAC and helpers.

Authorization model
-------------------
* Access to tenant data requires an ACTIVE membership of the authenticated
  user in the organization of the request. Global roles / ``is_superuser`` do
  NOT grant tenant data access: they only govern platform-level features.
* What a member may do inside an organization comes from the membership role
  (owner / admin / manager / analyst / viewer) via ``ROLE_PERMISSIONS``.
* The request's organization is chosen with the ``X-Organization-Id`` header.
  Without the header a user with exactly one active membership gets that
  organization; with several the header is mandatory (HTTP 400).
* Every denial on an organization the user does not belong to (missing,
  revoked, inactive or simply someone else's) answers the same 403, so the
  API cannot be used to discover which organizations exist.

The organization is then bound to the database session so PostgreSQL Row
Level Security enforces the same boundary underneath the application code
(see app/database.py).
"""
import re
import uuid
from dataclasses import dataclass
from typing import FrozenSet, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import audit_denied
from app.core.deps import get_current_active_user
from app.database import bind_context, get_db
from app.models import Organization, OrganizationMember, User

ORG_HEADER = "X-Organization-Id"

ROLES = ("owner", "admin", "manager", "analyst", "viewer")
ROLE_RANK = {role: len(ROLES) - i for i, role in enumerate(ROLES)}  # owner=5 ... viewer=1

_VIEWER = frozenset({
    "org:read", "members:read",
    "brands:read", "products:read", "profiles:read",
    "ads:read", "templates:read", "campaigns:read", "research:read",
})
# Analysts research the market (Ads Library queries spend shared API quota).
_ANALYST = _VIEWER | {"reports:read", "research:write"}
_MANAGER = _ANALYST | {
    "brands:write", "brands:delete",
    "products:write", "products:delete",
    "profiles:write", "profiles:delete",
    "ads:write", "ads:delete",
    "templates:write", "templates:delete",
    "campaigns:write", "campaigns:delete",
    "research:delete",
    "uploads:write",
}
_ADMIN = _MANAGER | {"members:manage", "audit:read"}
_OWNER = _ADMIN | {"org:manage"}

ROLE_PERMISSIONS = {
    "viewer": frozenset(_VIEWER),
    "analyst": frozenset(_ANALYST),
    "manager": frozenset(_MANAGER),
    "admin": frozenset(_ADMIN),
    "owner": frozenset(_OWNER),
}

_FORBIDDEN_ORG = "You do not have access to this organization"


@dataclass
class OrgContext:
    org: Organization
    membership: OrganizationMember
    user: User

    @property
    def role(self) -> str:
        return self.membership.role

    @property
    def permissions(self) -> FrozenSet[str]:
        return ROLE_PERMISSIONS.get(self.membership.role, frozenset())

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _forbidden(detail: str = _FORBIDDEN_ORG) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def find_active_membership(db: Session, user_id: str, org_id: str) -> Optional[OrganizationMember]:
    return (
        db.query(OrganizationMember)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .filter(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == org_id,
            OrganizationMember.status == "active",
            Organization.is_active.is_(True),
        )
        .first()
    )


def bind_org_to_session(db: Session, org_id: Optional[str]) -> None:
    """Make PostgreSQL RLS see ``org_id`` for this and every later transaction
    of the session. The context is HMAC-signed (app.database.bind_context), so
    SQL that merely overwrites the raw setting cannot switch tenant."""
    bind_context(db, org_id)


def build_context(db: Session, user: User, org_id: str) -> OrgContext:
    membership = find_active_membership(db, user.id, org_id)
    if membership is None:
        raise _forbidden()
    return OrgContext(org=membership.organization, membership=membership, user=user)


def _deny_org_access(request: Request, user: User, requested_org: Optional[str], reason: str) -> None:
    audit_denied(
        action="access.denied", actor_user_id=user.id,
        organization_id=(requested_org or None), target=f"{request.method} {request.url.path}",
        detail={"reason": reason}, request=request,
    )


async def get_current_org(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    x_organization_id: Optional[str] = Header(None, alias=ORG_HEADER, max_length=64),
) -> OrgContext:
    """Central dependency: the validated organization of this request.

    Authenticated user + ACTIVE membership + active organization, then the
    organization is bound to the DB session for RLS. Raises 403 otherwise.
    """
    if x_organization_id:
        requested = x_organization_id.strip()
        try:
            ctx = build_context(db, user, requested)
        except HTTPException:
            _deny_org_access(request, user, requested[:64], "not_an_active_member")
            raise
    else:
        memberships = (
            db.query(OrganizationMember)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .filter(
                OrganizationMember.user_id == user.id,
                OrganizationMember.status == "active",
                Organization.is_active.is_(True),
            )
            .all()
        )
        if not memberships:
            _deny_org_access(request, user, None, "no_active_membership")
            raise _forbidden("No active organization membership")
        if len(memberships) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Several organizations: send the {ORG_HEADER} header",
            )
        membership = memberships[0]
        ctx = OrgContext(org=membership.organization, membership=membership, user=user)

    bind_org_to_session(db, ctx.org.id)
    return ctx


def require_org_permission(permission: str):
    """Dependency factory: current organization + a specific RBAC permission."""
    async def checker(request: Request, ctx: OrgContext = Depends(get_current_org)) -> OrgContext:
        if not ctx.can(permission):
            audit_denied(
                action="permission.denied", actor_user_id=ctx.user.id, organization_id=ctx.org.id,
                target=permission, detail={"role": ctx.role, "path": f"{request.method} {request.url.path}"},
                request=request,
            )
            raise _forbidden("Insufficient permissions in this organization")
        return ctx
    return checker


_LOOSE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def client_or_new_id(value: Optional[str], loose: bool = False) -> str:
    """Accept a client-generated id only when it is a well-formed UUID (the
    frontend uses crypto.randomUUID()); anything else gets a server id. Ids are
    never guessable sequences, so a primary-key collision with another
    organization's row is not a usable existence oracle."""
    if value:
        try:
            return str(uuid.UUID(str(value)))
        except ValueError:
            # ``loose`` is for entities whose UI generates readable ids
            # (camp_xxx, adset_xxx...): any short slug is accepted.
            if loose and _LOOSE_ID_RE.match(str(value)):
                return str(value)
    return str(uuid.uuid4())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "org"


def create_organization(db: Session, name: str, owner: User) -> Organization:
    """Create an organization with ``owner`` as its first (owner) member.
    Does not commit."""
    org = Organization(
        id=str(uuid.uuid4()),
        name=name.strip(),
        slug=f"{_slugify(name)}-{uuid.uuid4().hex[:8]}",
    )
    db.add(org)
    db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=owner.id, role="owner", status="active"))
    db.flush()
    return org


def create_personal_organization(db: Session, user: User) -> Organization:
    return create_organization(db, f"{user.name or user.email} (personal)", user)


async def require_platform_admin(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> User:
    """Platform-level administration (global catalogs). Needs ``is_superuser``;
    the session then carries the SIGNED platform-admin capability -- and NO
    organization, so it still cannot read any tenant data."""
    if not user.is_superuser:
        audit_denied(action="catalog.write_denied", actor_user_id=user.id,
                     target=f"{request.method} {request.url.path}", request=request)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform administrator required")
    bind_context(db, None, platform_admin=True)
    return user

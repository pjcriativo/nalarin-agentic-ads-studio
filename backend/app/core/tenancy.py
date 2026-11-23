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

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user
from app.database import ORG_SETTING, get_db
from app.models import Organization, OrganizationMember, User

ORG_HEADER = "X-Organization-Id"

ROLES = ("owner", "admin", "manager", "analyst", "viewer")
ROLE_RANK = {role: len(ROLES) - i for i, role in enumerate(ROLES)}  # owner=5 ... viewer=1

_VIEWER = frozenset({
    "org:read", "members:read",
    "brands:read", "products:read", "profiles:read",
})
_ANALYST = _VIEWER | {"reports:read"}
_MANAGER = _ANALYST | {
    "brands:write", "brands:delete",
    "products:write", "products:delete",
    "profiles:write", "profiles:delete",
}
_ADMIN = _MANAGER | {"members:manage"}
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
    of the session (the after_begin hook in app.database re-applies it)."""
    db.info["org_id"] = org_id
    db.execute(text("SELECT set_config(:name, :org, true)"), {"name": ORG_SETTING, "org": org_id or ""})


def build_context(db: Session, user: User, org_id: str) -> OrgContext:
    membership = find_active_membership(db, user.id, org_id)
    if membership is None:
        raise _forbidden()
    return OrgContext(org=membership.organization, membership=membership, user=user)


async def get_current_org(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    x_organization_id: Optional[str] = Header(None, alias=ORG_HEADER, max_length=64),
) -> OrgContext:
    """Central dependency: the validated organization of this request.

    Authenticated user + ACTIVE membership + active organization, then the
    organization is bound to the DB session for RLS. Raises 403 otherwise.
    """
    if x_organization_id:
        ctx = build_context(db, user, x_organization_id.strip())
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
    async def checker(ctx: OrgContext = Depends(get_current_org)) -> OrgContext:
        if not ctx.can(permission):
            raise _forbidden("Insufficient permissions in this organization")
        return ctx
    return checker


def client_or_new_id(value: Optional[str]) -> str:
    """Accept a client-generated id only when it is a well-formed UUID (the
    frontend uses crypto.randomUUID()); anything else gets a server id. Ids are
    never guessable sequences, so a primary-key collision with another
    organization's row is not a usable existence oracle."""
    if value:
        try:
            return str(uuid.UUID(str(value)))
        except ValueError:
            pass
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

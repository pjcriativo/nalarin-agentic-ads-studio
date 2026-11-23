from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import CustomerProfile as ProfileModel
from app.core.tenancy import OrgContext, client_or_new_id, require_org_permission
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

# Pydantic schemas
class CustomerProfileBase(BaseModel):
    name: str
    demographics: str = ""
    painPoints: str = ""
    goals: str = ""

class CustomerProfileCreate(CustomerProfileBase):
    id: Optional[str] = None

class CustomerProfile(CustomerProfileBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


def _own_profile(db: Session, ctx: OrgContext, profile_id: str):
    return (
        db.query(ProfileModel)
        .filter(ProfileModel.id == profile_id, ProfileModel.organization_id == ctx.org.id)
        .first()
    )


def _serialize(p: ProfileModel) -> dict:
    # Convert snake_case to camelCase for frontend
    return {
        "id": p.id,
        "name": p.name,
        "demographics": p.demographics or "",
        "painPoints": p.pain_points or "",
        "goals": p.goals or "",
        "created_at": p.created_at,
    }


@router.get("", response_model=List[CustomerProfile])
def read_profiles(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("profiles:read")),
):
    profiles = (
        db.query(ProfileModel)
        .filter(ProfileModel.organization_id == ctx.org.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_serialize(p) for p in profiles]

@router.post("/", response_model=CustomerProfile)
def create_profile(
    profile: CustomerProfileCreate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("profiles:write")),
):
    db_profile = ProfileModel(
        id=client_or_new_id(profile.id),
        organization_id=ctx.org.id,
        name=profile.name,
        demographics=profile.demographics,
        pain_points=profile.painPoints,
        goals=profile.goals
    )
    db.add(db_profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Resource id already in use")
    db.refresh(db_profile)
    return _serialize(db_profile)

@router.put("/{profile_id}")
def update_profile(
    profile_id: str,
    profile: CustomerProfileBase,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("profiles:write")),
):
    db_profile = _own_profile(db, ctx, profile_id)
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db_profile.name = profile.name
    db_profile.demographics = profile.demographics
    db_profile.pain_points = profile.painPoints
    db_profile.goals = profile.goals

    db.commit()
    db.refresh(db_profile)
    return {"success": True}

@router.delete("/{profile_id}")
def delete_profile(
    profile_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("profiles:delete")),
):
    db_profile = _own_profile(db, ctx, profile_id)
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(db_profile)
    db.commit()
    return {"success": True}

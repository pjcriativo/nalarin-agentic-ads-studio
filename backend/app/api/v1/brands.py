from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Brand as BrandModel, Product as ProductModel, CustomerProfile as ProfileModel
from app.schemas.brand import Brand, BrandCreate, BrandUpdate
from app.core.tenancy import OrgContext, client_or_new_id, require_org_permission

router = APIRouter()

# Every query below is scoped to the caller's organization twice: explicitly
# (organization_id == ctx.org.id) and by PostgreSQL row level security. A brand
# of another organization is indistinguishable from one that does not exist.


def _own_brand(db: Session, ctx: OrgContext, brand_id: str):
    return (
        db.query(BrandModel)
        .filter(BrandModel.id == brand_id, BrandModel.organization_id == ctx.org.id)
        .first()
    )


def _own_profiles(db: Session, ctx: OrgContext, profile_ids):
    """Profiles of THIS organization among ``profile_ids`` (others are ignored)."""
    if not profile_ids:
        return []
    return (
        db.query(ProfileModel)
        .filter(ProfileModel.id.in_(list(profile_ids)), ProfileModel.organization_id == ctx.org.id)
        .all()
    )


@router.get("", response_model=List[Brand])
def read_brands(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("brands:read")),
):
    return (
        db.query(BrandModel)
        .filter(BrandModel.organization_id == ctx.org.id)
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post("/", response_model=Brand)
def create_brand(
    brand: BrandCreate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("brands:write")),
):
    db_brand = BrandModel(
        id=client_or_new_id(brand.id),
        organization_id=ctx.org.id,
        name=brand.name,
        logo=brand.logo,
        voice=brand.voice,
        primary_color=brand.colors.primary,
        secondary_color=brand.colors.secondary,
        highlight_color=brand.colors.highlight,
    )
    db.add(db_brand)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Resource id already in use")
    db.refresh(db_brand)

    for p in brand.products or []:
        db.add(ProductModel(
            id=client_or_new_id(p.id),
            organization_id=ctx.org.id,
            brand_id=db_brand.id,
            name=p.name,
            description=p.description,
            product_shots=p.product_shots,
        ))

    for profile in _own_profiles(db, ctx, brand.profileIds):
        db_brand.profiles.append(profile)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Resource id already in use")
    db.refresh(db_brand)
    return db_brand


@router.delete("/{brand_id}")
def delete_brand(
    brand_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("brands:delete")),
):
    db_brand = _own_brand(db, ctx, brand_id)
    if db_brand is None:
        raise HTTPException(status_code=404, detail="Brand not found")
    db.delete(db_brand)
    db.commit()
    return {"success": True}


@router.put("/{brand_id}", response_model=Brand)
def update_brand(
    brand_id: str,
    brand: BrandUpdate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("brands:write")),
):
    db_brand = _own_brand(db, ctx, brand_id)
    if not db_brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    # Update basic fields
    db_brand.name = brand.name
    db_brand.logo = brand.logo
    db_brand.voice = brand.voice
    db_brand.primary_color = brand.colors.primary
    db_brand.secondary_color = brand.colors.secondary
    db_brand.highlight_color = brand.colors.highlight

    # Update Products (only products of this organization can be touched)
    existing_products = {p.id: p for p in db_brand.products}
    incoming_product_ids = set()

    for p in brand.products or []:
        if p.id:
            incoming_product_ids.add(p.id)

        if p.id and p.id in existing_products:
            # Product already belongs to this brand - update it
            existing_product = existing_products[p.id]
            existing_product.name = p.name
            existing_product.description = p.description
            existing_product.product_shots = p.product_shots
        elif p.id:
            moved = (
                db.query(ProductModel)
                .filter(ProductModel.id == p.id, ProductModel.organization_id == ctx.org.id)
                .first()
            )
            if moved:
                # Product of ANOTHER brand of the same organization - reassign it
                moved.brand_id = brand_id
                moved.name = p.name
                moved.description = p.description
                moved.product_shots = p.product_shots
            else:
                # Unknown id (or an id that lives in another organization, which is
                # indistinguishable): create a new product in this organization.
                db.add(ProductModel(
                    id=client_or_new_id(p.id),
                    organization_id=ctx.org.id,
                    brand_id=brand_id,
                    name=p.name,
                    description=p.description,
                    product_shots=p.product_shots,
                ))
        else:  # new product without a pre-assigned ID
            new_id = client_or_new_id(None)
            db.add(ProductModel(
                id=new_id,
                organization_id=ctx.org.id,
                brand_id=brand_id,
                name=p.name,
                description=p.description,
                product_shots=p.product_shots,
            ))
            incoming_product_ids.add(new_id)

    # Remove products that are no longer assigned to this brand
    for pid, p in existing_products.items():
        if pid not in incoming_product_ids:
            db.delete(p)

    # Update Profiles
    if brand.profileIds is not None:
        db_brand.profiles = _own_profiles(db, ctx, brand.profileIds)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Resource id already in use")
    db.refresh(db_brand)
    return db_brand

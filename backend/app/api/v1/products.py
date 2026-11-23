from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Brand as BrandModel, Product as ProductModel
from app.schemas.product import Product, ProductCreate, ProductUpdate
from app.core.tenancy import OrgContext, client_or_new_id, require_org_permission

router = APIRouter()

# Scoped to the caller's organization twice: explicit filter + PostgreSQL RLS.


def _own_product(db: Session, ctx: OrgContext, product_id: str):
    return (
        db.query(ProductModel)
        .filter(ProductModel.id == product_id, ProductModel.organization_id == ctx.org.id)
        .first()
    )


@router.get("", response_model=List[Product])
def read_products(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("products:read")),
):
    return (
        db.query(ProductModel)
        .filter(ProductModel.organization_id == ctx.org.id)
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{product_id}", response_model=Product)
def read_product(
    product_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("products:read")),
):
    product = _own_product(db, ctx, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("/", response_model=Product)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("products:write")),
):
    # The parent brand must belong to this organization. A brand of another
    # organization answers exactly like a missing one.
    brand = (
        db.query(BrandModel)
        .filter(BrandModel.id == product.brand_id, BrandModel.organization_id == ctx.org.id)
        .first()
    )
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found")

    data = product.model_dump()
    data["id"] = client_or_new_id(data.get("id"))
    db_product = ProductModel(**data, organization_id=ctx.org.id)
    db.add(db_product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Resource id already in use")
    db.refresh(db_product)
    return db_product


@router.put("/{product_id}", response_model=Product)
def update_product(
    product_id: str,
    product: ProductUpdate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("products:write")),
):
    db_product = _own_product(db, ctx, product_id)
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    # ProductUpdate cannot carry brand_id / organization_id / id, so a product
    # can neither be moved to another brand nor to another organization here.
    for key, value in product.model_dump(exclude_unset=True).items():
        setattr(db_product, key, value)

    db.commit()
    db.refresh(db_product)
    return db_product


@router.delete("/{product_id}")
def delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("products:delete")),
):
    db_product = _own_product(db, ctx, product_id)
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    db.delete(db_product)
    db.commit()
    return {"message": "Product deleted successfully"}

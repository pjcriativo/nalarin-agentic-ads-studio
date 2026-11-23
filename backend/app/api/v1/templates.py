from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import WinningAd as WinningAdModel
from app.schemas.template import WinningAd
from app.core.storage import save_local
from app.core.tenancy import OrgContext, require_org_permission

router = APIRouter()

@router.get("/", response_model=List[WinningAd])
def read_winning_ads(
    search: Optional[str] = None,
    category: Optional[str] = None,
    style: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:read"))
):
    query = db.query(WinningAdModel).filter(WinningAdModel.organization_id == ctx.org.id)
    
    if category:
        query = query.filter(WinningAdModel.template_category == category)
    if style:
        query = query.filter(WinningAdModel.design_style == style)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (WinningAdModel.name.ilike(search_term)) |
            (WinningAdModel.tags.ilike(search_term)) |
            (WinningAdModel.product_name.ilike(search_term))
        )
    
    return query.all()

@router.get("/filters")
def read_template_filters(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:read"))
):
    own = WinningAdModel.organization_id == ctx.org.id
    categories = db.query(WinningAdModel.template_category).distinct().filter(own, WinningAdModel.template_category != None).all()
    styles = db.query(WinningAdModel.design_style).distinct().filter(own, WinningAdModel.design_style != None).all()
    
    return {
        "categories": [c[0] for c in categories],
        "styles": [s[0] for s in styles]
    }

@router.get("/{id}/preview", response_model=WinningAd)
def read_template_preview(
    id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:read"))
):
    template = db.query(WinningAdModel).filter(
        WinningAdModel.organization_id == ctx.org.id, WinningAdModel.id == id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template

from fastapi import File, UploadFile
import os

from app.api.v1.uploads import ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE, _looks_like_image


@router.post("/upload")
async def upload_winning_ad(
    images: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:write"))
):
    if len(images) > 20:
        raise HTTPException(status_code=400, detail="Too many files (max 20)")
    saved_ads = []
    for image in images:
        original_name = os.path.basename(image.filename or "template")
        extension = os.path.splitext(original_name)[1].lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Invalid file type")
        content = await image.read(MAX_IMAGE_SIZE + 1)
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        if not _looks_like_image(content):
            raise HTTPException(status_code=400, detail="File content does not match an image type")

        # Stored in the organization's private upload namespace (signed name)
        url = save_local(ctx.org.id, content, extension)

        # Note: In a real app, we'd probably analyze the image here or let the user fill in details
        # For now, we create a basic record
        new_ad = WinningAdModel(
            organization_id=ctx.org.id,
            name=original_name,
            image_url=url,
            filename=url.rsplit("/", 1)[-1],
            template_category="Uploaded",
            design_style="Unknown"
        )
        db.add(new_ad)
        db.commit()
        db.refresh(new_ad)
        saved_ads.append(new_ad)

    return {"message": f"Successfully uploaded {len(saved_ads)} templates", "ads": saved_ads}

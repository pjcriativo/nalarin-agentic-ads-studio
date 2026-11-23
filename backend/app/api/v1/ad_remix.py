"""
Ad Remix API Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from typing import List
import json

from app.database import get_db
from app.core.tenancy import OrgContext, require_org_permission
from app.models import WinningAd, Brand, Product, CustomerProfile
from app.schemas.ad_blueprint import (
    AdBlueprint,
    AdBlueprintResponse,
    AdConcept,
    BrandData,
    DeconstructRequest,
    ReconstructRequest
)
from app.services.ad_remix_service import deconstruct_template, reconstruct_ad
from app.ai import errors as ai_errors
from app.ai.api_errors import to_http
from app.ai.gateway import AIGateway
from app.ai.media import load_template_image

router = APIRouter()


@router.post("/deconstruct", response_model=AdBlueprint)
async def deconstruct_ad_template(
    request: DeconstructRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:write")),
):
    """
    Deconstruct a template into a structural blueprint
    
    This analyzes the template image and extracts:
    - Layout framework
    - Narrative arc
    - Text hierarchy
    - Psychological triggers
    - Visual style guide
    """
    # Get the template
    template = db.query(WinningAd).filter(WinningAd.organization_id == ctx.org.id, WinningAd.id == request.template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Deconstruct the template
    try:
        image = await load_template_image(template.image_url, ctx.org.id)
        blueprint = await deconstruct_template(AIGateway.for_context(db, ctx), image)
        
        # Save the blueprint to the template
        template.blueprint_json = blueprint.model_dump()
        template.blueprint_analyzed_at = func.now()
        db.commit()
        
        return blueprint
        
    except ai_errors.AIError as e:
        if e.kind == ai_errors.INVALID_RESPONSE:
            raise HTTPException(status_code=500, detail="Deconstruction failed")
        raise to_http(e)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Deconstruction failed")


@router.post("/reconstruct", response_model=AdConcept)
async def reconstruct_ad_from_blueprint(
    request: ReconstructRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("brands:read")),
):
    """
    Reconstruct an ad by applying brand data to a blueprint
    
    This takes a template's blueprint and generates a new ad concept
    with your brand/product information while maintaining the proven structure.
    """
    if not ctx.can("templates:read"):
        raise HTTPException(status_code=403, detail="Insufficient permissions in this organization")
    # Get the template with blueprint
    template = db.query(WinningAd).filter(WinningAd.organization_id == ctx.org.id, WinningAd.id == request.template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    if not template.blueprint_json:
        raise HTTPException(
            status_code=400,
            detail="Template has not been deconstructed yet. Run /deconstruct first."
        )
    
    # Get brand, product, and profile data
    brand = db.query(Brand).filter(Brand.id == request.brand_id, Brand.organization_id == ctx.org.id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    
    product = db.query(Product).filter(Product.id == request.product_id, Product.organization_id == ctx.org.id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    profile = db.query(CustomerProfile).filter(CustomerProfile.id == request.profile_id, CustomerProfile.organization_id == ctx.org.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Customer profile not found")
    
    # Build brand data
    brand_data = BrandData(
        brand_name=brand.name,
        brand_voice=brand.voice,
        product_name=product.name,
        product_description=product.description or "",
        audience_demographics=profile.demographics or "",
        audience_pain_points=profile.pain_points or "",
        audience_goals=profile.goals or "",
        campaign_offer=request.campaign_offer,
        campaign_urgency=request.campaign_urgency,
        campaign_messaging=request.campaign_messaging
    )
    
    # Reconstruct the blueprint
    blueprint = AdBlueprint(**template.blueprint_json)
    
    try:
        ad_concept = await reconstruct_ad(AIGateway.for_context(db, ctx), blueprint, brand_data)
        return ad_concept
        
    except ai_errors.AIError as e:
        if e.kind == ai_errors.INVALID_RESPONSE:
            raise HTTPException(status_code=500, detail="Reconstruction failed")
        raise to_http(e)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Reconstruction failed")


@router.get("/blueprints/{template_id}", response_model=AdBlueprint)
async def get_template_blueprint(
    template_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:read")),
):
    """
    Get the blueprint for a specific template
    """
    template = db.query(WinningAd).filter(WinningAd.organization_id == ctx.org.id, WinningAd.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    if not template.blueprint_json:
        raise HTTPException(
            status_code=404,
            detail="Template has not been deconstructed yet"
        )
    
    return AdBlueprint(**template.blueprint_json)


@router.get("/blueprints", response_model=List[dict])
async def list_templates_with_blueprints(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("templates:read")),
):
    """
    List all templates that have been deconstructed
    """
    templates = db.query(WinningAd).filter(WinningAd.organization_id == ctx.org.id, WinningAd.blueprint_json.isnot(None)).all()
    
    return [
        {
            "template_id": t.id,
            "template_name": t.name,
            "blueprint": t.blueprint_json,
            "analyzed_at": t.blueprint_analyzed_at
        }
        for t in templates
    ]

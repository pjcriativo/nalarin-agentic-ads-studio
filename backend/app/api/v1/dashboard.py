from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Brand, Product, GeneratedAd, WinningAd, FacebookCampaign
from app.core.tenancy import OrgContext, get_current_org

router = APIRouter()

@router.get("/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_current_org),
):
    """
    Get aggregated statistics for the dashboard.

    Every counter is scoped to the caller's organization (explicit filter + RLS).
    """
    brands_count = db.query(Brand).filter(Brand.organization_id == ctx.org.id).count()
    products_count = db.query(Product).filter(Product.organization_id == ctx.org.id).count()
    generated_ads_count = db.query(GeneratedAd).filter(GeneratedAd.organization_id == ctx.org.id).count()
    templates_count = db.query(WinningAd).filter(WinningAd.organization_id == ctx.org.id).count()
    campaigns_count = db.query(FacebookCampaign).filter(FacebookCampaign.organization_id == ctx.org.id).count()

    return {
        "brands_count": brands_count,
        "products_count": products_count,
        "generated_ads_count": generated_ads_count,
        "templates_count": templates_count,
        "campaigns_count": campaigns_count
    }


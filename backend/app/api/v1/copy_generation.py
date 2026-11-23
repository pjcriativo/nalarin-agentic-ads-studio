from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from app.ai import errors as ai_errors
from app.ai.api_errors import to_http
from app.ai.gateway import AIGateway
from app.ai.json_utils import parse_json
from app.ai.types import ChatRequest, Message
from app.core.tenancy import OrgContext, require_org_permission
from app.database import get_db

router = APIRouter()

# No server-wide AI key: the organization's own credential and model (AI Gateway, BYOK) serve
# every request, and its usage/cost is accounted to that organization.
COPY_MAX_TOKENS = 2048


class CopyGenerationRequest(BaseModel):
    brand: Dict[str, Any]
    product: Dict[str, Any]
    profile: Dict[str, Any]
    template: Optional[Dict[str, Any]] = None
    variationCount: int = 3
    campaignDetails: Dict[str, str]
    customPrompt: Optional[str] = None


class FieldRegenerationRequest(BaseModel):
    field: str
    currentValue: str
    brand: Dict[str, Any]
    product: Dict[str, Any]
    profile: Dict[str, Any]
    template: Optional[Dict[str, Any]] = None
    campaignDetails: Dict[str, str]


@router.post("/generate")
async def generate_copy(request: CopyGenerationRequest, db: Session = Depends(get_db),
                        ctx: OrgContext = Depends(require_org_permission("ads:write"))):
    """Generate ad copy variations through the organization's AI Gateway"""

    try:
        # Build the prompt
        count = request.variationCount
        prompt = f"""You are an expert ad copywriter. Generate {count} variations of ad copy for a Facebook/Instagram ad campaign.

BRAND VOICE: {request.brand.get('voice', 'Professional and friendly')}

PRODUCT: {request.product.get('name')}
{f"Description: {request.product.get('description')}" if request.product.get('description') else ''}

TARGET AUDIENCE:
- Demographics: {request.profile.get('demographics', 'General audience')}
- Pain Points: {request.profile.get('pain_points', 'Not specified')}
- Goals: {request.profile.get('goals', 'Not specified')}

CAMPAIGN DETAILS:
- Offer: {request.campaignDetails.get('offer')}
- Key Messaging: {request.campaignDetails.get('messaging')}

TEMPLATE STYLE: {request.template.get('design_style', 'Modern and clean') if request.template else 'Modern and clean'}

BODY COPY STYLES (vary across variations):
1. BULLET POINTS WITH EMOJIS: Use 2-4 bullet points with emojis at the start
   - Sometimes use the same emoji (e.g., ✓ ✓ ✓ or ⭐ ⭐ ⭐)
   - Sometimes use mixed emojis (e.g., 🎯 💪 ✨ 🚀)
   - Keep each bullet concise and benefit-focused
   Example: "✓ Save 50% today
✓ Free shipping
✓ 30-day guarantee"

2. EMOTIONAL STORYTELLING: Longer narrative that connects emotionally
   - Tell a relatable story or paint a vivid picture
   - Use emotional triggers and sensory details
   - Build desire and urgency through narrative
   - Can be 150-200 characters for story-driven ads
   Example: "Remember that feeling when everything just clicks? When you finally found the solution you've been searching for? That's what our customers experience every day..."

INSTRUCTIONS:
Generate {count} distinct variations. Mix both body copy styles across variations. Each variation should:
1. Match the brand voice consistently
2. Address the audience's pain points and goals
3. Incorporate the campaign offer and key messaging
4. Be compelling, conversion-focused, and ad-appropriate
5. Keep headlines under 40 characters
6. For bullet-point style: Keep body under 125 characters
7. For storytelling style: Can extend to 200 characters
8. Keep CTAs under 20 characters

Return ONLY valid JSON in this exact format:
{{
  "variations": [
    {{
      "headline": "Short, punchy headline",
      "body": "Compelling body copy (bullets with emojis OR emotional story)",
      "cta": "Action CTA"
    }}
  ]
}}"""

        # Use custom prompt if provided
        if request.customPrompt:
            prompt = request.customPrompt

        gateway = AIGateway.for_context(db, ctx)
        result = await gateway.chat("copy.generate", ChatRequest(
            messages=[Message.user(prompt)], max_tokens=COPY_MAX_TOKENS))
        # Parse JSON (markdown fences / surrounding prose tolerated)
        return parse_json(result.response.text, result.provider)

    except ai_errors.AIError as e:
        if e.kind == ai_errors.INVALID_RESPONSE:
            raise HTTPException(status_code=500, detail="Failed to parse AI response as JSON")
        raise to_http(e)
    except HTTPException:
        raise
    except Exception:
        # never print prompts, provider bodies or keys
        raise HTTPException(status_code=500, detail="Copy generation failed")


@router.post("/regenerate-field")
async def regenerate_field(request: FieldRegenerationRequest, db: Session = Depends(get_db),
                           ctx: OrgContext = Depends(require_org_permission("ads:write"))):
    """Regenerate a specific field (headline, body, or cta)"""

    try:
        field_prompts = {
            "headline": "Generate a new headline (under 40 characters)",
            "body": "Generate new body copy (under 125 characters for bullets, or up to 200 for storytelling)",
            "cta": "Generate a new call-to-action (under 20 characters)"
        }

        prompt = f"""You are an expert ad copywriter. {field_prompts.get(request.field, 'Generate new copy')}.

BRAND VOICE: {request.brand.get('voice', 'Professional and friendly')}
PRODUCT: {request.product.get('name')}
TARGET AUDIENCE: {request.profile.get('demographics', 'General audience')}
CAMPAIGN: {request.campaignDetails.get('offer')}

Current {request.field}: {request.currentValue}

Generate a DIFFERENT, fresh variation that:
1. Matches the brand voice
2. Is compelling and conversion-focused
3. Follows the character limits

Return ONLY the new {request.field} text, nothing else."""

        gateway = AIGateway.for_context(db, ctx)
        result = await gateway.chat("copy.regenerate_field", ChatRequest(
            messages=[Message.user(prompt)], max_tokens=512))
        new_value = result.response.text.strip().strip('"').strip("'")

        return {"newValue": new_value}

    except ai_errors.AIError as e:
        raise to_http(e)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Field regeneration failed")

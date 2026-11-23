"""
Ad Remix Service - Business logic for deconstructing and reconstructing ads.

AI calls go through the organization's AI Gateway (BYOK credential, configured model,
spending limits, usage accounting): there is no process-wide AI configuration here.
"""
import json
from typing import Any, Dict

from pydantic import ValidationError

from app.ai.errors import AIError, INVALID_RESPONSE
from app.ai.gateway import AIGateway
from app.ai.json_utils import parse_json
from app.ai.types import ChatRequest, ImagePart, Message
from app.schemas.ad_blueprint import AdBlueprint, AdConcept, BrandData
from app.prompts.ad_remix_prompts import build_deconstruction_prompt, build_reconstruction_prompt


async def deconstruct_template(gateway: AIGateway, image: ImagePart) -> AdBlueprint:
    """
    Analyze a template image (vision) and extract its structural blueprint.

    Args:
        gateway: the organization's AI Gateway (its own credential, model and budget)
        image: the template image bytes + mime type (loaded by the caller; see app.ai.media)

    Returns:
        AdBlueprint with extracted structure
    """
    prompt = build_deconstruction_prompt("the attached template image")
    result = await gateway.chat("ad_remix.deconstruct", ChatRequest(
        messages=[Message.user(prompt, image)], max_tokens=4096))
    try:
        return AdBlueprint(**parse_json(result.response.text, result.provider))
    except (ValidationError, TypeError):
        raise AIError(INVALID_RESPONSE, "blueprint does not match the expected structure", provider=result.provider)


async def reconstruct_ad(
    gateway: AIGateway,
    blueprint: AdBlueprint,
    brand_data: BrandData
) -> AdConcept:
    """
    Generate a new ad concept by applying brand data to a blueprint

    Args:
        gateway: the organization's AI Gateway
        blueprint: The structural blueprint to follow
        brand_data: The new brand/product information

    Returns:
        AdConcept with generated content
    """
    prompt = build_reconstruction_prompt(
        blueprint=blueprint.model_dump(),
        brand_name=brand_data.brand_name,
        brand_voice=brand_data.brand_voice or "",
        product_name=brand_data.product_name,
        product_description=brand_data.product_description,
        audience_demographics=brand_data.audience_demographics,
        audience_pain_points=brand_data.audience_pain_points or "",
        audience_goals=brand_data.audience_goals or "",
        campaign_offer=brand_data.campaign_offer,
        campaign_urgency=brand_data.campaign_urgency or "",
        campaign_messaging=brand_data.campaign_messaging
    )
    result = await gateway.chat("ad_remix.reconstruct", ChatRequest(
        messages=[Message.user(prompt)], max_tokens=4096))
    try:
        return AdConcept(**parse_json(result.response.text, result.provider))
    except (ValidationError, TypeError):
        raise AIError(INVALID_RESPONSE, "ad concept does not match the expected structure", provider=result.provider)


def extract_json_from_response(text: str) -> Dict[str, Any]:
    """
    Extract JSON from a response that might have markdown code blocks
    """
    # Try to find JSON in markdown code blocks
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end].strip()
    
    return json.loads(text)

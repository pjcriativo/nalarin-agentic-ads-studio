"""Registry of provider adapters. Adding a provider = one adapter class + one entry here.
(Provider names are protocol identifiers, not model names.)"""
from typing import Dict

from app.ai.adapters.anthropic import AnthropicAdapter
from app.ai.adapters.base import ProviderAdapter
from app.ai.adapters.gemini import GeminiAdapter
from app.ai.adapters.openai import OpenAIAdapter

PROVIDER_ADAPTERS: Dict[str, ProviderAdapter] = {
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
    "openai": OpenAIAdapter(),
}

PROVIDERS = tuple(PROVIDER_ADAPTERS)


def get_adapter(provider: str) -> ProviderAdapter:
    try:
        return PROVIDER_ADAPTERS[provider]
    except KeyError:
        raise ValueError(f"Unsupported AI provider: {provider}")

"""Cost arithmetic. Money is integer USD micros (millionths of a dollar); prices are USD
micros per ONE MILLION tokens, so the cost of N tokens is ``ceil(N * price / 1_000_000)``
(rounding UP: never under-count what is charged against a budget)."""
import math
from typing import Optional

from app.ai.types import ChatRequest, Usage

CHARS_PER_TOKEN = 3  # conservative (real text averages 3-4): the estimate must not under-reserve


def tokens_cost_micros(tokens: int, price_micros_per_mtok: Optional[int]) -> Optional[int]:
    if price_micros_per_mtok is None:
        return None
    return math.ceil(max(tokens, 0) * price_micros_per_mtok / 1_000_000)


def usage_cost_micros(usage: Usage, model) -> Optional[int]:
    """Cost computed from the provider-reported usage; None when the model has no price."""
    cached = max(min(usage.cached_input_tokens, usage.input_tokens), 0)
    cached_price = getattr(model, "cached_input_price_micros_per_mtok", None)
    if cached_price is None:
        cached = 0  # no cache price informed: every prompt token is charged at the input price
    cost_in = tokens_cost_micros(usage.input_tokens - cached, model.input_price_micros_per_mtok)
    cost_cached = tokens_cost_micros(cached, cached_price) if cached else 0
    cost_out = tokens_cost_micros(usage.output_tokens, model.output_price_micros_per_mtok)
    if cost_in is None or cost_out is None:
        return None
    return cost_in + cost_cached + cost_out


def estimate_cost_micros(request: ChatRequest, model) -> Optional[int]:
    """Worst case before the call: estimated prompt tokens + the full output allowance."""
    prompt_tokens = math.ceil(request.approx_input_chars() / CHARS_PER_TOKEN) + 16
    max_out = request.max_tokens
    if model.max_output_tokens:
        max_out = min(max_out, model.max_output_tokens)
    # worst case: the dearer of the two prices applies to the whole prompt
    prices = [p for p in (model.input_price_micros_per_mtok, getattr(model, "cached_input_price_micros_per_mtok", None)) if p is not None]
    cost_in = tokens_cost_micros(prompt_tokens, max(prices)) if model.input_price_micros_per_mtok is not None else None
    cost_out = tokens_cost_micros(max_out, model.output_price_micros_per_mtok)
    if cost_in is None or cost_out is None:
        return None
    return cost_in + cost_out


def usd_to_micros(usd) -> int:
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(usd)) * 1_000_000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def micros_to_usd(micros: Optional[int]) -> Optional[float]:
    return None if micros is None else micros / 1_000_000

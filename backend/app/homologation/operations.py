"""The ALLOWLIST: the only operations the homologation runner can execute, each with a fixed, tiny, non-sensitive payload.

AI operations use a fixed neutral prompt (no user, tenant or business data) and small ``max_tokens``. Google Ads
operations are READ ONLY: OAuth refresh, list accessible customers, GAQL SELECT (``customer`` / ``campaign`` structure /
a 7-day campaign performance), Keyword Planner ideas. There is deliberately no mutate/create/pause/enable call here
(a static test greps this module and the reader for them).
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

AI_PROVIDERS = ("anthropic", "gemini", "openai")

PROMPT_OK = "Reply with the single word OK."
PROMPT_TOOL = "Call the tool get_status with component set to 'homologation'. Do not answer in text."
PROMPT_TOOL_FOLLOWUP = "Now reply with the single word DONE."
PROMPT_JSON = 'Return a JSON object with exactly one key "status" whose value is the string "ok".'
SYSTEM_PROMPT = "You are a connectivity probe. Follow the instruction exactly and keep it minimal."
MAX_OUTPUT_TOKENS = 256     # room for hidden reasoning tokens of reasoning models, still a few hundred tokens at most
KEYWORD_SEEDS = ["running shoes"]
KEYWORD_LIMIT = 5


@dataclass(frozen=True)
class Operation:
    op_id: str
    system: str          # anthropic | gemini | openai | google_ads
    kind: str            # ai_free | ai_paid | gads_read
    description: str
    max_calls: int       # provider requests this operation can send
    needs: tuple         # env variable names that must be present (values are never printed)


def _ai_ops(provider: str) -> List[Operation]:
    p = provider.upper()
    base = (f"HOMOLOG_{p}_API_KEY",)
    paid = base + (f"HOMOLOG_{p}_MODEL", f"HOMOLOG_{p}_INPUT_PRICE_MICROS_PER_MTOK", f"HOMOLOG_{p}_OUTPUT_PRICE_MICROS_PER_MTOK")
    return [
        Operation(f"ai.{provider}.verify", provider, "ai_free", "Free read-only model listing: the key is accepted", 1, base),
        Operation(f"ai.{provider}.chat_minimal", provider, "ai_paid", "One minimal completion (fixed prompt): usage tokens and cost are reported", 1, paid),
        Operation(f"ai.{provider}.tool_call", provider, "ai_paid", "Forced tool call + tool_result round trip (2 requests): tool-calling contract", 2, paid),
        Operation(f"ai.{provider}.json_mode", provider, "ai_paid", "Structured JSON answer (fixed prompt): the answer parses to the expected object", 1, paid),
    ]


GADS_BASE = ("HOMOLOG_GADS_REFRESH_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_DEVELOPER_TOKEN")
GADS_ACCOUNT = GADS_BASE + ("HOMOLOG_GADS_CUSTOMER_ID",)

OPERATIONS: Dict[str, Operation] = {op.op_id: op for op in (
    *[op for p in AI_PROVIDERS for op in _ai_ops(p)],
    Operation("gads.oauth_refresh", "google_ads", "gads_read", "OAuth refresh-token exchange (no Ads API call)", 1, GADS_BASE),
    Operation("gads.accessible_customers", "google_ads", "gads_read", "CustomerService.list_accessible_customers (read)", 1, GADS_BASE),
    Operation("gads.account_info", "google_ads", "gads_read", "GAQL SELECT customer ... LIMIT 1 (through the MCC when given)", 1, GADS_ACCOUNT),
    Operation("gads.structure", "google_ads", "gads_read", "GAQL SELECT campaign / ad_group / ad_group_ad (structure, limited)", 3, GADS_ACCOUNT),
    Operation("gads.performance", "google_ads", "gads_read", "GAQL SELECT campaign metrics for the last 7 days (limited)", 1, GADS_ACCOUNT),
    Operation("gads.keyword_ideas", "google_ads", "gads_read", "Keyword Planner generate_keyword_ideas, 1 seed, limit 5 (estimates)", 1, GADS_ACCOUNT),
)}

# Operations that need another one to have been approved first (their input comes from it).
DEPENDS_ON: Dict[str, str] = {"gads.accessible_customers": "gads.oauth_refresh", "gads.account_info": "gads.oauth_refresh",
                              "gads.structure": "gads.account_info", "gads.performance": "gads.account_info",
                              "gads.keyword_ideas": "gads.account_info"}

def missing_env(op: Operation, env: Dict[str, str]) -> List[str]:
    return [name for name in op.needs if not env.get(name)]

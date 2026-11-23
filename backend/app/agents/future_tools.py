"""CONTRACTS of the future Google Ads tools. Nothing here executes, returns data or is offered to a model.

Why they exist now: the integration with the UI and the later Google Ads stage need a stable, reviewed shape
(arguments, result, required permission, approval) to build against. Every contract carries
``status="contract_only"``: the tool registry refuses to run them (``ToolUnavailable``), no agent lists them
in ``allowed_tools`` (a test enforces it) and ``GET /agents/tools`` reports them as planned, never as available.
No handler returns example rows: faking data "as if real" is exactly what this stage must not do.

Side effects (``side_effect``):
  read             reads account data, changes nothing
  draft_mutation   creates something that cannot spend (a PAUSED campaign), needs ``ads:draft`` + human review
  spend_mutation   can start or raise spending: needs ``ads:spend`` AND a consumed spend approval
                   (see app/core spend approvals); an agent may NEVER hold this tool on its own authority.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

STATUS_CONTRACT_ONLY = "contract_only"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateRange(_Args):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ read
class GetCampaignPerformanceArgs(_Args):
    customer_id: str = Field(pattern=r"^\d{10}$")
    date_range: DateRange
    campaign_ids: List[str] = Field(default_factory=list, max_length=50)


class CampaignPerformanceRow(BaseModel):
    campaign_id: str
    campaign_name: str
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float
    conversions_value: float


class GetCampaignPerformanceResult(BaseModel):
    customer_id: str
    currency_code: str
    rows: List[CampaignPerformanceRow]
    retrieved_at: str


class GetSearchTermsReportArgs(_Args):
    customer_id: str = Field(pattern=r"^\d{10}$")
    date_range: DateRange
    campaign_id: Optional[str] = None
    limit: int = Field(default=200, ge=1, le=1000)


class SearchTermRow(BaseModel):
    search_term: str
    campaign_id: str
    ad_group_id: str
    impressions: int
    clicks: int
    cost_micros: int
    conversions: float


class GetSearchTermsReportResult(BaseModel):
    customer_id: str
    rows: List[SearchTermRow]
    retrieved_at: str


class KeywordIdeasArgs(_Args):
    customer_id: str = Field(pattern=r"^\d{10}$")
    seed_keywords: List[str] = Field(min_length=1, max_length=20)
    language_code: str = Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    geo_target_ids: List[str] = Field(default_factory=list, max_length=20)


class KeywordIdea(BaseModel):
    keyword: str
    avg_monthly_searches: Optional[int]
    competition: Optional[Literal["low", "medium", "high"]]
    low_top_of_page_bid_micros: Optional[int]
    high_top_of_page_bid_micros: Optional[int]


class KeywordIdeasResult(BaseModel):
    ideas: List[KeywordIdea]
    retrieved_at: str


class GetAccountStructureArgs(_Args):
    customer_id: str = Field(pattern=r"^\d{10}$")


class GetAccountStructureResult(BaseModel):
    customer_id: str
    campaigns: List[Dict[str, Any]]
    retrieved_at: str


# ------------------------------------------------------------------ draft / spend (NOT for agents on their own authority)
class DraftCampaignArgs(_Args):
    customer_id: str = Field(pattern=r"^\d{10}$")
    name: str = Field(min_length=1, max_length=120)
    plan_task_id: str  # the agent task whose reviewed result this draft materializes


class DraftCampaignResult(BaseModel):
    campaign_id: str
    status: Literal["PAUSED"]


class ApplyChangeProposalArgs(_Args):
    customer_id: str = Field(pattern=r"^\d{10}$")
    proposal_id: str
    approval_id: str  # a CONSUMED spend approval for exactly this payload


class ApplyChangeProposalResult(BaseModel):
    applied: bool
    mutation_audit_id: str


@dataclass(frozen=True)
class FutureToolContract:
    name: str
    description: str
    args_model: Type[BaseModel]
    result_model: Type[BaseModel]
    side_effect: Literal["read", "draft_mutation", "spend_mutation"]
    required_permissions: tuple
    requires_human_approval: bool
    agent_may_hold: bool
    status: str = STATUS_CONTRACT_ONLY
    depends_on: str = "Google Ads integration (later stage); account link and OAuth of the organization"

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "status": self.status,
            "side_effect": self.side_effect, "required_permissions": list(self.required_permissions),
            "requires_human_approval": self.requires_human_approval, "agent_may_hold": self.agent_may_hold,
            "depends_on": self.depends_on,
            "arguments_schema": self.args_model.model_json_schema(),
            "result_schema": self.result_model.model_json_schema(),
        }


FUTURE_TOOL_CONTRACTS: Dict[str, FutureToolContract] = {c.name: c for c in (
    FutureToolContract("google_ads.get_campaign_performance", "Read campaign performance for a date range.",
                       GetCampaignPerformanceArgs, GetCampaignPerformanceResult, "read", ("ads:read",), False, True),
    FutureToolContract("google_ads.get_search_terms_report", "Read the search terms report.",
                       GetSearchTermsReportArgs, GetSearchTermsReportResult, "read", ("ads:read",), False, True),
    FutureToolContract("google_ads.keyword_ideas", "Read keyword ideas with measured volume and bid ranges (Keyword Planner).",
                       KeywordIdeasArgs, KeywordIdeasResult, "read", ("ads:read",), False, True),
    FutureToolContract("google_ads.get_account_structure", "Read campaigns, ad groups and assets of an account.",
                       GetAccountStructureArgs, GetAccountStructureResult, "read", ("ads:read",), False, True),
    FutureToolContract("google_ads.draft_campaign", "Materialize a reviewed plan as a PAUSED draft campaign.",
                       DraftCampaignArgs, DraftCampaignResult, "draft_mutation", ("ads:draft",), True, False),
    FutureToolContract("google_ads.apply_change_proposal", "Apply a human-approved change that can spend money.",
                       ApplyChangeProposalArgs, ApplyChangeProposalResult, "spend_mutation", ("ads:spend",), True, False),
)}

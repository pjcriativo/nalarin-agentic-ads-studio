"""Pydantic contracts: what each specialist must return, and the supervisor's plan and report.

Design rules:
* Every claim that carries data is labelled ``real`` (a tool of THIS run or data the requester supplied returned it),
  ``estimate`` (Google Keyword Planner's own estimate) or ``hypothesis`` (the model's idea), with its ``source``.
  Cited metrics are verified server-side against what the tools actually returned (app.agents.evidence): a made-up volume,
  CPC or conversion count fails validation and cannot be stored as a result. A hypothesis can never carry a number.
* Text limits that the ad platform enforces (30/90 characters) are enforced here too, so an invalid answer is
  rejected and repaired before it is ever stored as a result.
"""
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Medium = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Headline = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=90)]

MAX_PLAN_STEPS = 6

DataKind = Literal["real", "estimate", "hypothesis"]
Source = Literal["google_ads_api", "google_keyword_planner", "product_page", "supplied_context", "model_knowledge"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------- data provenance
class CitedMetric(_Out):
    """A number the output relies on, with the entity it belongs to, exactly as the tool returned it
    (e.g. entity ``campaign:123`` / ``keyword:running shoes`` / ``page:https://...``, name ``clicks``)."""
    entity: Short
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    value: float


class DataBasis(_Out):
    statement: Medium
    data_kind: DataKind
    source: Source
    metrics: List[CitedMetric] = Field(default_factory=list, max_length=10)


# ---------------------------------------------------------------------------- specialist outputs
class ProductAnalysis(_Out):
    summary: Long
    value_propositions: List[Medium] = Field(default_factory=list, max_length=15)
    audience_hypotheses: List[Medium] = Field(default_factory=list, max_length=15)
    objections: List[Medium] = Field(default_factory=list, max_length=15)
    claims_to_verify: List[Medium] = Field(default_factory=list, max_length=20)
    missing_information: List[Medium] = Field(default_factory=list, max_length=20)
    sources: List[Short] = Field(default_factory=list, max_length=10)
    data_basis: List[DataBasis] = Field(default_factory=list, max_length=30)   # what the page states = real; inferences = hypothesis


class KeywordCandidate(_Out):
    keyword: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    intent: Literal["informational", "commercial", "transactional", "navigational"]
    match_type_suggestion: Literal["exact", "phrase", "broad"]
    rationale: Medium
    # Default is the honest one: an idea of the model, no numbers. Planner figures must be cited and are verified.
    data_kind: DataKind = "hypothesis"
    source: Source = "model_knowledge"
    metrics: List[CitedMetric] = Field(default_factory=list, max_length=6)


class MarketResearch(_Out):
    summary: Long
    themes: List[Medium] = Field(default_factory=list, max_length=20)
    keyword_candidates: List[KeywordCandidate] = Field(default_factory=list, max_length=60)
    negative_keyword_candidates: List[Short] = Field(default_factory=list, max_length=40)
    alternatives_or_competitors: List[Short] = Field(default_factory=list, max_length=20)
    # Honesty marker: without a data tool these are model hypotheses, never measured demand.
    evidence: Literal["model_hypotheses", "provided_context", "fetched_pages", "keyword_planner", "account_data", "mixed"] = "model_hypotheses"
    verification_notice: Medium = ("Items labelled 'hypothesis' are unverified ideas of the model; only items labelled 'estimate' carry "
                                   "Google Keyword Planner figures (Google's estimates, not measurements), and 'real' carries account data.")
    data_basis: List[DataBasis] = Field(default_factory=list, max_length=30)


class AdGroupPlan(_Out):
    name: Short
    keyword_themes: List[Short] = Field(default_factory=list, max_length=20)
    landing_page: Optional[Short] = None
    notes: Optional[Medium] = None


class CampaignPlan(_Out):
    name: Short
    goal: Medium
    ad_groups: List[AdGroupPlan] = Field(min_length=1, max_length=15)


class SearchAdsStrategy(_Out):
    objective: Medium
    campaigns: List[CampaignPlan] = Field(min_length=1, max_length=10)
    bidding_recommendation: Long
    measurement_plan: List[Medium] = Field(default_factory=list, max_length=15)
    risks: List[Medium] = Field(default_factory=list, max_length=15)
    assumptions: List[Medium] = Field(default_factory=list, max_length=15)
    data_basis: List[DataBasis] = Field(default_factory=list, max_length=30)


class AdGroupCopy(_Out):
    ad_group: Short
    headlines: List[Headline] = Field(min_length=3, max_length=15)
    descriptions: List[Description] = Field(min_length=2, max_length=4)


class AdCopy(_Out):
    ad_groups: List[AdGroupCopy] = Field(min_length=1, max_length=10)
    notes: Optional[Medium] = None


class PolicyFinding(_Out):
    severity: Literal["info", "warning", "blocker"]
    item: Medium
    rule: Short
    suggestion: Medium


class PolicyReview(_Out):
    verdict: Literal["approved", "needs_changes", "blocked"]
    summary: Long
    findings: List[PolicyFinding] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _verdict_matches_findings(self):
        blockers = any(f.severity == "blocker" for f in self.findings)
        if blockers and self.verdict == "approved":
            raise ValueError("verdict 'approved' contradicts a blocker finding")
        return self


class PerformanceAnalysis(_Out):
    summary: Long
    findings: List[Medium] = Field(default_factory=list, max_length=20)
    hypotheses: List[Medium] = Field(default_factory=list, max_length=15)
    recommended_experiments: List[Medium] = Field(default_factory=list, max_length=15)
    data_limitations: List[Medium] = Field(min_length=1, max_length=15)
    data_basis: List[DataBasis] = Field(default_factory=list, max_length=40)   # every figure in `findings` must be backed here


# ---------------------------------------------------------------------------- supervisor
class PlanStep(_Out):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,24}$")]
    agent: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]
    objective: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1500)]
    depends_on: List[str] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)


class SupervisorPlan(_Out):
    summary: Medium
    steps: List[PlanStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)


class FinalReport(_Out):
    summary: Long
    key_results: List[Medium] = Field(default_factory=list, max_length=20)
    recommendations: List[Medium] = Field(default_factory=list, max_length=20)
    open_questions: List[Medium] = Field(default_factory=list, max_length=15)

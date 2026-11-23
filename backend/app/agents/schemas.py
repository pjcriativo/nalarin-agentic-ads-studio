"""Pydantic contracts: what each specialist must return, and the supervisor's plan and report.

Design rules:
* Nothing here has a field for a metric the agent could only make up (search volume, CPC, impressions...).
  Market and strategy agents give HYPOTHESES labelled as such; real figures arrive with the future
  Google Ads read tools (see app.agents.future_tools).
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


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------- specialist outputs
class ProductAnalysis(_Out):
    summary: Long
    value_propositions: List[Medium] = Field(default_factory=list, max_length=15)
    audience_hypotheses: List[Medium] = Field(default_factory=list, max_length=15)
    objections: List[Medium] = Field(default_factory=list, max_length=15)
    claims_to_verify: List[Medium] = Field(default_factory=list, max_length=20)
    missing_information: List[Medium] = Field(default_factory=list, max_length=20)
    sources: List[Short] = Field(default_factory=list, max_length=10)


class KeywordCandidate(_Out):
    keyword: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    intent: Literal["informational", "commercial", "transactional", "navigational"]
    match_type_suggestion: Literal["exact", "phrase", "broad"]
    rationale: Medium


class MarketResearch(_Out):
    summary: Long
    themes: List[Medium] = Field(default_factory=list, max_length=20)
    keyword_candidates: List[KeywordCandidate] = Field(default_factory=list, max_length=60)
    negative_keyword_candidates: List[Short] = Field(default_factory=list, max_length=40)
    alternatives_or_competitors: List[Short] = Field(default_factory=list, max_length=20)
    # Honesty marker: without a data tool these are model hypotheses, never measured demand.
    evidence: Literal["model_hypotheses", "provided_context", "fetched_pages", "mixed"] = "model_hypotheses"
    verification_notice: Medium = "Keyword ideas are unverified hypotheses: search volume, competition and cost were NOT measured."


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

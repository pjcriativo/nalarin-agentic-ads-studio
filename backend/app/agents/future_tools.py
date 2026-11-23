"""CONTRACTS of the Google Ads tools that are STILL not implemented: the two that can change an account.
(Etapa 02.5 turned the four READ contracts -- performance, search terms, keyword ideas, account structure -- into live
read-only tools: see app/agents/google_tools.py.)

Nothing here executes, returns data or is offered to a model.

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
from typing import Any, Dict, Literal, Type

from pydantic import BaseModel, ConfigDict, Field

STATUS_CONTRACT_ONLY = "contract_only"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    FutureToolContract("google_ads.draft_campaign", "Materialize a reviewed plan as a PAUSED draft campaign.",
                       DraftCampaignArgs, DraftCampaignResult, "draft_mutation", ("ads:draft",), True, False),
    FutureToolContract("google_ads.apply_change_proposal", "Apply a human-approved change that can spend money.",
                       ApplyChangeProposalArgs, ApplyChangeProposalResult, "spend_mutation", ("ads:spend",), True, False),
)}

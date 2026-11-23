"""AI Gateway: the single door through which the platform talks to an AI provider.

For one call (``AIGateway.chat``):

1. **Route**: the operation's primary model and ordered fallbacks come from the organization's
   own configuration (``ai_routing``; else its enabled models in creation order). Nothing about
   models is hard-coded. Candidates without an active credential, disabled, or lacking a needed
   capability (tools / vision / json) are skipped.
2. **Budget**: the worst-case cost is RESERVED atomically against the organization's daily/monthly
   limits (app.ai.budget). A limit that cannot be proven (model without price while a budget is
   active) fails CLOSED.
3. **Call** the provider through its adapter with the organization's own decrypted key.
4. **Account**: tokens, estimate, computed cost, user, model and operation are logged (no prompt,
   no completion, no secret); the reservation becomes the real cost.
5. **Interruption** (etapa 02.5): when the awaiting task is cancelled or times out while the provider call is in flight
   (``asyncio.CancelledError``), the provider may have processed -- and billed -- the request. The reservation is therefore
   NEVER released: it is charged at its estimate (``error_kind='interrupted'`` in the usage log) so the organization's limits
   stay conservative, and the estimate is exposed as ``interrupted_cost_micros`` so the caller can account it on the task.
6. **Fallback**: on a *recoverable* error (rate limit, timeout, provider unavailable) the next
   candidate is tried; every other error (auth, quota, invalid request, blocked content, unusable
   answer, budget) stops immediately and is raised as a redacted ``AIError``.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.ai import budget as budget_mod
from app.ai import credentials as cred_mod
from app.ai import errors as E
from app.ai import pricing
from app.ai.adapters import PROVIDER_ADAPTERS, ProviderAdapter
from app.ai.types import ChatRequest, ChatResponse
from app.core.config import settings
from app.models import AiModel, AiRouting, AiUsageLog

DEFAULT_OPERATION = "default"


@dataclass
class GatewayResult:
    response: ChatResponse
    model_row_id: str
    provider: str
    model_id: str
    attempts: int
    request_id: str
    cost_micros: Optional[int]


def _needs_ok(model: AiModel, needs: Dict[str, bool]) -> bool:
    return ((not needs["tools"] or model.supports_tools)
            and (not needs["vision"] or model.supports_vision)
            and (not needs["json"] or model.supports_json))


class AIGateway:
    def __init__(self, db: Session, organization_id: str, user_id: Optional[str] = None,
                 adapters: Optional[Dict[str, ProviderAdapter]] = None, timeout: Optional[float] = None):
        self.db = db
        self.org_id = organization_id
        self.user_id = user_id
        self.adapters = adapters if adapters is not None else PROVIDER_ADAPTERS
        self.timeout = timeout or settings.AI_TIMEOUT_SECONDS
        # Cost charged for the LAST call that was interrupted mid-flight (None: not interrupted, or no price known).
        self.interrupted_cost_micros: Optional[int] = None

    @classmethod
    def for_context(cls, db: Session, ctx, **kwargs) -> "AIGateway":
        user = getattr(ctx, "user", None)
        return cls(db, ctx.org.id, getattr(user, "id", None), **kwargs)

    # ---- routing ----------------------------------------------------------------
    def candidates(self, operation: str) -> List[AiModel]:
        """Ordered models for ``operation`` (primary first) that can actually be used now."""
        models = {m.id: m for m in self.db.query(AiModel).filter(
            AiModel.organization_id == self.org_id, AiModel.enabled.is_(True)).order_by(AiModel.created_at).all()}
        ordered: List[AiModel] = []
        routing = None
        for op in (operation, DEFAULT_OPERATION):
            routing = self.db.query(AiRouting).filter(
                AiRouting.organization_id == self.org_id, AiRouting.operation == op).first()
            if routing is not None:
                break
        if routing is not None:
            ids = ([routing.primary_model_id] if routing.primary_model_id else []) + list(routing.fallback_model_ids or [])
            seen = set()
            for model_id in ids:
                if model_id in models and model_id not in seen:
                    ordered.append(models[model_id])
                    seen.add(model_id)
        else:
            ordered = list(models.values())
        # only models whose provider has an active credential of THIS organization
        usable = []
        for model in ordered:
            if model.provider in self.adapters and cred_mod.active_credential(self.db, self.org_id, model.provider):
                usable.append(model)
        return usable

    # ---- logging ----------------------------------------------------------------
    def _log(self, request_id: str, attempt: int, operation: str, model: AiModel, credential_id: Optional[str],
             status: str, *, error_kind: Optional[str] = None, usage=None, estimate: Optional[int] = None,
             cost: Optional[int] = None, started: float = 0.0) -> None:
        self.db.add(AiUsageLog(
            organization_id=self.org_id, user_id=self.user_id, request_id=request_id, attempt=attempt,
            operation=operation[:80], provider=model.provider, model_id=model.model_id, credential_id=credential_id,
            status=status, error_kind=error_kind,
            input_tokens=getattr(usage, "input_tokens", 0), output_tokens=getattr(usage, "output_tokens", 0),
            cached_input_tokens=getattr(usage, "cached_input_tokens", 0), reasoning_tokens=getattr(usage, "reasoning_tokens", 0),
            estimated_cost_micros=estimate, cost_micros=cost,
            latency_ms=int((time.monotonic() - started) * 1000) if started else None,
        ))
        self.db.commit()

    def _charge_interrupted(self, request_id, attempt, operation, model, credential, reserved, estimate, started) -> None:
        """Best effort and synchronous (it runs while the task is being cancelled): never raises, always lets the
        cancellation continue. Unknown price => nothing to charge, but the reservation (if any) is still settled at its
        estimate by the abandoned-reservation recovery if this bookkeeping itself fails."""
        self.interrupted_cost_micros = estimate
        try:
            if reserved:
                budget_mod.settle(self.db, self.org_id, reserved, estimate or 0)
            self._log(request_id, attempt, operation, model, getattr(credential, "id", None), "error",
                      error_kind="interrupted", estimate=estimate, cost=estimate, started=started)
        except Exception:  # noqa: BLE001
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass

    # ---- the call ---------------------------------------------------------------
    async def chat(self, operation: str, request: ChatRequest) -> GatewayResult:
        self.interrupted_cost_micros = None
        needs = request.needs()
        candidates = self.candidates(operation)
        if not candidates:
            raise E.AIError(E.NOT_CONFIGURED, "no usable model for this operation")
        capable = [m for m in candidates if _needs_ok(m, needs)]
        if not capable:
            raise E.AIError(E.NOT_CONFIGURED, "no configured model supports the required capabilities "
                            + ",".join(k for k, v in needs.items() if v))

        request_id = str(uuid.uuid4())
        last_error: Optional[E.AIError] = None
        for attempt, model in enumerate(capable, start=1):
            started = time.monotonic()
            adapter = self.adapters[model.provider]
            credential = None
            reserved: list = []
            estimate = pricing.estimate_cost_micros(request, model)
            try:
                if budget_mod.enabled_periods(self.db, self.org_id):
                    if estimate is None:
                        raise E.AIError(E.BUDGET_EXCEEDED, "model has no price: cannot enforce the spending limit")
                    reserved = budget_mod.reserve(self.db, self.org_id, estimate, request_id=request_id)
                credential, key = cred_mod.get_plain_key(self.db, self.org_id, model.provider)
                response = await adapter.chat(request, model.model_id, key, self.timeout)
            except E.AIError as exc:
                if reserved:
                    budget_mod.release(self.db, self.org_id, reserved)
                if exc.kind == E.BUDGET_EXCEEDED:
                    raise  # our own limit: not a model problem, no attempt was made
                self._log(request_id, attempt, operation, model, getattr(credential, "id", None), "error",
                          error_kind=exc.kind, estimate=estimate, started=started)
                last_error = exc
                if exc.fallback_eligible:
                    continue
                raise
            except asyncio.CancelledError:
                # Cancelled / timed out while the provider may already be processing: charge, never release.
                self._charge_interrupted(request_id, attempt, operation, model, credential, reserved, estimate, started)
                raise
            except Exception:  # an adapter bug must never escape with provider/secret detail
                if reserved:
                    budget_mod.release(self.db, self.org_id, reserved)
                self._log(request_id, attempt, operation, model, getattr(credential, "id", None), "error",
                          error_kind=E.UNKNOWN, estimate=estimate, started=started)
                raise E.AIError(E.UNKNOWN, "unexpected adapter failure", provider=model.provider) from None

            cost = pricing.usage_cost_micros(response.usage, model)
            if reserved:
                budget_mod.settle(self.db, self.org_id, reserved, cost or 0)
            self._log(request_id, attempt, operation, model, credential.id, "success", usage=response.usage,
                      estimate=estimate, cost=cost, started=started)
            return GatewayResult(response=response, model_row_id=model.id, provider=model.provider,
                                 model_id=model.model_id, attempts=attempt, request_id=request_id, cost_micros=cost)

        assert last_error is not None
        raise last_error

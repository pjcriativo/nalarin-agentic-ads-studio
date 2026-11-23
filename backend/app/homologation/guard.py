"""Safety rails of the homologation runner: authorization, allowlist, cost ledger, redaction, report.

* AUTHORIZATION: real calls need ``HOMOLOG_AUTHORIZATION == AUTHORIZATION_PHRASE`` in the environment AND ``--execute``.
* ALLOWLIST: only operations declared in ``operations.OPERATIONS`` can run; each is read-only on the Google side
  (GAQL SELECT, Keyword Planner ideas, OAuth refresh, list accessible customers) and a tiny fixed prompt on the AI side.
* COST: every paid call is priced BEFORE it is sent (worst case = estimated prompt + the full output allowance, using the
  operator-supplied price). A call that would cross the per-operation or the total cap is refused, never sent.
* REDACTION: every string that reaches a report/log passes ``Redactor`` (exact secret values + token-shaped patterns).
  Reports hold statuses, counts, tokens and costs: never prompts, completions, keys, tokens or account data.
"""
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

AUTHORIZATION_PHRASE = "I_AUTHORIZE_REAL_CALLS_PHASE_B"
AUTH_ENV = "HOMOLOG_AUTHORIZATION"

DEFAULT_MAX_TOTAL_USD = 0.50
DEFAULT_MAX_OP_USD = 0.05

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                  # OpenAI / Anthropic style keys
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),                # Google API keys
    re.compile(r"ya29\.[0-9A-Za-z_\-]{10,}"),              # Google OAuth access tokens
    re.compile(r"1//[0-9A-Za-z_\-]{10,}"),                 # Google OAuth refresh tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|authorization)(\"?\s*[:=]\s*\"?)[^\s\",}]{6,}"),
]


class Redactor:
    """Masks exact secret values given at runtime plus token-shaped strings."""

    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def add(self, *secrets: str) -> None:
        self.__init__([*self._secrets, *secrets])  # type: ignore[misc]

    def __call__(self, text) -> str:
        out = str(text)
        for secret in self._secrets:
            out = out.replace(secret, "[REDACTED]")
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub(lambda m: "[REDACTED]" if m.lastindex is None else f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        return out


class NotAuthorized(Exception):
    pass


class CostCapExceeded(Exception):
    pass


def authorized(env: Optional[Dict[str, str]] = None) -> bool:
    return (env if env is not None else os.environ).get(AUTH_ENV, "") == AUTHORIZATION_PHRASE


def require_authorization(execute: bool, env: Optional[Dict[str, str]] = None) -> None:
    if not execute:
        raise NotAuthorized("--execute was not given: nothing is sent (use `plan` to inspect)")
    if not authorized(env):
        raise NotAuthorized(f"{AUTH_ENV} does not hold the authorization phrase: real calls are not authorized")


def usd_to_micros(usd: float) -> int:
    return int(round(usd * 1_000_000))


@dataclass
class CostLedger:
    """Hard spending cap for one homologation run (USD micros)."""
    max_total_micros: int = usd_to_micros(DEFAULT_MAX_TOTAL_USD)
    max_op_micros: int = usd_to_micros(DEFAULT_MAX_OP_USD)
    spent_micros: int = 0
    reserved: List[int] = field(default_factory=list)

    def check(self, worst_case_micros: Optional[int], label: str = "operation") -> None:
        if worst_case_micros is None:
            raise CostCapExceeded(f"{label}: no price supplied for the model, so the cost cap cannot be enforced (refused)")
        if worst_case_micros > self.max_op_micros:
            raise CostCapExceeded(f"{label}: worst case {worst_case_micros} > per-operation cap {self.max_op_micros} micros")
        if self.spent_micros + worst_case_micros > self.max_total_micros:
            raise CostCapExceeded(f"{label}: would exceed the total cap ({self.max_total_micros} micros)")

    def charge(self, micros: Optional[int], fallback_worst_case: int) -> int:
        """Record the real cost; when it is unknown, charge the worst case (never under-count)."""
        value = micros if micros is not None else fallback_worst_case
        self.spent_micros += value
        return value


def worst_case_micros(prompt_chars: int, max_output_tokens: int, in_price: Optional[int], out_price: Optional[int]) -> Optional[int]:
    """Same conservative arithmetic as app.ai.pricing (3 chars/token, +16, rounded up)."""
    if in_price is None or out_price is None:
        return None
    prompt_tokens = math.ceil(prompt_chars / 3) + 16
    return math.ceil(prompt_tokens * in_price / 1_000_000) + math.ceil(max_output_tokens * out_price / 1_000_000)


# ---- report -------------------------------------------------------------------------------------------
PASSED, FAILED, NOT_TESTED = "approved", "failed", "not_tested"


@dataclass
class OpResult:
    op_id: str
    system: str
    status: str                      # approved | failed | not_tested
    detail: str = ""                 # short, redacted, no payloads
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_micros: Optional[int] = None
    checks: Dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", {})}

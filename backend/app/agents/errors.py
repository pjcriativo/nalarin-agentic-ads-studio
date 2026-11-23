"""Reasons an agent task or run ends badly. ``kind`` is a stable machine value; ``message`` is short, redacted
and safe to show to the organization (never a provider body, never a secret)."""
from typing import Optional

from app.agents.security import redact_secrets, truncate

CANCELLED = "cancelled"
TIMEOUT = "timeout"
STEP_LIMIT = "step_limit"
TOOL_LIMIT = "tool_limit"
LOOP_DETECTED = "loop_detected"
INVALID_OUTPUT = "invalid_output"
INVALID_PLAN = "invalid_plan"
SUBTASK_FAILED = "subtask_failed"
COST_LIMIT = "cost_limit"
LOST_LEASE = "lost_lease"
AUTHORIZATION = "authorization"
AGENT_DISABLED = "agent_disabled"
INTERRUPTED = "interrupted"
INTERNAL = "internal"
AI_PREFIX = "ai_"  # followed by an AI Gateway error kind (authentication, quota, budget_exceeded, not_configured...)


class AgentError(Exception):
    def __init__(self, kind: str, message: str = "", *, detail: Optional[dict] = None):
        self.kind = kind
        self.message = truncate(redact_secrets(message or kind), 300)
        self.detail = detail or {}
        super().__init__(f"{kind}: {self.message}")

    def __repr__(self) -> str:
        return f"AgentError(kind={self.kind!r})"


class LostLease(Exception):
    """This executor no longer owns the task (a recovery re-queued it, or it was cancelled/finished elsewhere)."""

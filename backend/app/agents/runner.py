"""One agent run: a bounded model <-> tools loop on top of the AI Gateway.

Guard rails, all enforced HERE (the model cannot opt out of any):
* ``max_steps`` model turns, ``max_tool_calls`` tool executions, a wall-clock ``deadline``, and the task's cost cap;
* loop prevention: the same (tool, arguments) more than ``max_repeated_calls`` times is refused, and a second
  refusal ends the run with ``loop_detected``;
* cancellation and the lease are checked before every model turn (``TaskStore.checkpoint``);
* a tool call is executed only if the agent's allow-list contains it, the requesting user's role permits it and
  its arguments validate against the Pydantic model; failures are returned to the model as tool errors;
* tool output is redacted, truncated, scanned for injection and wrapped as untrusted data before the model sees it;
* the final answer must parse and validate against the agent's output schema (one repair turn, then ``invalid_output``).
Budgets, credentials, routing and fallbacks are the AI Gateway's: this module never touches a provider.
"""
import asyncio
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import ValidationError

from app.agents import errors as AE
from app.agents.errors import AgentError
from app.agents.registry import AgentDefinition, AgentLimits, build_system_prompt, output_contract
from app.agents.security import canonical_args, dumps, redact_secrets, scan_injection, truncate, wrap_untrusted
from app.agents.store import TaskStore, utcnow
from app.agents.tools import ToolContext, ToolDenied, ToolUnavailable, authorize, get_tool
from app.ai import errors as AIE
from app.ai.json_utils import parse_json
from app.ai.types import ChatRequest, Message, TextPart, ToolResult, ToolSpec

MAX_TOOL_OUTPUT_CHARS = 6000
TOOL_TIMEOUT_SECONDS = 30
Problems = Callable[[Any], List[str]]
EventSink = Callable[[str, Dict[str, Any]], None]


@dataclass
class RunOutcome:
    output: Dict[str, Any]
    steps: int
    cost_micros: int
    input_tokens: int
    output_tokens: int


class AgentRunner:
    def __init__(self, *, gateway, store: TaskStore, tool_ctx: ToolContext, defn: AgentDefinition, limits: AgentLimits,
                 deadline: datetime, max_cost_micros: Optional[int], run_id: Optional[str], phase: str = "run",
                 post_validate: Optional[Problems] = None, on_event: Optional[EventSink] = None,
                 clock: Callable[[], datetime] = utcnow):
        self.gateway, self.store, self.tool_ctx, self.defn, self.limits = gateway, store, tool_ctx, defn, limits
        self.deadline, self.max_cost, self.run_id, self.phase = deadline, max_cost_micros, run_id, phase
        self.post_validate, self.on_event, self.clock = post_validate, on_event or (lambda *_: None), clock
        self.steps = self.cost = self.tin = self.tout = 0
        self.task_cost = 0

    # ------------------------------------------------------------------ guards
    def _remaining(self) -> float:
        return (self.deadline - self.clock()).total_seconds()

    def _guard(self) -> None:
        if self.store.checkpoint():
            raise AgentError(AE.CANCELLED, "cancelled by the requester")
        if self._remaining() <= 0:
            raise AgentError(AE.TIMEOUT, "the task ran out of time")
        if self.max_cost is not None and self.task_cost >= self.max_cost:
            raise AgentError(AE.COST_LIMIT, "the task cost limit was reached")

    # ------------------------------------------------------------------ model call
    async def _chat(self, request: ChatRequest):
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(self.gateway.chat(self.defn.operation, request), timeout=max(self._remaining(), 0.001))
        except asyncio.TimeoutError:
            cost = getattr(self.gateway, "interrupted_cost_micros", None)        # what the gateway charged for the abandoned call
            self.cost += cost or 0
            self._step("llm", status="error", error_kind=AE.TIMEOUT, cost_micros=cost, latency_ms=int((time.monotonic() - started) * 1000))
            raise AgentError(AE.TIMEOUT, "the model call exceeded the remaining time") from None
        except asyncio.CancelledError:
            # worker shutdown (or any cancellation) while the call was in flight: keep its charged cost on the task
            cost = getattr(self.gateway, "interrupted_cost_micros", None)
            self.cost += cost or 0
            try:
                self._step("llm", status="error", error_kind="interrupted", cost_micros=cost,
                           latency_ms=int((time.monotonic() - started) * 1000))
            except Exception:  # noqa: BLE001 - e.g. the lease is already lost; the cancellation must proceed
                pass
            raise
        except AIE.AIError as exc:
            self._step("llm", status="error", error_kind=exc.kind, latency_ms=int((time.monotonic() - started) * 1000))
            raise AgentError(AE.AI_PREFIX + exc.kind, exc.message) from None
        self.cost += result.cost_micros or 0
        self.tin += result.response.usage.input_tokens
        self.tout += result.response.usage.output_tokens
        self._step("llm", output={"text": truncate(redact_secrets(result.response.text or ""), 800),
                                  "tool_calls": [c.name for c in result.response.tool_calls], "finish": result.response.finish_reason},
                   request_id=result.request_id, provider=result.provider, model_id=result.model_id,
                   input_tokens=result.response.usage.input_tokens, output_tokens=result.response.usage.output_tokens,
                   cost_micros=result.cost_micros, latency_ms=int((time.monotonic() - started) * 1000))
        return result

    def _step(self, kind: str, **fields) -> None:
        name = fields.pop("name", None) or f"{self.defn.operation}:{self.phase}"
        _, self.task_cost = self.store.add_step(kind=kind, run_id=self.run_id, name=name, **fields)
        self.steps += 1

    # ------------------------------------------------------------------ tools
    def _tool_error(self, call, kind: str, message: str) -> ToolResult:
        self._step("tool", name=call.name, status="error", error_kind=kind, input=call.arguments, output={"error": message})
        return ToolResult(call.id, wrap_untrusted(call.name, dumps({"error": message}), []), is_error=True)

    async def _run_tool(self, call, seen: Counter, state: Dict[str, int]) -> ToolResult:
        if call.name not in self.defn.allowed_tools:
            self.on_event("agent.tool.denied", {"tool": call.name, "agent": self.defn.key, "reason": "not_allowed_for_agent"})
            return self._tool_error(call, "tool_not_allowed", f"tool '{call.name}' is not available to this agent")
        try:
            tool = get_tool(call.name)
            authorize(tool, self.tool_ctx)
        except ToolUnavailable as exc:
            return self._tool_error(call, "tool_unavailable", str(exc))
        except ToolDenied as exc:
            self.on_event("agent.tool.denied", {"tool": call.name, "agent": self.defn.key, "reason": "permission"})
            return self._tool_error(call, "permission_denied", str(exc))
        if not isinstance(call.arguments, dict):
            return self._tool_error(call, "invalid_arguments", "arguments must be a JSON object")
        signature = call.name + canonical_args(call.arguments)
        seen[signature] += 1
        if seen[signature] > self.limits.max_repeated_calls:
            state["repeats"] += 1
            if state["repeats"] >= 2:
                raise AgentError(AE.LOOP_DETECTED, f"the agent kept repeating the same call to {call.name}")
            return self._tool_error(call, "repeated_call", "this exact call was already made; use its previous result or change approach")
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()[:6])
            return self._tool_error(call, "invalid_arguments", truncate(problems, 400))
        started = time.monotonic()
        try:
            output = await asyncio.wait_for(tool.handler(self.tool_ctx, args), timeout=TOOL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return self._tool_error(call, "tool_timeout", "the tool timed out")
        except ToolUnavailable as exc:
            return self._tool_error(call, "tool_unavailable", str(exc))
        except Exception:
            return self._tool_error(call, "tool_failed", "the tool failed")  # never echo exception text (could carry data)
        raw = redact_secrets(dumps(output))
        flags = scan_injection(raw)
        if flags:
            self.on_event("agent.injection.suspected", {"tool": call.name, "agent": self.defn.key, "rules": ",".join(flags)})
        shown = truncate(raw, MAX_TOOL_OUTPUT_CHARS)
        self._step("tool", name=call.name, input=call.arguments, output={"preview": truncate(raw, 1500)}, injection=bool(flags),
                   latency_ms=int((time.monotonic() - started) * 1000))
        return ToolResult(call.id, wrap_untrusted(call.name, shown, flags))

    # ------------------------------------------------------------------ the loop
    def _validate_final(self, text: str):
        try:
            data = parse_json(text, "")
        except AIE.AIError:
            return None, ["the answer is not a single valid JSON object"]
        try:
            model = self.defn.output_model.model_validate(data)
        except ValidationError as exc:
            return None, [f"{'.'.join(str(p) for p in e['loc']) or 'answer'}: {e['msg']}" for e in exc.errors()[:8]]
        if self.post_validate:
            problems = self.post_validate(model)
            if problems:
                return None, problems[:8]
        return model, []

    async def run(self, user_prompt: str) -> RunOutcome:
        specs = [ToolSpec(t.name, t.description, t.spec_parameters()) for t in (get_tool(n) for n in self.defn.allowed_tools)]
        system = build_system_prompt(self.defn, output_contract(self.defn.output_model))
        messages: List[Message] = [Message.user(user_prompt)]
        seen: Counter = Counter()
        state = {"repeats": 0}
        tool_calls = repairs = 0
        for _turn in range(self.limits.max_steps):
            self._guard()
            result = await self._chat(ChatRequest(messages=messages, system=system, tools=specs,
                                                  max_tokens=self.limits.max_output_tokens))
            response = result.response
            if response.tool_calls and specs:
                messages.append(Message("assistant", ([TextPart(response.text)] if response.text else []) + list(response.tool_calls)))
                results = []
                for call in response.tool_calls:
                    tool_calls += 1
                    if tool_calls > self.limits.max_tool_calls:
                        raise AgentError(AE.TOOL_LIMIT, "the agent used more tool calls than allowed")
                    results.append(await self._run_tool(call, seen, state))
                messages.append(Message.tool_results(*results))
                continue
            model, problems = self._validate_final(response.text or "")
            if model is not None:
                return RunOutcome(model.model_dump(mode="json"), self.steps, self.cost, self.tin, self.tout)
            if repairs >= 1:
                raise AgentError(AE.INVALID_OUTPUT, "the final answer did not match the output contract: " + "; ".join(problems[:3]))
            repairs += 1
            self._step("system", name="output_repair", status="error", error_kind=AE.INVALID_OUTPUT, output={"problems": problems})
            messages.append(Message.assistant(response.text or "(empty)"))
            messages.append(Message.user("Your answer is not valid: " + "; ".join(problems)
                                         + ". Reply again with ONLY the corrected JSON object that matches the output contract."))
        raise AgentError(AE.STEP_LIMIT, f"the agent did not finish within {self.limits.max_steps} steps")

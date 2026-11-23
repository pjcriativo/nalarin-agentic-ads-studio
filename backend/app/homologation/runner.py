"""Executes allowlisted operations (Phase B) or describes them (``plan``/``preflight``, offline).

No operation here writes to any ad platform. AI calls go through the SAME provider adapters the AI Gateway uses (so the
wire contract under test is the production one) with the operator's own key and prices; the cost cap is enforced
before every paid request. Reports contain only statuses, counts, tokens, costs and short redacted details.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional

from app.ai import errors as E
from app.ai import pricing
from app.ai.adapters import PROVIDER_ADAPTERS
from app.ai.json_utils import parse_json
from app.ai.types import ChatRequest, Message, ToolResult, ToolSpec, Usage
from app.homologation import guard
from app.homologation import operations as O
from app.homologation.guard import FAILED, NOT_TESTED, PASSED, CostLedger, OpResult, Redactor

TOOL = ToolSpec("get_status", "Report the status of a component.",
                {"type": "object", "properties": {"component": {"type": "string"}}, "required": ["component"]})


def _secret_values(env: Dict[str, str]) -> List[str]:
    markers = ("KEY", "TOKEN", "SECRET", "REFRESH", "PASSWORD", "AUTHORIZATION")
    return [v for k, v in env.items() if v and any(t in k.upper() for t in markers)]


def _model_for(provider: str, env: Dict[str, str]):
    p = provider.upper()

    def price(name):
        raw = env.get(f"HOMOLOG_{p}_{name}_PRICE_MICROS_PER_MTOK")
        return int(raw) if raw and raw.isdigit() else None
    return SimpleNamespace(model_id=env.get(f"HOMOLOG_{p}_MODEL", ""), input_price_micros_per_mtok=price("INPUT"),
                           output_price_micros_per_mtok=price("OUTPUT"), cached_input_price_micros_per_mtok=None,
                           max_output_tokens=None)


class Runner:
    def __init__(self, env: Dict[str, str], ledger: Optional[CostLedger] = None, only: Optional[List[str]] = None):
        self.env = env
        self.ledger = ledger or CostLedger()
        self.redact = Redactor(_secret_values(env))
        self.only = set(only) if only else None
        self.results: Dict[str, OpResult] = {}
        self.timeout = float(env.get("HOMOLOG_TIMEOUT_SECONDS", "45"))
        self.state: Dict[str, object] = {}

    # ---- helpers -----------------------------------------------------------------------------------
    def _ai_request(self, prompt: str, **kw) -> ChatRequest:
        return ChatRequest(messages=[Message.user(prompt)], system=O.SYSTEM_PROMPT, max_tokens=O.MAX_OUTPUT_TOKENS, **kw)

    async def _paid_call(self, provider: str, request: ChatRequest, label: str):
        """Price check -> one real request -> ledger. Returns (response, cost_micros)."""
        model = _model_for(provider, self.env)
        worst = pricing.estimate_cost_micros(request, model)
        self.ledger.check(worst, label)
        key = self.env[f"HOMOLOG_{provider.upper()}_API_KEY"]
        response = await PROVIDER_ADAPTERS[provider].chat(request, model.model_id, key, self.timeout)
        cost = self.ledger.charge(pricing.usage_cost_micros(response.usage, model), worst or 0)
        return response, cost

    def _done(self, op: O.Operation, status: str, started: float, detail: str = "", usage: Optional[Usage] = None,
              cost: Optional[int] = None, checks: Optional[Dict[str, bool]] = None) -> OpResult:
        result = OpResult(op.op_id, op.system, status, self.redact(detail)[:300], int((time.monotonic() - started) * 1000),
                          getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None), cost, checks or {})
        self.results[op.op_id] = result
        return result

    # ---- AI operations ------------------------------------------------------------------------------
    async def _ai(self, op: O.Operation) -> OpResult:
        provider, kind = op.system, op.op_id.rsplit(".", 1)[1]
        started = time.monotonic()
        key = self.env[f"HOMOLOG_{provider.upper()}_API_KEY"]
        adapter = PROVIDER_ADAPTERS[provider]
        if kind == "verify":
            models = await adapter.list_models(key, self.timeout)
            configured = self.env.get(f"HOMOLOG_{provider.upper()}_MODEL")
            listed = any(m["model_id"] == configured for m in models) if configured else None
            checks = {"key_accepted": True}
            if listed is not None:
                checks["configured_model_is_listed"] = listed
            return self._done(op, PASSED if all(checks.values()) else FAILED, started, f"{len(models)} chat models listed", checks=checks)
        if kind == "chat_minimal":
            response, cost = await self._paid_call(provider, self._ai_request(O.PROMPT_OK), op.op_id)
            checks = {"text_present": bool(response.text.strip()),
                      "usage_reported": response.usage.input_tokens > 0 and response.usage.output_tokens > 0,
                      "finish_normal": response.finish_reason in ("stop", "length"), "cost_computed": cost is not None}
            return self._done(op, PASSED if all(checks.values()) else FAILED, started, f"finish={response.finish_reason}",
                              response.usage, cost, checks)
        if kind == "json_mode":
            response, cost = await self._paid_call(provider, self._ai_request(O.PROMPT_JSON, json_mode=True), op.op_id)
            try:
                data = parse_json(response.text, provider)
                parsed = isinstance(data, dict) and data.get("status") == "ok"
            except E.AIError:
                parsed = False
            checks = {"json_parsed_to_expected_object": parsed, "usage_reported": response.usage.output_tokens > 0}
            return self._done(op, PASSED if all(checks.values()) else FAILED, started, f"finish={response.finish_reason}",
                              response.usage, cost, checks)
        # tool_call: turn 1 forces the tool; turn 2 returns the result (replays provider_data such as Gemini's thoughtSignature)
        first = self._ai_request(O.PROMPT_TOOL, tools=[TOOL], tool_choice="required")
        r1, c1 = await self._paid_call(provider, first, op.op_id + "#1")
        call = r1.tool_calls[0] if r1.tool_calls else None
        checks = {"tool_call_returned": call is not None, "tool_name_matches": bool(call and call.name == "get_status"),
                  "arguments_are_object": bool(call and isinstance(call.arguments.get("component"), str))}
        if call is None:
            return self._done(op, FAILED, started, "no tool call returned", r1.usage, c1, checks)
        messages = [Message.user(O.PROMPT_TOOL), Message("assistant", list(r1.tool_calls)),
                    Message.tool_results(ToolResult(call.id, json.dumps({"status": "operational"})))]
        second = ChatRequest(messages=messages, system=O.SYSTEM_PROMPT, tools=[TOOL], tool_choice="none",
                             max_tokens=O.MAX_OUTPUT_TOKENS)
        r2, c2 = await self._paid_call(provider, second, op.op_id + "#2")
        checks["tool_result_round_trip_answered"] = bool(r2.text.strip())
        usage = Usage(r1.usage.input_tokens + r2.usage.input_tokens, r1.usage.output_tokens + r2.usage.output_tokens)
        return self._done(op, PASSED if all(checks.values()) else FAILED, started, f"finish={r2.finish_reason}", usage, c1 + c2, checks)

    # ---- Google Ads operations (READ ONLY) -------------------------------------------------------------
    def _digits(self, name: str) -> str:
        return "".join(c for c in self.env.get(name, "") if c.isdigit())

    def _reader(self):
        from app.services.google_ads_reader import GoogleAdsReader
        return GoogleAdsReader(self.env["HOMOLOG_GADS_REFRESH_TOKEN"], self._digits("HOMOLOG_GADS_CUSTOMER_ID"),
                               self._digits("HOMOLOG_GADS_LOGIN_CUSTOMER_ID") or None)

    def _configure_google(self) -> None:
        from app.core.config import settings
        for name in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_DEVELOPER_TOKEN"):
            setattr(settings, name, self.env[name])

    async def _gads(self, op: O.Operation) -> OpResult:
        from app.services import google_ads_reader as reader
        from app.services.google_ads_oauth import GoogleOAuthError, refresh_access_token
        started, kind = time.monotonic(), op.op_id.split(".", 1)[1]
        self._configure_google()
        try:
            if kind == "oauth_refresh":
                data = await refresh_access_token(self.env["HOMOLOG_GADS_REFRESH_TOKEN"], self.env["GOOGLE_ADS_CLIENT_ID"],
                                                  self.env["GOOGLE_ADS_CLIENT_SECRET"])
                checks = {"access_token_issued": bool(data.get("access_token")),
                          "adwords_scope_granted": "auth/adwords" in str(data.get("scope", ""))}
                return self._done(op, PASSED if all(checks.values()) else FAILED, started, "refresh exchange answered", checks=checks)
            if kind == "accessible_customers":
                def call():
                    client = reader.CLIENT_FACTORY(self.env["HOMOLOG_GADS_REFRESH_TOKEN"], None)
                    names = client.get_service("CustomerService").list_accessible_customers().resource_names
                    return [n.split("/")[-1] for n in names]
                ids = await asyncio.wait_for(asyncio.to_thread(call), self.timeout)
                target, mcc = self._digits("HOMOLOG_GADS_CUSTOMER_ID"), self._digits("HOMOLOG_GADS_LOGIN_CUSTOMER_ID")
                checks = {"at_least_one_customer": bool(ids)}
                if target:  # a client account reached THROUGH an MCC is not listed directly: the manager must be
                    checks["target_or_its_manager_accessible"] = target in ids or (bool(mcc) and mcc in ids)
                return self._done(op, PASSED if all(checks.values()) else FAILED, started, f"{len(ids)} accessible customers", checks=checks)
            rdr = self._reader()
            if kind == "account_info":
                info = await rdr.account()
                checks = {"account_found": bool(info.get("found")), "currency_present": bool(info.get("currency_code")),
                          "provenance_real": info["provenance"]["data_kind"] == "real"}
                return self._done(op, PASSED if all(checks.values()) else FAILED, started,
                                  f"manager_account={info.get('is_manager_account')} test_account={info.get('test_account')}", checks=checks)
            if kind == "structure":
                data = await rdr.structure([], limit=5)
                return self._done(op, PASSED, started, "structure query ok (content not recorded)",
                                  checks={"query_ok": True, "provenance_real": data["provenance"]["data_kind"] == "real"})
            if kind == "performance":
                data = await rdr.performance("campaign", reader.resolve_range(None, None), [], limit=5)
                return self._done(op, PASSED, started, "performance query ok (metrics not recorded)",
                                  checks={"query_ok": True, "provenance_real": data["provenance"]["data_kind"] == "real"})
            if kind == "keyword_ideas":
                geo = [g for g in self.env.get("HOMOLOG_GADS_GEO_IDS", "2840").split(",") if g]
                data = await rdr.keyword_ideas(O.KEYWORD_SEEDS, self.env.get("HOMOLOG_GADS_LANGUAGE", "en"), geo, O.KEYWORD_LIMIT)
                return self._done(op, PASSED, started, f"{data['idea_count']} ideas (estimates, not recorded)",
                                  checks={"query_ok": True, "provenance_estimate": data["provenance"]["data_kind"] == "estimate"})
        except GoogleOAuthError as exc:
            return self._done(op, FAILED, started, f"oauth: {exc}")
        except Exception as exc:  # noqa: BLE001
            err = reader.classify_google_error(exc)
            return self._done(op, FAILED, started, f"{err.reason}: {err.message}")
        return self._done(op, NOT_TESTED, started, "unknown operation")

    # ---- driver --------------------------------------------------------------------------------------
    def _selected(self) -> List[O.Operation]:
        unknown = (self.only or set()) - set(O.OPERATIONS)
        if unknown:
            raise ValueError(f"operations outside the allowlist: {sorted(unknown)}")
        return [op for op in O.OPERATIONS.values() if self.only is None or op.op_id in self.only]

    async def execute(self) -> Dict[str, OpResult]:
        for op in self._selected():
            started = time.monotonic()
            missing = O.missing_env(op, self.env)
            if missing:
                self._done(op, NOT_TESTED, started, "missing configuration: " + ", ".join(missing))
                continue
            dep = O.DEPENDS_ON.get(op.op_id)
            if dep and (dep not in self.results or self.results[dep].status != PASSED):
                self._done(op, NOT_TESTED, started, f"prerequisite {dep} did not pass")
                continue
            try:
                await (self._ai(op) if op.kind.startswith("ai") else self._gads(op))
            except guard.CostCapExceeded as exc:
                self._done(op, NOT_TESTED, started, f"refused by cost cap: {exc}")
            except E.AIError as exc:
                self._done(op, FAILED, started, f"{exc.kind}: {exc}")
            except Exception as exc:  # noqa: BLE001
                self._done(op, FAILED, started, type(exc).__name__)
        return self.results

    def report(self) -> dict:
        summary = {PASSED: 0, FAILED: 0, NOT_TESTED: 0}
        for r in self.results.values():
            summary[r.status] += 1
        return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "total_cost_usd": round(self.ledger.spent_micros / 1_000_000, 6),
                "cost_cap_usd": self.ledger.max_total_micros / 1_000_000,
                "summary": summary, "operations": [r.as_dict() for r in self.results.values()]}

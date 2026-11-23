"""OpenAI adapter: Chat Completions (POST /chat/completions, GET /models).

Chat Completions is used (rather than the newer Responses API) because it is the widest,
most stable surface and maps one-to-one onto the neutral contract; the docs recommend
Responses for new OpenAI-only projects, so this is a documented, replaceable choice.
Wire facts: ``Authorization: Bearer``; ``max_completion_tokens``; ``tool_calls[].function.arguments``
is a JSON STRING; tool results are ``role: tool`` messages; images are ``image_url`` data URLs.
"""
import base64
import json
from typing import Any, Dict, List

from app.ai import errors as E
from app.ai import types as T
from app.ai.adapters.base import ProviderAdapter
from app.core.config import settings

_FINISH = {"stop": T.STOP, "length": T.LENGTH, "tool_calls": T.TOOL_CALLS, "content_filter": T.CONTENT_FILTER,
           "function_call": T.TOOL_CALLS}
_QUOTA_CODES = {"insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}


class OpenAIAdapter(ProviderAdapter):
    name = "openai"

    @property
    def _base(self) -> str:
        return settings.AI_OPENAI_BASE_URL

    def _headers(self, api_key: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _error_kind(self, status: int, body: Any) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        code = str((err or {}).get("code") or "") if isinstance(err, dict) else ""
        err_type = str((err or {}).get("type") or "") if isinstance(err, dict) else ""
        if status == 429 and (code in _QUOTA_CODES or err_type in _QUOTA_CODES):
            return E.QUOTA
        if status == 429 and code == "slow_down":
            return E.RATE_LIMIT
        return self._generic_kind(status) or E.UNKNOWN

    # ---- request ----------------------------------------------------------------
    def _messages(self, request: T.ChatRequest) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if request.system:
            out.append({"role": "system", "content": request.system})
        for message in request.messages:
            texts = [p for p in message.parts if isinstance(p, (T.TextPart, T.ImagePart))]
            results = [p for p in message.parts if isinstance(p, T.ToolResult)]
            calls = [p for p in message.parts if isinstance(p, T.ToolCall)]
            for result in results:
                out.append({"role": "tool", "tool_call_id": result.call_id, "content": result.content})
            if message.role == "assistant":
                entry: Dict[str, Any] = {"role": "assistant",
                                         "content": "".join(p.text for p in texts if isinstance(p, T.TextPart)) or None}
                if calls:
                    entry["tool_calls"] = [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in calls]
                out.append(entry)
            elif texts:
                if all(isinstance(p, T.TextPart) for p in texts):
                    out.append({"role": "user", "content": "".join(p.text for p in texts)})
                else:
                    content: List[Dict[str, Any]] = []
                    for p in texts:
                        if isinstance(p, T.TextPart):
                            content.append({"type": "text", "text": p.text})
                        else:
                            url = f"data:{p.mime_type};base64,{base64.b64encode(p.data).decode()}"
                            content.append({"type": "image_url", "image_url": {"url": url}})
                    out.append({"role": "user", "content": content})
        return out

    def build_body(self, request: T.ChatRequest, model_id: str) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": model_id, "messages": self._messages(request),
                                "max_completion_tokens": request.max_tokens}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.stop:
            body["stop"] = request.stop
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        if request.tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.parameters}} for t in request.tools]
            choice = request.tool_choice
            if isinstance(choice, dict):
                body["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
            elif choice in ("none", "required"):
                body["tool_choice"] = choice
            else:
                body["tool_choice"] = "auto"
        return body

    # ---- response ---------------------------------------------------------------
    def parse(self, body: Dict[str, Any], model_id: str) -> T.ChatResponse:
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise E.AIError(E.INVALID_RESPONSE, "no choices", provider=self.name)
        choice = choices[0]
        message = choice.get("message") or {}
        calls: List[T.ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = (raw or {}).get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                raise E.AIError(E.INVALID_RESPONSE, "tool call arguments are not JSON", provider=self.name)
            if not isinstance(args, dict):
                raise E.AIError(E.INVALID_RESPONSE, "tool call arguments are not an object", provider=self.name)
            calls.append(T.ToolCall(id=str(raw.get("id")), name=str(fn.get("name")), arguments=args))
        usage = body.get("usage") or {}
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        reasoning = int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        finish = _FINISH.get(choice.get("finish_reason"), T.OTHER)
        if calls:
            finish = T.TOOL_CALLS
        if message.get("refusal") and not message.get("content"):
            finish = T.CONTENT_FILTER
        return T.ChatResponse(
            text=message.get("content") or "", tool_calls=calls, finish_reason=finish,
            usage=T.Usage(input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0),
                          cached_input_tokens=cached, reasoning_tokens=reasoning),
            model=str(body.get("model") or model_id), provider=self.name, response_id=body.get("id"),
        )

    # ---- contract ---------------------------------------------------------------
    async def chat(self, request, model_id, api_key, timeout):
        body = await self._request("POST", f"{self._base}/chat/completions", api_key, timeout, self.build_body(request, model_id))
        return self.parse(body, model_id)

    async def list_models(self, api_key, timeout):
        body = await self._request("GET", f"{self._base}/models", api_key, timeout)
        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise E.AIError(E.INVALID_RESPONSE, "unexpected model list", provider=self.name)
        return [{"model_id": m["id"], "display_name": None} for m in items if isinstance(m, dict) and m.get("id")]

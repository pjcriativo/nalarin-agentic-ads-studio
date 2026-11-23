"""Anthropic adapter: Messages API (POST /v1/messages, GET /v1/models).

Wire facts (official docs, see docs/etapa-02-01-ai-gateway.md): headers ``x-api-key`` and
``anthropic-version``; ``system`` is a top-level field; tool results travel as ``tool_result``
blocks in a ``user`` message; images are base64 ``image`` blocks; errors are
``{"type":"error","error":{"type","message"}}``.
"""
import base64
from typing import Any, Dict, List

from app.ai import errors as E
from app.ai import types as T
from app.ai.adapters.base import ProviderAdapter
from app.core.config import settings

_FINISH = {"end_turn": T.STOP, "stop_sequence": T.STOP, "max_tokens": T.LENGTH, "tool_use": T.TOOL_CALLS,
           "refusal": T.CONTENT_FILTER, "pause_turn": T.OTHER}


class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"

    @property
    def _base(self) -> str:
        return settings.AI_ANTHROPIC_BASE_URL

    def _headers(self, api_key: str) -> Dict[str, str]:
        return {"x-api-key": api_key, "anthropic-version": settings.AI_ANTHROPIC_VERSION,
                "content-type": "application/json"}

    def _error_kind(self, status: int, body: Any) -> str:
        err_type = ((body or {}).get("error") or {}).get("type") if isinstance(body, dict) else None
        if status == 402 or err_type == "billing_error":
            return E.QUOTA
        if status in (500, 529, 409) or err_type in ("api_error", "overloaded_error"):
            return E.UNAVAILABLE
        if status == 504 or err_type == "timeout_error":
            return E.TIMEOUT
        return self._generic_kind(status) or E.UNKNOWN

    # ---- request ----------------------------------------------------------------
    def _blocks(self, parts: List[T.Part]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for part in parts:
            if isinstance(part, T.TextPart):
                blocks.append({"type": "text", "text": part.text})
            elif isinstance(part, T.ImagePart):
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": part.mime_type,
                                                           "data": base64.b64encode(part.data).decode()}})
            elif isinstance(part, T.ToolCall):
                blocks.append({"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments})
            elif isinstance(part, T.ToolResult):
                block = {"type": "tool_result", "tool_use_id": part.call_id, "content": part.content}
                if part.is_error:
                    block["is_error"] = True
                blocks.append(block)
        return blocks

    def build_body(self, request: T.ChatRequest, model_id: str) -> Dict[str, Any]:
        system = request.system or ""
        if request.json_mode:
            # no native JSON mode: say it, and the gateway validates the answer anyway
            system = (system + "\n\n" if system else "") + "Respond with a single valid JSON value and nothing else."
        body: Dict[str, Any] = {
            "model": model_id,
            "max_tokens": request.max_tokens,
            "messages": [{"role": m.role, "content": self._blocks(m.parts)} for m in request.messages],
        }
        if system:
            body["system"] = system
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.stop:
            body["stop_sequences"] = request.stop
        if request.tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                             for t in request.tools]
            choice = request.tool_choice
            if choice == "required":
                body["tool_choice"] = {"type": "any"}
            elif choice == "none":
                body["tool_choice"] = {"type": "none"}
            elif isinstance(choice, dict):
                body["tool_choice"] = {"type": "tool", "name": choice["name"]}
            else:
                body["tool_choice"] = {"type": "auto"}
        return body

    # ---- response ---------------------------------------------------------------
    def parse(self, body: Dict[str, Any], model_id: str) -> T.ChatResponse:
        if not isinstance(body, dict) or not isinstance(body.get("content"), list):
            raise E.AIError(E.INVALID_RESPONSE, "missing content", provider=self.name)
        text_parts: List[str] = []
        calls: List[T.ToolCall] = []
        for block in body["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                calls.append(T.ToolCall(id=str(block.get("id")), name=str(block.get("name")),
                                        arguments=block.get("input") if isinstance(block.get("input"), dict) else {}))
        usage = body.get("usage") or {}
        cached = int(usage.get("cache_read_input_tokens") or 0)
        prompt = int(usage.get("input_tokens") or 0) + cached + int(usage.get("cache_creation_input_tokens") or 0)
        reasoning = int(((usage.get("output_tokens_details") or {}).get("thinking_tokens")) or 0)
        finish = _FINISH.get(body.get("stop_reason"), T.OTHER)
        if calls:
            finish = T.TOOL_CALLS
        return T.ChatResponse(
            text="".join(text_parts), tool_calls=calls, finish_reason=finish,
            usage=T.Usage(input_tokens=prompt, output_tokens=int(usage.get("output_tokens") or 0),
                          cached_input_tokens=cached, reasoning_tokens=reasoning),
            model=str(body.get("model") or model_id), provider=self.name, response_id=body.get("id"),
        )

    # ---- contract ---------------------------------------------------------------
    async def chat(self, request, model_id, api_key, timeout):
        body = await self._request("POST", f"{self._base}/v1/messages", api_key, timeout, self.build_body(request, model_id))
        return self.parse(body, model_id)

    async def list_models(self, api_key, timeout):
        body = await self._request("GET", f"{self._base}/v1/models?limit=100", api_key, timeout)
        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise E.AIError(E.INVALID_RESPONSE, "unexpected model list", provider=self.name)
        return [{"model_id": m["id"], "display_name": m.get("display_name")} for m in items if isinstance(m, dict) and m.get("id")]

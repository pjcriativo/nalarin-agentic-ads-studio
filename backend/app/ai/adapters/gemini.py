"""Google Gemini adapter: ``generateContent`` over REST (GET /models for discovery).

The old ``google-generativeai`` package is not used (replaced by this adapter; the official
SDK today is ``google-genai``). Wire facts: key in the ``x-goog-api-key`` HEADER (never the
``?key=`` query string, which would end up in URLs/logs); roles are ``user``/``model``;
``systemInstruction`` is top-level; function calls are ``functionCall{name,args}`` parts and
answers ``functionResponse{name,response}`` parts; usage is ``usageMetadata``.
"""
import base64
from typing import Any, Dict, List, Optional

from app.ai import errors as E
from app.ai import types as T
from app.ai.adapters.base import ProviderAdapter
from app.core.config import settings

_BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
            "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION"}
_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "$id", "$ref", "definitions", "$defs", "default", "examples"}


def _clean_schema(node: Any) -> Any:
    """Gemini accepts an OpenAPI-style subset of JSON Schema: drop what it rejects."""
    if isinstance(node, dict):
        return {k: _clean_schema(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    return node


class GeminiAdapter(ProviderAdapter):
    name = "gemini"

    @property
    def _base(self) -> str:
        return settings.AI_GEMINI_BASE_URL

    def _headers(self, api_key: str) -> Dict[str, str]:
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def _error_kind(self, status: int, body: Any) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        message = str((err or {}).get("message") or "").lower() if isinstance(err, dict) else ""
        if status in (400, 401) and ("api key not valid" in message or "api_key_invalid" in message or "invalid api key" in message):
            return E.AUTHENTICATION
        if status == 402:
            return E.QUOTA
        return self._generic_kind(status) or E.UNKNOWN

    # ---- request ----------------------------------------------------------------
    def _contents(self, request: T.ChatRequest) -> List[Dict[str, Any]]:
        names: Dict[str, str] = {}
        for message in request.messages:
            for part in message.parts:
                if isinstance(part, T.ToolCall):
                    names[part.id] = part.name
        contents: List[Dict[str, Any]] = []
        for message in request.messages:
            parts: List[Dict[str, Any]] = []
            for part in message.parts:
                if isinstance(part, T.TextPart):
                    parts.append({"text": part.text})
                elif isinstance(part, T.ImagePart):
                    parts.append({"inline_data": {"mime_type": part.mime_type, "data": base64.b64encode(part.data).decode()}})
                elif isinstance(part, T.ToolCall):
                    call_part: Dict[str, Any] = {"functionCall": {"name": part.name, "args": part.arguments}}
                    signature = (part.provider_data or {}).get("thoughtSignature")
                    if signature:  # thinking models require it echoed back (Gemini 3 rejects the turn otherwise)
                        call_part["thoughtSignature"] = signature
                    parts.append(call_part)
                elif isinstance(part, T.ToolResult):
                    key = "error" if part.is_error else "result"
                    parts.append({"functionResponse": {"name": names.get(part.call_id, part.call_id),
                                                       "response": {key: part.content}}})
            if parts:
                contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        return contents

    def build_body(self, request: T.ChatRequest, model_id: str) -> Dict[str, Any]:
        config: Dict[str, Any] = {"maxOutputTokens": request.max_tokens}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.stop:
            config["stopSequences"] = request.stop
        if request.json_mode:
            config["responseMimeType"] = "application/json"
        body: Dict[str, Any] = {"contents": self._contents(request), "generationConfig": config}
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        if request.tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": _clean_schema(t.parameters)}
                for t in request.tools]}]
            choice = request.tool_choice
            if isinstance(choice, dict):
                cfg = {"mode": "ANY", "allowedFunctionNames": [choice["name"]]}
            elif choice == "required":
                cfg = {"mode": "ANY"}
            elif choice == "none":
                cfg = {"mode": "NONE"}
            else:
                cfg = {"mode": "AUTO"}
            body["toolConfig"] = {"functionCallingConfig": cfg}
        return body

    @staticmethod
    def _model_path(model_id: str) -> str:
        return model_id if model_id.startswith("models/") else f"models/{model_id}"

    # ---- response ---------------------------------------------------------------
    def parse(self, body: Dict[str, Any], model_id: str) -> T.ChatResponse:
        if not isinstance(body, dict):
            raise E.AIError(E.INVALID_RESPONSE, "not an object", provider=self.name)
        candidates = body.get("candidates")
        if not candidates:
            reason = (body.get("promptFeedback") or {}).get("blockReason")
            if reason:
                raise E.AIError(E.CONTENT_BLOCKED, f"prompt blocked: {reason}", provider=self.name)
            raise E.AIError(E.INVALID_RESPONSE, "no candidates", provider=self.name)
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        parts = (candidate.get("content") or {}).get("parts") or []
        texts: List[str] = []
        calls: List[T.ToolCall] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if "functionCall" in part:
                fc = part["functionCall"] or {}
                signature = part.get("thoughtSignature") or part.get("thought_signature")
                calls.append(T.ToolCall(id=str(fc.get("id") or f"call_{index}"), name=str(fc.get("name")),
                                        arguments=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                                        provider_data={"thoughtSignature": signature} if signature else None))
            elif "text" in part and not part.get("thought"):
                texts.append(part["text"])
        reason = candidate.get("finishReason")
        if reason in _BLOCKED and not texts and not calls:
            raise E.AIError(E.CONTENT_BLOCKED, f"blocked: {reason}", provider=self.name)
        usage = body.get("usageMetadata") or {}
        thoughts = int(usage.get("thoughtsTokenCount") or 0)
        finish = {"STOP": T.STOP, "MAX_TOKENS": T.LENGTH}.get(reason, T.CONTENT_FILTER if reason in _BLOCKED else T.OTHER)
        if calls:
            finish = T.TOOL_CALLS
        return T.ChatResponse(
            text="".join(texts), tool_calls=calls, finish_reason=finish,
            usage=T.Usage(input_tokens=int(usage.get("promptTokenCount") or 0),
                          output_tokens=int(usage.get("candidatesTokenCount") or 0) + thoughts,
                          cached_input_tokens=int(usage.get("cachedContentTokenCount") or 0), reasoning_tokens=thoughts),
            model=str(body.get("modelVersion") or model_id), provider=self.name, response_id=body.get("responseId"),
        )

    # ---- contract ---------------------------------------------------------------
    async def chat(self, request, model_id, api_key, timeout):
        url = f"{self._base}/{self._model_path(model_id)}:generateContent"
        body = await self._request("POST", url, api_key, timeout, self.build_body(request, model_id))
        return self.parse(body, model_id)

    async def list_models(self, api_key, timeout):
        body = await self._request("GET", f"{self._base}/models?pageSize=100", api_key, timeout)
        items = body.get("models") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise E.AIError(E.INVALID_RESPONSE, "unexpected model list", provider=self.name)
        out: List[Dict[str, Any]] = []
        for m in items:
            if not isinstance(m, dict) or not m.get("name"):
                continue
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            out.append({"model_id": str(m["name"]).removeprefix("models/"), "display_name": m.get("displayName")})
        return out

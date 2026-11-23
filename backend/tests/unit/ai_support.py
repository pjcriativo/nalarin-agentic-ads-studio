"""Shared test doubles for the AI Gateway tests.

NO test may reach a paid provider: ``conftest.py`` installs a transport that fails the test on
any network attempt, and ``ProviderMock`` replaces it with scripted ``httpx`` answers for the three
providers' real endpoints. Model names below are arbitrary TEST DATA (the gateway has none built in).
"""
import json
from typing import Callable, Dict, List, Optional, Union

import httpx

HOSTS = {"api.anthropic.com": "anthropic", "api.openai.com": "openai", "generativelanguage.googleapis.com": "gemini"}
KEYS = {"anthropic": "sk-ant-test-KEYKEYKEY1234", "openai": "sk-test-OPENAIKEY5678", "gemini": "AIzaTestGeminiKey0123456789abcd"}


def jr(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---- success bodies, one per provider wire format ----------------------------------------
def anthropic_ok(text="hello", inp=10, out=5, tool_calls=(), stop="end_turn", extra_usage=None):
    content = [{"type": "text", "text": text}] if text is not None else []
    for call in tool_calls:
        content.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["args"]})
    usage = {"input_tokens": inp, "output_tokens": out, **(extra_usage or {})}
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": content, "model": "m-anth",
            "stop_reason": "tool_use" if tool_calls else stop, "usage": usage}


def openai_ok(text="hello", inp=10, out=5, tool_calls=(), finish="stop", usage_extra=None):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": json.dumps(c["args"])}} for c in tool_calls]
    usage = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out, **(usage_extra or {})}
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": "m-oai",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else finish}], "usage": usage}


def gemini_ok(text="hello", inp=10, out=5, tool_calls=(), finish="STOP", usage_extra=None):
    parts = [{"text": text}] if text is not None else []
    for c in tool_calls:
        parts.append({"functionCall": {"name": c["name"], "args": c["args"], **({"id": c["id"]} if c.get("id") else {})}})
    usage = {"promptTokenCount": inp, "candidatesTokenCount": out, "totalTokenCount": inp + out, **(usage_extra or {})}
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "usageMetadata": usage, "modelVersion": "m-gem", "responseId": "resp-1"}


OK = {"anthropic": anthropic_ok, "openai": openai_ok, "gemini": gemini_ok}


class ProviderMock:
    """Scripted answers per provider; records every request (for header/body/URL assertions)."""

    def __init__(self):
        self.requests: List[httpx.Request] = []
        self.queues: Dict[str, List[Union[httpx.Response, Exception, Callable]]] = {}
        self.default: Dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def queue(self, provider: str, *items):
        self.queues.setdefault(provider, []).extend(items)

    def always(self, provider: str, handler):
        self.default[provider] = handler

    def of(self, provider: str) -> List[httpx.Request]:
        return [r for r in self.requests if HOSTS.get(r.url.host) == provider]

    def body(self, request: httpx.Request):
        return json.loads(request.content.decode()) if request.content else None

    def transport(self) -> httpx.MockTransport:
        def dispatch(request: httpx.Request) -> httpx.Response:
            provider = HOSTS.get(request.url.host)
            assert provider, f"unexpected host in a test: {request.url.host}"
            self.requests.append(request)
            queue = self.queues.get(provider) or []
            item = queue.pop(0) if queue else self.default.get(provider)
            assert item is not None, f"no scripted answer left for {provider}"
            if isinstance(item, Exception):
                raise item
            return item(request) if callable(item) else item
        return httpx.MockTransport(dispatch)


def deny_network(request: httpx.Request):
    raise AssertionError(f"a test attempted a REAL network call to {request.url.host}")


# ---- database seeding (superuser connection; bypasses RLS) ----------------------------------
def seed_credential(db, org_id, provider, key=None):
    from app.core.token_encryption import encrypt_token
    from app.models import AiCredential
    key = key or KEYS[provider]
    row = AiCredential(organization_id=org_id, provider=provider, encrypted_key=encrypt_token(key), last4=key[-4:], status="active")
    db.add(row)
    db.commit()
    return row


def seed_model(db, org_id, provider, model_id, **fields):
    from app.models import AiModel
    row = AiModel(organization_id=org_id, provider=provider, model_id=model_id, **fields)
    db.add(row)
    db.commit()
    return row


def gateway_for(org_id, user_id=None, **kwargs):
    """A gateway on an application session bound to ``org_id`` (runtime role, RLS in force)."""
    from app.ai.gateway import AIGateway
    from app.database import SessionLocal, bind_context
    db = SessionLocal()
    bind_context(db, org_id)
    return AIGateway(db, org_id, user_id, **kwargs), db

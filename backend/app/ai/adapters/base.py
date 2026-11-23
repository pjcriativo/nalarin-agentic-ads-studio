"""Adapter base: HTTP plumbing, error mapping and the contract every provider implements.

Adapters talk to the provider's REST API with ``httpx`` (already a dependency): one small,
auditable surface per provider, full control of timeouts / redirects / error bodies, and a
single place (``_transport``) where tests substitute a mock transport so NO test can ever
reach a paid endpoint.

Security rules enforced here, for every provider:
* credentials travel only in HEADERS (never in URLs, which end up in logs and exceptions);
* redirects are never followed (a provider URL is fixed configuration, not user input);
* an exception message/provider body is redacted before it becomes an ``AIError``.
"""
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from app.ai import errors as E
from app.ai.types import ChatRequest, ChatResponse

# Tests (and only tests) replace this with an ``httpx.MockTransport``.
_transport: Optional[httpx.AsyncBaseTransport] = None


def set_transport(transport: Optional[httpx.AsyncBaseTransport]) -> None:
    global _transport
    _transport = transport


def make_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=_transport)


class ProviderAdapter(ABC):
    name: str = ""

    # ---- contract -----------------------------------------------------------
    @abstractmethod
    async def chat(self, request: ChatRequest, model_id: str, api_key: str, timeout: float) -> ChatResponse: ...

    @abstractmethod
    async def list_models(self, api_key: str, timeout: float) -> List[Dict[str, Any]]:
        """Normalized: [{"model_id": str, "display_name": str|None}] of chat-capable models."""

    async def verify(self, api_key: str, timeout: float = 20.0) -> None:
        """Connection test: a free, read-only call that proves the key is accepted.
        Raises AIError (authentication/permission/unavailable...) otherwise."""
        await self.list_models(api_key, timeout)

    # ---- provider specifics ---------------------------------------------------
    @abstractmethod
    def _headers(self, api_key: str) -> Dict[str, str]: ...

    @abstractmethod
    def _error_kind(self, status: int, body: Any) -> str: ...

    # ---- shared plumbing ------------------------------------------------------
    async def _request(self, method: str, url: str, api_key: str, timeout: float,
                       payload: Optional[dict] = None) -> Any:
        try:
            async with make_client(timeout) as client:
                response = await client.request(method, url, headers=self._headers(api_key),
                                                json=payload if payload is not None else None)
        except httpx.TimeoutException as exc:
            raise E.AIError(E.TIMEOUT, "request timed out", provider=self.name, secrets=[api_key]) from None
        except httpx.HTTPError as exc:
            # connection/DNS/TLS problems: the provider is unreachable. Never echo the exception
            # text (it can contain the URL or proxy details).
            raise E.AIError(E.UNAVAILABLE, "provider unreachable", provider=self.name, secrets=[api_key]) from None

        body: Any
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = None

        if response.status_code >= 400:
            kind = self._error_kind(response.status_code, body)
            raise E.AIError(kind, self._error_message(body, response.status_code), provider=self.name,
                            status=response.status_code, secrets=[api_key])
        if body is None:
            raise E.AIError(E.INVALID_RESPONSE, "response was not JSON", provider=self.name,
                            status=response.status_code, secrets=[api_key])
        return body

    def _error_message(self, body: Any, status: int) -> str:
        """Best-effort provider message (it will be redacted by AIError)."""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("status") or f"HTTP {status}")
            if isinstance(err, str):
                return err
        return f"HTTP {status}"

    @staticmethod
    def _generic_kind(status: int) -> Optional[str]:
        if status == 401:
            return E.AUTHENTICATION
        if status == 403:
            return E.PERMISSION
        if status == 404:
            return E.NOT_FOUND
        if status in (400, 413, 422):
            return E.INVALID_REQUEST
        if status == 408 or status == 504:
            return E.TIMEOUT
        if status == 429:
            return E.RATE_LIMIT
        if status >= 500:
            return E.UNAVAILABLE
        return None

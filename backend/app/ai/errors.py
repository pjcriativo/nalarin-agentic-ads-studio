"""Unified AI errors + secret redaction.

Every adapter maps its provider's failures onto ``AIError.kind``. Messages are redacted
BEFORE the exception exists (provider bodies, URLs and the API key itself never survive),
so a log line, a usage row or an HTTP answer built from ``str(error)`` cannot leak a secret.
"""
import re
from typing import Iterable, Optional

AUTHENTICATION = "authentication"      # credential rejected / invalid
PERMISSION = "permission"              # credential valid but not allowed
QUOTA = "quota"                        # billing / credit / spend cap exhausted (does not clear by retrying)
RATE_LIMIT = "rate_limit"              # transient throttling
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"            # provider overloaded / 5xx / network
INVALID_REQUEST = "invalid_request"    # our request is malformed or unsupported
NOT_FOUND = "not_found"                # unknown model / endpoint
CONTENT_BLOCKED = "content_blocked"    # safety filter / refusal
INVALID_RESPONSE = "invalid_response"  # provider answered but not in a usable shape
BUDGET_EXCEEDED = "budget_exceeded"    # OUR per-organization spending limit
NOT_CONFIGURED = "not_configured"      # no model / credential configured for the operation
UNKNOWN = "unknown"

# Automatic switch to the next model happens ONLY for errors that are transient and tied to
# the provider's health: another model/provider may well succeed. Everything else is either
# our misconfiguration (auth, permission, quota, not_found...), our request (invalid_request,
# content_blocked) or our own limit (budget): switching would hide the problem or repeat it.
FALLBACK_KINDS = frozenset({RATE_LIMIT, TIMEOUT, UNAVAILABLE})

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{8,}"),
    re.compile(r"(?i)(x-api-key|x-goog-api-key|api[_-]?key|authorization)\s*[:=]\s*\S+"),
    re.compile(r"[?&]key=[^&\s]+"),
]
MAX_MESSAGE = 300


def redact(text: object, secrets: Iterable[Optional[str]] = ()) -> str:
    """Remove the given secrets and anything that looks like a credential; truncate."""
    value = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            value = value.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    value = " ".join(value.split())
    return value[:MAX_MESSAGE]


class AIError(Exception):
    def __init__(self, kind: str, message: str = "", *, provider: Optional[str] = None,
                 status: Optional[int] = None, secrets: Iterable[Optional[str]] = ()):
        self.kind = kind
        self.provider = provider
        self.status = status
        self.message = redact(message, secrets)
        super().__init__(self.message or kind)

    @property
    def fallback_eligible(self) -> bool:
        return self.kind in FALLBACK_KINDS

    def __repr__(self) -> str:  # never include anything but redacted fields
        return f"AIError(kind={self.kind!r}, provider={self.provider!r}, status={self.status!r})"


# What an HTTP client of OUR API is told (generic: no provider body, no secret).
PUBLIC_MESSAGES = {
    AUTHENTICATION: "The AI provider rejected this organization's credential. Check or rotate the API key.",
    PERMISSION: "The AI provider denied access for this organization's credential.",
    QUOTA: "The AI provider reports that this organization's quota or credit is exhausted.",
    RATE_LIMIT: "The AI provider is rate limiting this organization. Try again shortly.",
    TIMEOUT: "The AI provider did not answer in time.",
    UNAVAILABLE: "The AI provider is temporarily unavailable.",
    INVALID_REQUEST: "The AI provider could not process the request.",
    NOT_FOUND: "The configured AI model is not available at the provider.",
    CONTENT_BLOCKED: "The AI provider blocked the request or the answer.",
    INVALID_RESPONSE: "The AI provider returned an unusable answer.",
    BUDGET_EXCEEDED: "The organization's AI spending limit has been reached.",
    NOT_CONFIGURED: "AI is not configured for this organization (credential and model required).",
    UNKNOWN: "The AI request failed.",
}

HTTP_STATUS = {
    AUTHENTICATION: 502, PERMISSION: 502, QUOTA: 502, RATE_LIMIT: 429, TIMEOUT: 504, UNAVAILABLE: 503,
    INVALID_REQUEST: 502, NOT_FOUND: 502, CONTENT_BLOCKED: 422, INVALID_RESPONSE: 502,
    BUDGET_EXCEEDED: 429, NOT_CONFIGURED: 409, UNKNOWN: 502,
}

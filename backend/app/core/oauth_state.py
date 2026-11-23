"""OAuth ``state`` for every ad-platform connect flow (Google, Meta, TikTok).

The provider redirects the browser to a PUBLIC callback (no Authorization
header), so the callback must recover *who* started the flow and *for which
organization* from the state alone -- without trusting anything an attacker can
forge or replay.

Properties
----------
* **Server-side and single use.** ``/oauth/start`` stores a row in
  ``oauth_states`` (user, organization, provider, hash of a browser-binding
  cookie, expiry). The callback consumes it with one atomic
  ``UPDATE ... WHERE used_at IS NULL``: a repeated callback (replay) matches
  nothing. The row is consumed *before* the code is exchanged, so even a failed
  exchange cannot be retried with the same state.
* **Bound to user, organization, provider and browser.** The state is a signed
  JWT carrying the row id; the row is authoritative. The httpOnly cookie holds a
  random value whose SHA-256 must match the stored hash, tying the callback to
  the browser that started the flow (login-CSRF / state fixation).
* **Short-lived** (10 minutes) and signed with SECRET_KEY.
* The callback re-checks, at consumption time, that the user is STILL an active
  member of the organization with ``connections:manage``: access revoked between
  start and callback is honoured.
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Response
from jwt import PyJWTError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

_ALGORITHM = "HS256"
_STATE_TTL_MINUTES = 10
_COOKIE_NAME = "oauth_state"


@dataclass(frozen=True)
class OAuthContext:
    user_id: str
    organization_id: str
    provider: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_oauth_state(db: Session, user_id: str, organization_id: str, provider: str):
    """Persist a new single-use state. Returns ``(state_token, cookie_value)``;
    the caller sends the token to the provider and sets the cookie."""
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    cookie_value = secrets.token_urlsafe(32)
    expires = now + timedelta(minutes=_STATE_TTL_MINUTES)
    db.execute(
        text(
            "INSERT INTO oauth_states (id, user_id, organization_id, provider, cookie_hash, expires_at) "
            "VALUES (:id, :u, :o, :p, :h, :e)"
        ),
        {"id": jti, "u": user_id, "o": organization_id, "p": provider, "h": _hash(cookie_value), "e": expires},
    )
    # opportunistic cleanup of long-dead rows keeps the control table small
    db.execute(text("DELETE FROM oauth_states WHERE expires_at < now() - interval '1 day'"))
    db.commit()
    token = jwt.encode(
        {"sub": user_id, "org": organization_id, "provider": provider, "jti": jti,
         "purpose": "oauth_state", "iat": now, "exp": expires},
        settings.SECRET_KEY, algorithm=_ALGORITHM,
    )
    return token, cookie_value


def consume_oauth_state(db: Session, token: str, provider: str, cookie_value) -> OAuthContext:
    """Validate and CONSUME a state. Raises ValueError (one generic message for
    every failure mode, so the callback is not an oracle)."""
    invalid = ValueError("Invalid, expired or already used OAuth state")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
    except PyJWTError:
        raise invalid
    if payload.get("purpose") != "oauth_state" or payload.get("provider") != provider:
        raise invalid
    jti, user_id, org_id = payload.get("jti"), payload.get("sub"), payload.get("org")
    if not (jti and user_id and org_id):
        raise invalid
    if not cookie_value:
        if settings.OAUTH_REQUIRE_STATE_COOKIE:
            raise invalid
        cookie_hash = None
    else:
        cookie_hash = _hash(cookie_value)

    row = db.execute(
        text(
            "UPDATE oauth_states SET used_at = now() "
            "WHERE id = :id AND used_at IS NULL AND expires_at > now() "
            "AND provider = :p AND user_id = :u AND organization_id = :o "
            "AND (CAST(:h AS text) IS NULL OR cookie_hash = :h) "
            "RETURNING id"
        ),
        {"id": jti, "p": provider, "u": user_id, "o": org_id, "h": cookie_hash},
    ).first()
    db.commit()  # consumed for good, whatever happens next
    if row is None:
        raise invalid
    return OAuthContext(user_id=user_id, organization_id=org_id, provider=provider)


def set_oauth_state_cookie(response: Response, value: str, secure: bool = True) -> None:
    """`secure=False` must be passed for local HTTP dev: browsers silently refuse
    to store a Secure cookie on a non-HTTPS origin."""
    response.set_cookie(
        key=_COOKIE_NAME,
        value=value,
        max_age=_STATE_TTL_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(key=_COOKIE_NAME, path="/")


def get_oauth_state_cookie_name() -> str:
    return _COOKIE_NAME

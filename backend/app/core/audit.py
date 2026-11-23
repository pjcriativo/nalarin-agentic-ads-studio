"""Security audit trail: member changes and denied access attempts.

Rules
-----
* Never record secrets: no tokens, passwords, API keys, headers or request
  bodies. ``detail`` goes through :func:`sanitize_detail` (key deny-list, size
  caps) before it is stored.
* Denied attempts are written with an INDEPENDENT short session/transaction so
  they survive the rollback that follows the HTTP error; a failure to audit
  never changes the answer sent to the client.
* The table is append-only for the application role (see the migration).
"""
import logging
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AuditLog

logger = logging.getLogger(__name__)

_SECRET_KEY_PARTS = ("token", "secret", "password", "passwd", "authorization", "cookie", "key", "credential")
_MAX_STR = 200
_MAX_KEYS = 12


def sanitize_detail(detail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not detail:
        return None
    clean: Dict[str, Any] = {}
    for name, value in list(detail.items())[:_MAX_KEYS]:
        if any(part in str(name).lower() for part in _SECRET_KEY_PARTS):
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            clean[str(name)] = value
        else:
            clean[str(name)] = str(value)[:_MAX_STR]
    return clean or None


def client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None
    return request.client.host


def _row(action, outcome, organization_id, actor_user_id, target, detail, request) -> AuditLog:
    return AuditLog(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action[:80],
        outcome=outcome,
        target=(str(target)[:200] if target is not None else None),
        detail=sanitize_detail(detail),
        ip_address=client_ip(request),
    )


def audit_event(db: Session, *, action: str, organization_id: str, actor_user_id: Optional[str],
                target: Optional[str] = None, detail: Optional[Dict[str, Any]] = None,
                request: Optional[Request] = None) -> None:
    """Record a SUCCESSFUL event inside the caller's transaction (commits with
    the change it describes; the session must be bound to ``organization_id``)."""
    db.add(_row(action, "success", organization_id, actor_user_id, target, detail, request))


def audit_denied(*, action: str, actor_user_id: Optional[str], organization_id: Optional[str] = None,
                 target: Optional[str] = None, detail: Optional[Dict[str, Any]] = None,
                 request: Optional[Request] = None) -> None:
    """Record a DENIED attempt in its own transaction. Never raises."""
    session = SessionLocal()
    try:
        session.add(_row(action, "denied", organization_id, actor_user_id, target, detail, request))
        session.commit()
    except Exception:  # pragma: no cover - auditing must not break the request
        session.rollback()
        logger.exception("could not record denied-access audit event")
    finally:
        session.close()

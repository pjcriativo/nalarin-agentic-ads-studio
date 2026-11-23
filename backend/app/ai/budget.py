"""Per-organization spending limits with ATOMIC reservation.

Why not "SELECT usage, compare, INSERT": two concurrent requests both read the same usage,
both pass, both spend. Here the check and the reservation are ONE ``UPDATE ... WHERE
spent + reserved + :estimate <= limit``: PostgreSQL row-locks the budget row, so concurrent
callers are serialized and the sum of reservations can never exceed the limit. The reservation
is committed immediately (visible to everybody) and later settled to the real cost.

Crash safety: every reservation also has a row in ``ai_budget_reservations`` (same transaction).
Settling/releasing deletes it. A row older than ``AI_RESERVATION_TTL_SECONDS`` belongs to a
process that died before settling; ``recover_abandoned`` converts it to SPENT at its estimate.
It is never simply released: the provider may have answered (and billed) before the crash, so
releasing could let the organization spend above its limit.

Windows (UTC): ``daily`` = the calendar day, ``monthly`` = the calendar month. A budget row is
lazily rolled into the current window by the same UPDATE (counters reset when the window moves).
Limits of every enabled period must admit the reservation, or none is made.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai import errors as E
from app.core.config import settings


def window_start(period: str, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


_RESERVE = text(
    "UPDATE ai_budgets SET "
    " window_start = :ws, "
    " spent_micros = CASE WHEN window_start = :ws THEN spent_micros ELSE 0 END, "
    " reserved_micros = (CASE WHEN window_start = :ws THEN reserved_micros ELSE 0 END) + :est "
    "WHERE organization_id = :org AND period = :period AND enabled "
    " AND (CASE WHEN window_start = :ws THEN spent_micros + reserved_micros ELSE 0 END) + :est <= limit_micros "
    "RETURNING id"
)

_INSERT_RESERVATION = text(
    "INSERT INTO ai_budget_reservations (id, organization_id, period, window_start, amount_micros, request_id) "
    "VALUES (:id, :org, :period, :ws, :amount, :request_id)"
)

# DELETE ... RETURNING row-locks the reservation: whoever deletes it first (the request settling it,
# or a recovery) applies its effect on the counters; the other finds nothing and does nothing.
_TAKE = text("DELETE FROM ai_budget_reservations WHERE id = :id AND organization_id = :org RETURNING amount_micros")

_TAKE_ABANDONED = text(
    "DELETE FROM ai_budget_reservations WHERE organization_id = :org AND created_at < :cutoff "
    "RETURNING id, period, window_start, amount_micros"
)

# Only the window the reservation was made in is touched: a window that rolled already reset its counters.
_SETTLE = text(
    "UPDATE ai_budgets SET "
    " reserved_micros = GREATEST(reserved_micros - :amount, 0), "
    " spent_micros = spent_micros + :actual "
    "WHERE organization_id = :org AND period = :period AND window_start = :ws"
)


@dataclass(frozen=True)
class Reservation:
    id: str
    period: str
    window_start: datetime
    amount_micros: int


def enabled_periods(db: Session, org_id: str) -> List[str]:
    rows = db.execute(text("SELECT period FROM ai_budgets WHERE organization_id = :org AND enabled"), {"org": org_id}).all()
    return [r[0] for r in rows]


def reserve(db: Session, org_id: str, estimate_micros: int, now: Optional[datetime] = None,
            request_id: Optional[str] = None) -> List[Reservation]:
    """Reserve ``estimate_micros`` against every enabled budget (all or nothing).
    Returns the reservations (empty list = no budget configured = nothing enforced).
    Raises AIError(BUDGET_EXCEEDED) without reserving anything."""
    recover_abandoned(db, org_id, now=now)  # charges what dead processes left behind, BEFORE judging the limit
    reserved: List[Reservation] = []
    for period in enabled_periods(db, org_id):
        ws = window_start(period, now)
        row = db.execute(_RESERVE, {"org": org_id, "period": period, "ws": ws, "est": int(estimate_micros)}).first()
        if row is None:
            db.rollback()  # undoes the periods reserved so far in this transaction
            raise E.AIError(E.BUDGET_EXCEEDED, f"{period} limit reached")
        reservation = Reservation(str(uuid.uuid4()), period, ws, int(estimate_micros))
        db.execute(_INSERT_RESERVATION, {"id": reservation.id, "org": org_id, "period": period, "ws": ws,
                                         "amount": reservation.amount_micros, "request_id": request_id})
        reserved.append(reservation)
    db.commit()
    return reserved


def settle(db: Session, org_id: str, reservations: List[Reservation], actual_micros: int) -> None:
    """Replace each reservation by what was really spent (``actual=0`` releases it). A reservation
    already recovered as abandoned is left alone: it was charged at its estimate."""
    for r in reservations:
        if db.execute(_TAKE, {"id": r.id, "org": org_id}).first() is None:
            continue
        db.execute(_SETTLE, {"org": org_id, "period": r.period, "ws": r.window_start,
                             "amount": r.amount_micros, "actual": int(actual_micros)})
    db.commit()


def release(db: Session, org_id: str, reservations: List[Reservation]) -> None:
    settle(db, org_id, reservations, 0)


def recover_abandoned(db: Session, org_id: str, now: Optional[datetime] = None, ttl_seconds: Optional[int] = None) -> int:
    """Convert reservations older than the TTL into spent (conservatively, at their estimate).
    Idempotent and safe to run concurrently. Returns how many were recovered."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=ttl_seconds if ttl_seconds is not None else settings.AI_RESERVATION_TTL_SECONDS)
    rows = db.execute(_TAKE_ABANDONED, {"org": org_id, "cutoff": cutoff}).all()
    for row in rows:
        db.execute(_SETTLE, {"org": org_id, "period": row.period, "ws": row.window_start,
                             "amount": row.amount_micros, "actual": row.amount_micros})
    if rows:
        from app.core.audit import audit_event
        audit_event(db, action="ai.budget.reservation_recovered", organization_id=org_id, actor_user_id=None,
                    detail={"count": len(rows), "charged_micros": sum(r.amount_micros for r in rows)})
    db.commit()
    return len(rows)

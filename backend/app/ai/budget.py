"""Per-organization spending limits with ATOMIC reservation.

Why not "SELECT usage, compare, INSERT": two concurrent requests both read the same usage,
both pass, both spend. Here the check and the reservation are ONE ``UPDATE ... WHERE
spent + reserved + :estimate <= limit``: PostgreSQL row-locks the budget row, so concurrent
callers are serialized and the sum of reservations can never exceed the limit. The reservation
is committed immediately (visible to everybody) and later settled to the real cost.

Windows (UTC): ``daily`` = the calendar day, ``monthly`` = the calendar month. A budget row is
lazily rolled into the current window by the same UPDATE (counters reset when the window moves).
Limits of every enabled period must admit the reservation, or none is made.
"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai import errors as E


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

_SETTLE = text(
    "UPDATE ai_budgets SET "
    " reserved_micros = GREATEST(reserved_micros - :est, 0), "
    " spent_micros = spent_micros + :actual "
    "WHERE organization_id = :org AND period = :period"
)


def enabled_periods(db: Session, org_id: str) -> List[str]:
    rows = db.execute(text("SELECT period FROM ai_budgets WHERE organization_id = :org AND enabled"), {"org": org_id}).all()
    return [r[0] for r in rows]


def reserve(db: Session, org_id: str, estimate_micros: int, now: Optional[datetime] = None) -> List[str]:
    """Reserve ``estimate_micros`` against every enabled budget (all or nothing).
    Returns the reserved periods (empty list = no budget configured = nothing enforced).
    Raises AIError(BUDGET_EXCEEDED) without reserving anything."""
    periods = enabled_periods(db, org_id)
    reserved: List[str] = []
    for period in periods:
        row = db.execute(_RESERVE, {"org": org_id, "period": period, "ws": window_start(period, now),
                                    "est": int(estimate_micros)}).first()
        if row is None:
            db.rollback()  # undoes the periods reserved so far in this transaction
            raise E.AIError(E.BUDGET_EXCEEDED, f"{period} limit reached")
        reserved.append(period)
    db.commit()
    return reserved


def settle(db: Session, org_id: str, periods: List[str], estimate_micros: int, actual_micros: int) -> None:
    """Replace the reservation by what was really spent (also used with actual=0 to release)."""
    for period in periods:
        db.execute(_SETTLE, {"org": org_id, "period": period, "est": int(estimate_micros), "actual": int(actual_micros)})
    db.commit()


def release(db: Session, org_id: str, periods: List[str], estimate_micros: int) -> None:
    settle(db, org_id, periods, estimate_micros, 0)

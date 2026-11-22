"""Background scanner for agentic auto-mode -- DISABLED BY DESIGN.

An earlier version "evaluated" two hard-coded fake campaigns (``act_12345`` /
``g_555``) with invented metrics and wrote them to the audit log, which made it
look like live optimization was running. Real metric ingestion and execution
do not exist yet (see ``AUTONOMOUS_EXECUTION_IMPLEMENTED``), so this service
now does nothing except say so. It never reads or writes campaign data and
never inserts audit rows.

Implement it for real (pull live CPA/spend through the platform services, scope
by organization, then evaluate and execute through the executor) in the
multi-tenant/agents phase.
"""
import logging
from typing import Dict

from sqlalchemy.orm import Session

from app.core.executor import AUTONOMOUS_EXECUTION_IMPLEMENTED

logger = logging.getLogger(__name__)

DISABLED_REASON = "autonomous-optimization-not-implemented"


class OptimizationSchedulerService:
    """Placeholder that keeps the call site stable while the engine is absent."""

    def __init__(self, db: Session):
        self.db = db

    def run_optimization_scan(self) -> Dict[str, object]:
        if not AUTONOMOUS_EXECUTION_IMPLEMENTED:
            logger.warning(
                "Optimization scan skipped: %s. No campaigns were read or changed.",
                DISABLED_REASON,
            )
        return {"status": "disabled", "reason": DISABLED_REASON, "audits_written": 0}

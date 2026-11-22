"""The auto-mode scheduler is disabled by design: it must never fabricate
campaign decisions or audit rows, whatever the environment says."""
from app.core.executor import AUTONOMOUS_EXECUTION_IMPLEMENTED
from app.models import MutationAudit
from app.services.optimization_scheduler import (
    DISABLED_REASON,
    OptimizationSchedulerService,
)


def test_autonomous_execution_is_not_claimed():
    assert AUTONOMOUS_EXECUTION_IMPLEMENTED is False


def test_scan_is_disabled_when_auto_mode_off(db_session, monkeypatch):
    monkeypatch.setenv("ADS_AUTO_MODE", "false")
    before = db_session.query(MutationAudit).count()
    result = OptimizationSchedulerService(db_session).run_optimization_scan()
    assert result["status"] == "disabled"
    assert result["reason"] == DISABLED_REASON
    assert db_session.query(MutationAudit).count() == before


def test_scan_stays_disabled_even_if_auto_mode_env_is_on(db_session, monkeypatch):
    monkeypatch.setenv("ADS_AUTO_MODE", "true")
    monkeypatch.setenv("ADS_AUTO_ACTIONS", "campaign.pause,budget.update")
    before = db_session.query(MutationAudit).count()
    result = OptimizationSchedulerService(db_session).run_optimization_scan()
    assert result["status"] == "disabled"
    assert result["audits_written"] == 0
    assert db_session.query(MutationAudit).count() == before

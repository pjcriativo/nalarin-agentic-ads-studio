"""
Unit tests for Optimization & Auto-Mode Executor
"""

import time
import pytest
from app.core.executor import (
    AutoModePolicy,
    ProposalTokenService,
    canonical_payload_hash,
    is_kill_switch_active,
)


def test_canonical_payload_hash():
    p1 = {"b": 2, "a": 1}
    p2 = {"a": 1, "b": 2}
    assert canonical_payload_hash(p1) == canonical_payload_hash(p2)


def test_auto_mode_policy_evaluation():
    policy = AutoModePolicy(
        auto_enabled=True,
        allowed_actions=["campaign.pause", "budget.update"],
        max_budget_micros=100_000_000,
        max_daily_spend_micros=500_000_000,
        max_actions_per_day=5,
    )

    # 1. Valid auto action
    res = policy.evaluate(
        action="campaign.pause",
        payload={"campaign_id": "123"},
        today_spend_micros=100_000_000,
        today_action_count=2,
    )
    assert res["ok"] is True
    assert res["mode"] == "auto"

    # 2. Action not in allowlist -> Manual
    res = policy.evaluate(
        action="campaign.create",
        payload={"campaign_id": "123"},
    )
    assert res["ok"] is False
    assert res["reason"] == "action-not-auto-allowed"

    # 3. Budget over ceiling -> Manual
    res = policy.evaluate(
        action="budget.update",
        payload={"amount_micros": 200_000_000},
    )
    assert res["ok"] is False
    assert res["reason"] == "budget-over-ceiling"


def test_proposal_token_service(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-secret-key-that-is-at-least-32-chars-long!")

    actor = "admin@example.com"
    provider = "google_ads"
    account_id = "12345"
    action = "campaign.pause"
    payload_hash = canonical_payload_hash({"a": 1})
    state_hash = canonical_payload_hash({})
    now = int(time.time())

    token = ProposalTokenService.sign(
        actor=actor,
        provider=provider,
        account_id=account_id,
        action=action,
        payload_hash=payload_hash,
        state_hash=state_hash,
        issued_at=now,
    )
    assert token is not None
    assert token.startswith("prop1_")

    # Verify OK
    ok, reason = ProposalTokenService.verify(
        token=token,
        actor=actor,
        provider=provider,
        account_id=account_id,
        action=action,
        payload_hash=payload_hash,
        state_hash=state_hash,
        now=now,
    )
    assert ok is True
    assert reason is None

    # Verify tampered actor -> bad signature
    ok, reason = ProposalTokenService.verify(
        token=token,
        actor="hacker@example.com",
        provider=provider,
        account_id=account_id,
        action=action,
        payload_hash=payload_hash,
        state_hash=state_hash,
        now=now,
    )
    assert ok is False
    assert reason == "bad-signature"


def test_operating_hours_use_the_configured_timezone():
    # 2024-01-01T12:00:00Z is 09:00 in America/Sao_Paulo (UTC-3) but 12:00 in UTC.
    noon_utc = 1704110400
    kwargs = dict(auto_enabled=True, allowed_actions=["campaign.pause"],
                  operating_hour_start=8, operating_hour_end=10)
    in_sp = AutoModePolicy(timezone_name="America/Sao_Paulo", **kwargs)
    in_utc = AutoModePolicy(timezone_name="UTC", **kwargs)
    assert in_sp.evaluate("campaign.pause", {}, now=noon_utc)["ok"] is True
    assert in_utc.evaluate("campaign.pause", {}, now=noon_utc)["reason"] == "outside-operating-hours"


def test_unverifiable_spend_fails_closed_when_a_ceiling_is_set():
    policy = AutoModePolicy(auto_enabled=True, allowed_actions=["campaign.pause"],
                            max_daily_spend_micros=1_000_000)
    verdict = policy.evaluate("campaign.pause", {}, today_spend_micros=None)
    assert verdict == {"ok": False, "mode": "manual", "reason": "daily-spend-unverifiable"}
    # without a ceiling there is nothing to verify
    no_ceiling = AutoModePolicy(auto_enabled=True, allowed_actions=["campaign.pause"])
    assert no_ceiling.evaluate("campaign.pause", {}, today_spend_micros=None)["ok"] is True


def test_empty_proposal_secret_env_falls_back_to_secret_key(monkeypatch):
    monkeypatch.setenv("PROPOSAL_TOKEN_SECRET", "")
    monkeypatch.setenv("SECRET_KEY", "k" * 40)
    assert ProposalTokenService._get_secret() == "k" * 40


"""Etapa 02.7 - the homologation HARNESS itself (guards, allowlist, cost cap, redaction, contract fixes).

These tests use scripted transports: they prove the safety rails work. They are NOT homologation of any provider
(that needs Phase B with real credentials, see docs/etapa-02-07-real-integrations-validation.md)."""
import asyncio
import json
import re
from pathlib import Path

import pytest

from app.ai.adapters import PROVIDER_ADAPTERS
from app.ai.types import ChatRequest, Message, ToolCall, ToolResult
from app.homologation import guard
from app.homologation.runner import Runner
from tests.unit.ai_support import KEYS, anthropic_ok, gemini_ok, jr

ROOT = Path(__file__).resolve().parents[2]


def env(**extra):
    base = {"HOMOLOG_ANTHROPIC_API_KEY": KEYS["anthropic"], "HOMOLOG_ANTHROPIC_MODEL": "m-anth",
            "HOMOLOG_ANTHROPIC_INPUT_PRICE_MICROS_PER_MTOK": "3000000",
            "HOMOLOG_ANTHROPIC_OUTPUT_PRICE_MICROS_PER_MTOK": "15000000"}
    base.update(extra)
    return base


def run(runner):
    return asyncio.run(runner.execute())


# ---- authorization -----------------------------------------------------------------------------------
def test_real_calls_need_flag_and_phrase():
    with pytest.raises(guard.NotAuthorized):
        guard.require_authorization(False, {guard.AUTH_ENV: guard.AUTHORIZATION_PHRASE})
    with pytest.raises(guard.NotAuthorized):
        guard.require_authorization(True, {})
    with pytest.raises(guard.NotAuthorized):
        guard.require_authorization(True, {guard.AUTH_ENV: "yes"})
    guard.require_authorization(True, {guard.AUTH_ENV: guard.AUTHORIZATION_PHRASE})


def test_cli_refuses_without_authorization(monkeypatch):
    from app.homologation.__main__ import main
    monkeypatch.delenv(guard.AUTH_ENV, raising=False)
    assert main(["run", "--execute"]) == 2
    assert main(["run"]) == 2


def test_operations_outside_allowlist_are_rejected():
    with pytest.raises(ValueError):
        run(Runner(env(), only=["gads.create_campaign"]))


# ---- cost cap ------------------------------------------------------------------------------------------
def test_no_price_means_no_request(provider_mock):
    e = env()
    del e["HOMOLOG_ANTHROPIC_OUTPUT_PRICE_MICROS_PER_MTOK"]
    results = run(Runner(e, only=["ai.anthropic.chat_minimal"]))
    assert results["ai.anthropic.chat_minimal"].status == guard.NOT_TESTED
    assert provider_mock.requests == []


def test_cost_cap_blocks_before_sending(provider_mock):
    ledger = guard.CostLedger(max_total_micros=10, max_op_micros=10)   # any real call exceeds 10 micros
    results = run(Runner(env(), ledger, ["ai.anthropic.chat_minimal"]))
    assert results["ai.anthropic.chat_minimal"].status == guard.NOT_TESTED
    assert "cost cap" in results["ai.anthropic.chat_minimal"].detail
    assert provider_mock.requests == []


def test_total_cap_stops_later_calls(provider_mock):
    provider_mock.always("anthropic", lambda r: jr(200, anthropic_ok(text="OK", inp=1000, out=200)))
    ledger = guard.CostLedger(max_total_micros=5000, max_op_micros=guard.usd_to_micros(0.05))
    r = Runner(env(), ledger, ["ai.anthropic.chat_minimal", "ai.anthropic.json_mode"])
    run(r)
    # first call really costs 1000*3 + 200*15 = 6000 micros (> cap): the second one must be refused, never sent
    assert r.results["ai.anthropic.chat_minimal"].cost_micros == 6000
    assert r.results["ai.anthropic.json_mode"].status == guard.NOT_TESTED
    assert len(provider_mock.of("anthropic")) == 1


# ---- the harness on a scripted provider --------------------------------------------------------------------
def test_tool_call_round_trip_passes_and_report_is_clean(provider_mock):
    call = {"id": "tu_1", "name": "get_status", "args": {"component": "homologation"}}
    provider_mock.queue("anthropic", jr(200, anthropic_ok(text=None, tool_calls=[call], inp=50, out=20)),
                        jr(200, anthropic_ok(text="DONE", inp=80, out=5)))
    r = Runner(env(), only=["ai.anthropic.tool_call"])
    results = run(r)
    assert results["ai.anthropic.tool_call"].status == guard.PASSED
    assert len(provider_mock.of("anthropic")) == 2
    text = json.dumps(r.report())
    assert KEYS["anthropic"] not in text and "connectivity probe" not in text and "Reply with" not in text
    assert r.report()["total_cost_usd"] > 0


def test_provider_error_is_reported_failed_without_secret(provider_mock):
    message = f"bad key {KEYS['anthropic']}"
    provider_mock.always("anthropic", lambda r: jr(401, {"type": "error", "error": {"type": "authentication_error", "message": message}}))
    r = Runner(env(), only=["ai.anthropic.verify"])
    result = run(r)["ai.anthropic.verify"]
    assert result.status == guard.FAILED and KEYS["anthropic"] not in json.dumps(r.report())


def test_missing_credentials_are_not_tested_not_failed():
    results = run(Runner({}, only=["ai.openai.verify", "gads.oauth_refresh", "gads.keyword_ideas"]))
    assert {r.status for r in results.values()} == {guard.NOT_TESTED}


def test_dependent_google_ops_do_not_run_after_a_missing_prerequisite():
    full = {"HOMOLOG_GADS_REFRESH_TOKEN": "1//refresh-secret-value", "GOOGLE_ADS_CLIENT_ID": "cid",
            "GOOGLE_ADS_CLIENT_SECRET": "csecret-xx", "GOOGLE_ADS_DEVELOPER_TOKEN": "devtoken-xx",
            "HOMOLOG_GADS_CUSTOMER_ID": "123-456-7890"}
    assert run(Runner(full, only=["gads.account_info"]))["gads.account_info"].status == guard.NOT_TESTED


# ---- redaction -------------------------------------------------------------------------------------------
def test_redactor_masks_values_and_patterns():
    red = guard.Redactor(["my-custom-secret-123"])
    out = red("a my-custom-secret-123 b sk-ant-abcdef123456 c AIzaSyABCDEFGHIJK d ya29.abcdefghijk "
              "e 1//0gAbCdEfGhIj f Bearer abcdefgh12345")
    for leaked in ("my-custom-secret-123", "sk-ant-abcdef123456", "AIzaSyABCDEFGHIJK", "ya29.abcdefghijk",
                   "1//0gAbCdEfGhIj", "abcdefgh12345"):
        assert leaked not in out


# ---- Gemini thought signature (contract fix) ---------------------------------------------------------------
def test_gemini_thought_signature_is_kept_and_replayed():
    adapter = PROVIDER_ADAPTERS["gemini"]
    body = gemini_ok(text=None, tool_calls=[{"id": "c1", "name": "get_status", "args": {"component": "x"}}])
    body["candidates"][0]["content"]["parts"][0]["thoughtSignature"] = "SIG-abc"
    response = adapter.parse(body, "m")
    assert response.tool_calls[0].provider_data == {"thoughtSignature": "SIG-abc"}
    request = ChatRequest(messages=[Message.user("q"), Message("assistant", list(response.tool_calls)),
                                    Message.tool_results(ToolResult("c1", "{}"))])
    parts = adapter.build_body(request, "m")["contents"][1]["parts"]
    assert parts[0]["thoughtSignature"] == "SIG-abc" and "functionCall" in parts[0]


def test_gemini_without_signature_is_unchanged():
    adapter = PROVIDER_ADAPTERS["gemini"]
    request = ChatRequest(messages=[Message("assistant", [ToolCall("c", "t", {})])])
    assert "thoughtSignature" not in adapter.build_body(request, "m")["contents"][0]["parts"][0]


# ---- read-only guarantee ---------------------------------------------------------------------------------
def test_homologation_package_cannot_mutate_google_ads():
    forbidden = re.compile(r"mutate|create_campaign|pause_campaign|enable_campaign|add_negative|google_ads_service", re.I)
    for path in (ROOT / "app" / "homologation").glob("*.py"):
        code = re.sub(r'""".*?"""', "", path.read_text(encoding="utf-8"), flags=re.S)
        code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
        assert not forbidden.findall(code), path.name


def test_real_tests_live_outside_the_default_collection():
    assert (ROOT / "tests_real").is_dir() and "tests_real" not in (ROOT / "pytest.ini").read_text()

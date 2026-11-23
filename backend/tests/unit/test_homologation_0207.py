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
        if path.name == "readonly.py":      # the guard itself imports the module it wraps; checked by its own tests below
            continue
        code = re.sub(r'""".*?"""', "", path.read_text(encoding="utf-8"), flags=re.S)
        code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
        assert not forbidden.findall(code), path.name


def test_real_tests_live_outside_the_default_collection():
    assert (ROOT / "tests_real").is_dir() and "tests_real" not in (ROOT / "pytest.ini").read_text()


# ---- etapa 02.7 pre-flight: ONE aggregate ceiling, read-only guard, no secret leaks -------------------------------
def test_aggregate_ceiling_is_hard_clamped_and_shared_through_the_ledger_file(tmp_path):
    path = str(tmp_path / "ledger.json")
    a = guard.CostLedger(guard.usd_to_micros(50), guard.usd_to_micros(0.05), path=path)
    assert a.max_total_micros == guard.usd_to_micros(1.00)          # --max-total-usd 50 is lowered to the US$ 1.00 ceiling
    a.settle(a.reserve(40_000, "a"), 30_000)                        # real cost below the reservation: only the real one counts
    a.add_external(270_000)
    b = guard.CostLedger(guard.usd_to_micros(1.00), guard.usd_to_micros(0.05), path=path)   # e.g. the agent tests, another process
    assert b.spent_micros == 300_000 and b.remaining_micros == 700_000
    b.add_external(500_000)
    c = guard.CostLedger(guard.usd_to_micros(1.00), guard.usd_to_micros(0.05), path=path)
    assert c.spent_micros == 800_000
    with pytest.raises(guard.CostCapExceeded):
        c.reserve(300_000, "next", enforce_op_cap=False)            # 800k + 300k > 1.00
    assert json.loads(open(path).read()) == {"spent_micros": 800_000, "reservations": {}}   # numbers only


def test_reservation_is_atomic_across_processes(tmp_path):
    """8 independent processes race for a US$ 1.00 ceiling with 0.30 each: exactly 3 may reserve, never more."""
    import subprocess
    import sys
    path = str(tmp_path / "ledger.json")
    code = ("import sys\nfrom app.homologation import guard\n"
            "l = guard.CostLedger(1_000_000, 400_000, path=sys.argv[1])\n"
            "try:\n    l.reserve(300_000, 'race'); print('GOT')\nexcept guard.CostCapExceeded:\n    print('REFUSED')\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, path], cwd=str(ROOT), stdout=subprocess.PIPE, text=True)
             for _ in range(8)]
    outs = [p.communicate(timeout=120)[0].strip().splitlines()[-1] for p in procs]
    assert outs.count("GOT") == 3 and outs.count("REFUSED") == 5
    final = guard.CostLedger(1_000_000, 400_000, path=path)
    assert final.reserved_micros == 900_000 and final.remaining_micros == 100_000


def test_reservation_blocks_a_second_call_before_the_first_settles(tmp_path):
    ledger = guard.CostLedger(100_000, 100_000, path=str(tmp_path / "l.json"))
    token = ledger.reserve(80_000, "first")
    with pytest.raises(guard.CostCapExceeded):
        ledger.reserve(30_000, "second")                            # the open reservation already holds 80k
    ledger.release(token)                                           # failure before sending: given back
    assert ledger.reserve(30_000, "second")


def test_unknown_cost_is_charged_in_full(tmp_path):
    ledger = guard.CostLedger(100_000, 100_000, path=str(tmp_path / "l.json"))
    assert ledger.settle(ledger.reserve(60_000, "interrupted"), None) == 60_000
    assert ledger.spent_micros == 60_000 and ledger.reserved_micros == 0
    with pytest.raises(guard.CostCapExceeded):
        ledger.reserve(50_000, "next")                              # 60k spent + 50k > 100k


def test_unreadable_ledger_refuses_instead_of_resetting(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("{not json")
    with pytest.raises(guard.CostCapExceeded):
        guard.CostLedger(100_000, 100_000, path=str(path))


def test_interrupted_provider_call_is_charged_the_reserved_worst_case(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(PROVIDER_ADAPTERS["anthropic"], "chat", boom)
    ledger = guard.CostLedger(guard.usd_to_micros(1.0), guard.usd_to_micros(0.05), path=str(tmp_path / "l.json"))
    results = run(Runner(env(), ledger, ["ai.anthropic.chat_minimal"]))
    assert results["ai.anthropic.chat_minimal"].status == guard.FAILED
    assert ledger.reserved_micros == 0 and ledger.spent_micros > 0   # not given back: the provider may have processed it


def test_default_aggregate_ceiling_is_one_dollar():
    assert guard.DEFAULT_MAX_TOTAL_USD == guard.HARD_MAX_TOTAL_USD == 1.00


def test_leak_scan_finds_secret_values_without_printing_them(tmp_path):
    env_ = {"HOMOLOG_OPENAI_API_KEY": "sk-proj-SUPERSECRETVALUE123"}
    clean, dirty = tmp_path / "ok.json", tmp_path / "bad.txt"
    clean.write_text('{"status": "approved"}')
    dirty.write_text("oops sk-proj-SUPERSECRETVALUE123")
    assert guard.scan_for_secrets([str(tmp_path)], env_) == [str(dirty)]
    assert "SUPERSECRETVALUE" not in "".join(guard.scan_for_secrets([str(tmp_path)], env_))


class _FakeService:
    def __init__(self):
        self.calls = []

    def search_stream(self, customer_id=None, query=None):
        self.calls.append(("search_stream", query))
        return []

    def mutate_campaigns(self, *a, **k):
        self.calls.append("MUTATION")

    def list_accessible_customers(self):
        return "ok"


class _FakeClient:
    def __init__(self):
        self.service = _FakeService()

    def get_service(self, name, *a, **k):
        return self.service

    def get_type(self, name):
        return object()


def test_readonly_guard_blocks_mutations_before_any_request():
    from app.homologation.readonly import GuardedClient, MutationBlocked
    fake = _FakeClient()
    client = GuardedClient(fake)
    list(client.get_service("GoogleAdsService").search_stream(customer_id="1", query="SELECT customer.id FROM customer"))
    assert client.get_service("CustomerService").list_accessible_customers() == "ok"
    for bad in (lambda: client.get_service("CampaignService"),
                lambda: client.get_service("CampaignBudgetService"),
                lambda: client.get_service("GoogleAdsService").mutate(customer_id="1", mutate_operations=[]),
                lambda: client.get_service("GoogleAdsService").search_stream(customer_id="1", query="UPDATE x"),
                lambda: client.get_service("GoogleAdsService").search_stream(customer_id="1", query="SELECT a FROM b; DROP"),
                lambda: client.get_service("CustomerService").create_customer_client(),
                lambda: client.mutate()):
        with pytest.raises(MutationBlocked):
            bad()
    assert "MUTATION" not in fake.service.calls and len(fake.service.calls) == 1


def test_readonly_install_wraps_both_application_factories(monkeypatch):
    from app.homologation import readonly
    from app.services import google_ads_reader as reader
    from app.services import google_ads_service as svc
    monkeypatch.setattr(reader, "CLIENT_FACTORY", lambda token, login: _FakeClient())
    monkeypatch.setattr(svc, "_build_client", lambda token: _FakeClient())
    uninstall = readonly.install()
    try:
        with pytest.raises(readonly.MutationBlocked):
            reader.CLIENT_FACTORY("t", None).get_service("CampaignService")
        with pytest.raises(readonly.MutationBlocked):
            svc._build_client("t").get_service("CampaignBudgetService")
        assert reader.CLIENT_FACTORY("t", None).get_service("GoogleAdsService") is not None
    finally:
        uninstall()

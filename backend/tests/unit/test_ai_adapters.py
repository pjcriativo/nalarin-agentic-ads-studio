"""Equivalent CONTRACT for the three provider adapters (Anthropic, Gemini, OpenAI), with scripted
httpx transports: the same neutral request must produce the right wire format on each provider,
and each provider's answers/errors must map onto the same neutral response/error model."""
import asyncio
import json

import httpx
import pytest

from app.ai import errors as E
from app.ai import types as T
from app.ai.adapters import PROVIDER_ADAPTERS, get_adapter
from tests.unit.ai_support import KEYS, OK, anthropic_ok, gemini_ok, jr, openai_ok

PROVIDERS = ["anthropic", "gemini", "openai"]
MODEL = "test-model-x"
WEATHER = T.ToolSpec("get_weather", "Weather of a city", {"type": "object", "properties": {"city": {"type": "string"}},
                                                         "required": ["city"], "additionalProperties": False})


def run(coro):
    return asyncio.run(coro)


def chat(provider, request, mock, answer=None, queue=None):
    mock.queue(provider, *(queue or [jr(200, answer if answer is not None else OK[provider]())]))
    return run(get_adapter(provider).chat(request, MODEL, KEYS[provider], 5.0))


def basic():
    return T.ChatRequest(messages=[T.Message.user("Hi there")], system="Be brief", max_tokens=100, temperature=0.2)


class TestRegistry:
    def test_exactly_the_three_providers(self):
        assert sorted(PROVIDER_ADAPTERS) == PROVIDERS
        with pytest.raises(ValueError):
            get_adapter("nope")

    def test_the_code_contains_no_model_names(self):
        """Model names/prices are DATA (ai_models); none may be hard-coded in the gateway code."""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[2] / "app"
        pattern = re.compile(r"\b(gpt-?\d|o\d-(mini|preview)|claude-(opus|sonnet|haiku|\d|fable)|gemini-(\d|pro|flash|ultra|nano)|text-embedding|davinci)", re.I)
        offenders = []
        for path in list(root.rglob("ai/**/*.py")) + [root / "api" / "v1" / "ai.py", root / "api" / "v1" / "copy_generation.py",
                                                       root / "services" / "ad_remix_service.py"]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == []


class TestRequestMapping:
    def test_headers_carry_the_key_and_the_url_never_does(self, provider_mock):
        for provider in PROVIDERS:
            chat(provider, basic(), provider_mock)
            request = provider_mock.of(provider)[-1]
            assert KEYS[provider] not in str(request.url)
            headers = {k.lower(): v for k, v in request.headers.items()}
            carried = {"anthropic": headers.get("x-api-key"), "openai": (headers.get("authorization") or "").removeprefix("Bearer "),
                       "gemini": headers.get("x-goog-api-key")}[provider]
            assert carried == KEYS[provider]
        assert provider_mock.of("anthropic")[-1].headers["anthropic-version"]

    def test_system_prompt_and_limits_are_mapped(self, provider_mock):
        chat("anthropic", basic(), provider_mock)
        body = provider_mock.body(provider_mock.of("anthropic")[-1])
        assert body["model"] == MODEL and body["system"] == "Be brief" and body["max_tokens"] == 100 and body["temperature"] == 0.2
        assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Hi there"}]}]

        chat("openai", basic(), provider_mock)
        body = provider_mock.body(provider_mock.of("openai")[-1])
        assert body["messages"][0] == {"role": "system", "content": "Be brief"} and body["messages"][1] == {"role": "user", "content": "Hi there"}
        assert body["max_completion_tokens"] == 100 and body["temperature"] == 0.2 and "max_tokens" not in body

        chat("gemini", basic(), provider_mock)
        request = provider_mock.of("gemini")[-1]
        body = provider_mock.body(request)
        assert request.url.path.endswith(f"/models/{MODEL}:generateContent")
        assert body["systemInstruction"] == {"parts": [{"text": "Be brief"}]}
        assert body["contents"] == [{"role": "user", "parts": [{"text": "Hi there"}]}]
        assert body["generationConfig"] == {"maxOutputTokens": 100, "temperature": 0.2}

    def test_temperature_is_omitted_unless_given(self, provider_mock):
        request = T.ChatRequest(messages=[T.Message.user("x")])
        for provider in PROVIDERS:
            chat(provider, request, provider_mock)
            assert "temperature" not in json.dumps(provider_mock.body(provider_mock.of(provider)[-1]))

    def test_json_mode(self, provider_mock):
        request = T.ChatRequest(messages=[T.Message.user("x")], json_mode=True)
        for provider in PROVIDERS:
            chat(provider, request, provider_mock)
        assert provider_mock.body(provider_mock.of("openai")[-1])["response_format"] == {"type": "json_object"}
        assert provider_mock.body(provider_mock.of("gemini")[-1])["generationConfig"]["responseMimeType"] == "application/json"
        assert "JSON" in provider_mock.body(provider_mock.of("anthropic")[-1])["system"]

    def test_tools_and_tool_choice(self, provider_mock):
        for choice in ("auto", "none", "required", {"name": "get_weather"}):
            request = T.ChatRequest(messages=[T.Message.user("x")], tools=[WEATHER], tool_choice=choice)
            for provider in PROVIDERS:
                chat(provider, request, provider_mock)
        by = {p: [provider_mock.body(r) for r in provider_mock.of(p)] for p in PROVIDERS}
        assert [b["tool_choice"] for b in by["anthropic"]] == [{"type": "auto"}, {"type": "none"}, {"type": "any"}, {"type": "tool", "name": "get_weather"}]
        assert by["anthropic"][0]["tools"] == [{"name": "get_weather", "description": "Weather of a city", "input_schema": WEATHER.parameters}]
        assert [b["tool_choice"] for b in by["openai"]] == ["auto", "none", "required", {"type": "function", "function": {"name": "get_weather"}}]
        assert by["openai"][0]["tools"][0] == {"type": "function", "function": {"name": "get_weather", "description": "Weather of a city", "parameters": WEATHER.parameters}}
        modes = [b["toolConfig"]["functionCallingConfig"] for b in by["gemini"]]
        assert modes == [{"mode": "AUTO"}, {"mode": "NONE"}, {"mode": "ANY"}, {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}]
        declared = by["gemini"][0]["tools"][0]["functionDeclarations"][0]
        assert declared["name"] == "get_weather" and "additionalProperties" not in declared["parameters"]  # unsupported keyword dropped

    def test_images(self, provider_mock):
        request = T.ChatRequest(messages=[T.Message.user("what is this", T.ImagePart("image/png", b"\x89PNGdata"))])
        for provider in PROVIDERS:
            chat(provider, request, provider_mock)
        a = provider_mock.body(provider_mock.of("anthropic")[-1])["messages"][0]["content"][1]
        assert a["type"] == "image" and a["source"]["type"] == "base64" and a["source"]["media_type"] == "image/png"
        o = provider_mock.body(provider_mock.of("openai")[-1])["messages"][0]["content"][1]
        assert o["type"] == "image_url" and o["image_url"]["url"].startswith("data:image/png;base64,")
        g = provider_mock.body(provider_mock.of("gemini")[-1])["contents"][0]["parts"][1]
        assert g["inline_data"]["mime_type"] == "image/png" and g["inline_data"]["data"]

    def test_stop_sequences(self, provider_mock):
        request = T.ChatRequest(messages=[T.Message.user("x")], stop=["END"])
        for provider in PROVIDERS:
            chat(provider, request, provider_mock)
        assert provider_mock.body(provider_mock.of("anthropic")[-1])["stop_sequences"] == ["END"]
        assert provider_mock.body(provider_mock.of("openai")[-1])["stop"] == ["END"]
        assert provider_mock.body(provider_mock.of("gemini")[-1])["generationConfig"]["stopSequences"] == ["END"]


class TestResponseMapping:
    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_text_usage_and_finish(self, provider, provider_mock):
        response = chat(provider, basic(), provider_mock, OK[provider](text="Olá!", inp=12, out=7))
        assert response.text == "Olá!" and response.finish_reason == T.STOP and response.provider == provider
        assert (response.usage.input_tokens, response.usage.output_tokens, response.usage.total_tokens) == (12, 7, 19)
        assert response.tool_calls == []

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_tool_calls_come_back_in_the_same_neutral_shape(self, provider, provider_mock):
        calls = [{"id": "call_1", "name": "get_weather", "args": {"city": "Lisbon"}}]
        response = chat(provider, T.ChatRequest(messages=[T.Message.user("x")], tools=[WEATHER]), provider_mock,
                        OK[provider](text=None, tool_calls=calls))
        assert response.finish_reason == T.TOOL_CALLS
        assert [(c.name, c.arguments) for c in response.tool_calls] == [("get_weather", {"city": "Lisbon"})]
        assert response.tool_calls[0].id

    @pytest.mark.parametrize("provider,body,expected", [
        ("anthropic", anthropic_ok(stop="max_tokens"), T.LENGTH),
        ("anthropic", anthropic_ok(stop="refusal"), T.CONTENT_FILTER),
        ("openai", openai_ok(finish="length"), T.LENGTH),
        ("openai", openai_ok(finish="content_filter"), T.CONTENT_FILTER),
        ("gemini", gemini_ok(finish="MAX_TOKENS"), T.LENGTH),
    ])
    def test_finish_reasons_are_normalized(self, provider, body, expected, provider_mock):
        assert chat(provider, basic(), provider_mock, body).finish_reason == expected

    def test_provider_specific_usage_details_are_normalized(self, provider_mock):
        a = chat("anthropic", basic(), provider_mock, anthropic_ok(inp=10, out=5, extra_usage={
            "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20, "output_tokens_details": {"thinking_tokens": 3}}))
        assert (a.usage.input_tokens, a.usage.cached_input_tokens, a.usage.reasoning_tokens, a.usage.output_tokens) == (130, 100, 3, 5)
        o = chat("openai", basic(), provider_mock, openai_ok(inp=50, out=30, usage_extra={
            "prompt_tokens_details": {"cached_tokens": 40}, "completion_tokens_details": {"reasoning_tokens": 12}}))
        assert (o.usage.input_tokens, o.usage.cached_input_tokens, o.usage.reasoning_tokens, o.usage.output_tokens) == (50, 40, 12, 30)
        g = chat("gemini", basic(), provider_mock, gemini_ok(inp=60, out=20, usage_extra={"cachedContentTokenCount": 25, "thoughtsTokenCount": 8}))
        assert (g.usage.input_tokens, g.usage.cached_input_tokens, g.usage.reasoning_tokens, g.usage.output_tokens) == (60, 25, 8, 28)


class TestToolRoundTrip:
    def history(self):
        call = T.ToolCall("call_9", "get_weather", {"city": "Lisbon"})
        return T.ChatRequest(tools=[WEATHER], messages=[
            T.Message.user("weather?"),
            T.Message("assistant", [T.TextPart("checking"), call]),
            T.Message.tool_results(T.ToolResult("call_9", "sunny, 24C")),
        ])

    def test_anthropic(self, provider_mock):
        chat("anthropic", self.history(), provider_mock)
        messages = provider_mock.body(provider_mock.of("anthropic")[-1])["messages"]
        assert messages[1]["role"] == "assistant" and messages[1]["content"][1] == {"type": "tool_use", "id": "call_9", "name": "get_weather", "input": {"city": "Lisbon"}}
        assert messages[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_9", "content": "sunny, 24C"}]}

    def test_openai(self, provider_mock):
        chat("openai", self.history(), provider_mock)
        messages = provider_mock.body(provider_mock.of("openai")[-1])["messages"]
        assistant = messages[1]
        assert assistant["role"] == "assistant" and assistant["content"] == "checking"
        assert assistant["tool_calls"] == [{"id": "call_9", "type": "function", "function": {"name": "get_weather", "arguments": json.dumps({"city": "Lisbon"})}}]
        assert messages[2] == {"role": "tool", "tool_call_id": "call_9", "content": "sunny, 24C"}

    def test_gemini_resolves_the_function_name_from_the_call_id(self, provider_mock):
        chat("gemini", self.history(), provider_mock)
        contents = provider_mock.body(provider_mock.of("gemini")[-1])["contents"]
        assert contents[1] == {"role": "model", "parts": [{"text": "checking"}, {"functionCall": {"name": "get_weather", "args": {"city": "Lisbon"}}}]}
        assert contents[2] == {"role": "user", "parts": [{"functionResponse": {"name": "get_weather", "response": {"result": "sunny, 24C"}}}]}

    def test_error_results_are_marked(self, provider_mock):
        req = T.ChatRequest(messages=[T.Message.user("x"), T.Message("assistant", [T.ToolCall("c1", "t", {})]),
                                      T.Message.tool_results(T.ToolResult("c1", "boom", is_error=True))])
        chat("anthropic", req, provider_mock)
        assert provider_mock.body(provider_mock.of("anthropic")[-1])["messages"][2]["content"][0]["is_error"] is True
        chat("gemini", req, provider_mock)
        assert "error" in provider_mock.body(provider_mock.of("gemini")[-1])["contents"][2]["parts"][0]["functionResponse"]["response"]


ERROR_CASES = [
    # (provider, status, body, expected kind)
    ("anthropic", 401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, E.AUTHENTICATION),
    ("anthropic", 403, {"type": "error", "error": {"type": "permission_error", "message": "no"}}, E.PERMISSION),
    ("anthropic", 404, {"type": "error", "error": {"type": "not_found_error", "message": "model"}}, E.NOT_FOUND),
    ("anthropic", 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}, E.INVALID_REQUEST),
    ("anthropic", 402, {"type": "error", "error": {"type": "billing_error", "message": "credit"}}, E.QUOTA),
    ("anthropic", 413, {"type": "error", "error": {"type": "request_too_large", "message": "big"}}, E.INVALID_REQUEST),
    ("anthropic", 429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}, E.RATE_LIMIT),
    ("anthropic", 500, {"type": "error", "error": {"type": "api_error", "message": "oops"}}, E.UNAVAILABLE),
    ("anthropic", 529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}, E.UNAVAILABLE),
    ("anthropic", 504, {"type": "error", "error": {"type": "timeout_error", "message": "slow"}}, E.TIMEOUT),
    ("openai", 401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error", "code": "invalid_api_key"}}, E.AUTHENTICATION),
    ("openai", 403, {"error": {"message": "region", "type": "invalid_request_error", "code": "unsupported_country_region_territory"}}, E.PERMISSION),
    ("openai", 404, {"error": {"message": "no model", "type": "invalid_request_error", "code": "model_not_found"}}, E.NOT_FOUND),
    ("openai", 400, {"error": {"message": "bad", "type": "invalid_request_error", "code": None}}, E.INVALID_REQUEST),
    ("openai", 429, {"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"}}, E.RATE_LIMIT),
    ("openai", 429, {"error": {"message": "slow down", "type": "rate_limit_error", "code": "slow_down"}}, E.RATE_LIMIT),
    ("openai", 429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}}, E.QUOTA),
    ("openai", 429, {"error": {"message": "spend", "type": "x", "code": "project_spend_limit_exceeded"}}, E.QUOTA),
    ("openai", 500, {"error": {"message": "server", "type": "server_error", "code": None}}, E.UNAVAILABLE),
    ("openai", 503, {"error": {"message": "overloaded", "type": "service_unavailable_error", "code": "server_is_overloaded"}}, E.UNAVAILABLE),
    ("gemini", 400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}, E.AUTHENTICATION),
    ("gemini", 400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}}, E.INVALID_REQUEST),
    ("gemini", 403, {"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}, E.PERMISSION),
    ("gemini", 404, {"error": {"code": 404, "message": "model", "status": "NOT_FOUND"}}, E.NOT_FOUND),
    ("gemini", 429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}, E.RATE_LIMIT),
    ("gemini", 500, {"error": {"code": 500, "message": "internal", "status": "INTERNAL"}}, E.UNAVAILABLE),
    ("gemini", 503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}}, E.UNAVAILABLE),
    ("gemini", 504, {"error": {"code": 504, "message": "deadline", "status": "DEADLINE_EXCEEDED"}}, E.TIMEOUT),
]


class TestErrors:
    @pytest.mark.parametrize("provider,status,body,kind", ERROR_CASES)
    def test_provider_errors_map_to_the_unified_kinds(self, provider, status, body, kind, provider_mock):
        provider_mock.queue(provider, jr(status, body))
        with pytest.raises(E.AIError) as caught:
            run(get_adapter(provider).chat(basic(), MODEL, KEYS[provider], 5.0))
        assert caught.value.kind == kind and caught.value.provider == provider and caught.value.status == status

    def test_only_transient_kinds_allow_automatic_fallback(self):
        eligible = {k for k in (E.AUTHENTICATION, E.PERMISSION, E.QUOTA, E.RATE_LIMIT, E.TIMEOUT, E.UNAVAILABLE, E.INVALID_REQUEST,
                                E.NOT_FOUND, E.CONTENT_BLOCKED, E.INVALID_RESPONSE, E.BUDGET_EXCEEDED, E.NOT_CONFIGURED, E.UNKNOWN)
                    if E.AIError(k).fallback_eligible}
        assert eligible == {E.RATE_LIMIT, E.TIMEOUT, E.UNAVAILABLE}

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_timeout_and_connection_failures(self, provider, provider_mock):
        provider_mock.queue(provider, httpx.ReadTimeout("took too long"), httpx.ConnectError("dns failure for host"))
        with pytest.raises(E.AIError) as first:
            run(get_adapter(provider).chat(basic(), MODEL, KEYS[provider], 5.0))
        with pytest.raises(E.AIError) as second:
            run(get_adapter(provider).chat(basic(), MODEL, KEYS[provider], 5.0))
        assert (first.value.kind, second.value.kind) == (E.TIMEOUT, E.UNAVAILABLE)
        assert first.value.__cause__ is None and second.value.__cause__ is None  # no httpx object (URL, proxy...) is chained

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_unusable_answers(self, provider, provider_mock):
        provider_mock.queue(provider, httpx.Response(200, content=b"<html>not json</html>"), jr(200, {"unexpected": True}), jr(200, []))
        for _ in range(3):
            with pytest.raises(E.AIError) as caught:
                run(get_adapter(provider).chat(basic(), MODEL, KEYS[provider], 5.0))
            assert caught.value.kind == E.INVALID_RESPONSE

    def test_openai_tool_arguments_must_be_a_json_object(self, provider_mock):
        bad = openai_ok(tool_calls=[{"id": "c", "name": "t", "args": {}}])
        bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
        provider_mock.queue("openai", jr(200, bad))
        with pytest.raises(E.AIError) as caught:
            run(get_adapter("openai").chat(basic(), MODEL, KEYS["openai"], 5.0))
        assert caught.value.kind == E.INVALID_RESPONSE

    def test_gemini_blocked_prompt_and_blocked_candidate(self, provider_mock):
        provider_mock.queue("gemini", jr(200, {"promptFeedback": {"blockReason": "SAFETY"}}),
                            jr(200, {"candidates": [{"finishReason": "SAFETY"}], "usageMetadata": {}}))
        for _ in range(2):
            with pytest.raises(E.AIError) as caught:
                run(get_adapter("gemini").chat(basic(), MODEL, KEYS["gemini"], 5.0))
            assert caught.value.kind == E.CONTENT_BLOCKED

    @pytest.mark.parametrize("provider,status,body", [(p, s, b) for p, s, b, _ in ERROR_CASES[:1] + ERROR_CASES[10:11] + ERROR_CASES[20:21]])
    def test_a_provider_that_echoes_the_key_never_leaks_it(self, provider, status, body, provider_mock):
        key = KEYS[provider]
        body = json.loads(json.dumps(body).replace("invalid x-api-key", f"invalid x-api-key {key}")
                          .replace("Incorrect API key provided", f"Incorrect API key provided: {key}")
                          .replace("API key not valid.", f"API key not valid: {key}."))
        provider_mock.queue(provider, jr(status, body))
        with pytest.raises(E.AIError) as caught:
            run(get_adapter(provider).chat(basic(), MODEL, key, 5.0))
        shown = f"{caught.value!s} {caught.value!r} {caught.value.message}"
        assert key not in shown and key[-8:] not in shown

    def test_redaction_patterns(self):
        text = "sk-proj-abcdefghijklmnop AIzaSyAbcdefghijklmnopqrstuv Bearer abcdefgh12345678 x-api-key: hunter2hunter2 ?key=SECRETVALUE9 custom-secret-12345"
        out = E.redact(text, secrets=["custom-secret-12345"])
        for leaked in ("abcdefghijklmnop", "AIzaSyAbc", "abcdefgh12345678", "hunter2", "SECRETVALUE9", "custom-secret"):
            assert leaked not in out
        assert len(E.redact("x" * 5000)) == E.MAX_MESSAGE


class TestModelDiscoveryAndVerification:
    def test_list_models_normalized(self, provider_mock):
        provider_mock.queue("anthropic", jr(200, {"data": [{"id": "a-1", "display_name": "A One", "type": "model"}], "has_more": False}))
        provider_mock.queue("openai", jr(200, {"object": "list", "data": [{"id": "o-1", "object": "model"}]}))
        provider_mock.queue("gemini", jr(200, {"models": [
            {"name": "models/g-1", "displayName": "G One", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed-1", "supportedGenerationMethods": ["embedContent"]}]}))
        assert run(get_adapter("anthropic").list_models(KEYS["anthropic"], 5)) == [{"model_id": "a-1", "display_name": "A One"}]
        assert run(get_adapter("openai").list_models(KEYS["openai"], 5)) == [{"model_id": "o-1", "display_name": None}]
        assert run(get_adapter("gemini").list_models(KEYS["gemini"], 5)) == [{"model_id": "g-1", "display_name": "G One"}]  # chat-capable only
        assert provider_mock.of("anthropic")[0].url.path == "/v1/models" and provider_mock.of("gemini")[0].method == "GET"

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_verify_succeeds_and_fails_with_a_precise_error(self, provider, provider_mock):
        ok = {"anthropic": {"data": []}, "openai": {"data": []}, "gemini": {"models": []}}[provider]
        provider_mock.queue(provider, jr(200, ok), jr(401, {"error": {"message": "nope", "type": "authentication_error"}}))
        run(get_adapter(provider).verify(KEYS[provider]))  # no exception
        with pytest.raises(E.AIError) as caught:
            run(get_adapter(provider).verify("wrong-key-00000000"))
        assert caught.value.kind == E.AUTHENTICATION

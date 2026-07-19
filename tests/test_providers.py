from unittest.mock import patch
from self_governance.providers import (
    get_provider,
    GeminiProvider,
    OpenRouterProvider,
    parse_openrouter_response,
    parse_gemini_response,
)


def test_get_provider_defaults_to_gemini():
    assert isinstance(get_provider(api_key="AIzaSomeGeminiKey"), GeminiProvider)
    assert isinstance(get_provider(api_key=None), GeminiProvider)


def test_get_provider_routes_openrouter_keys_to_openrouter_provider():
    assert isinstance(get_provider(api_key="sk-or-v1-abc123"), OpenRouterProvider)


def test_openrouter_provider_builds_openai_compatible_request():
    """The OpenRouter provider must hit openrouter.ai with an OpenAI-
    compatible chat completions payload, so any OpenRouter-hosted model
    (Claude, GPT, Llama, ...) works through the same request shape."""
    provider = OpenRouterProvider()
    captured = {}

    def fake_execute_request(url, headers, data, parser_func):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["parser_func"] = parser_func
        return {"text": "ok", "prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "STOP"}

    with patch("self_governance.providers._execute_request", side_effect=fake_execute_request):
        result = provider.generate_content(
            prompt="hello",
            api_key="sk-or-v1-abc123",
            model="anthropic/claude-3.5-sonnet",
            system_instruction="be helpful",
            temperature=0.5,
        )

    assert result["text"] == "ok"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-v1-abc123"
    assert captured["data"]["model"] == "anthropic/claude-3.5-sonnet"
    assert captured["data"]["messages"][0] == {"role": "system", "content": "be helpful"}
    assert captured["data"]["messages"][1] == {"role": "user", "content": "hello"}
    assert captured["data"]["temperature"] == 0.5
    assert captured["parser_func"] is parse_openrouter_response


def test_openrouter_provider_defaults_model_when_none_given():
    """The fallback must come from config.DEFAULT_OPENROUTER_MODEL, not a
    bare literal in providers.py -- a hardcoded fallback here would be
    silently inconsistent with every other provider's routing-through-
    config convention (an OpenRouter key falling back to an unexpected,
    possibly costlier model with no visibility into why)."""
    from self_governance.config import DEFAULT_OPENROUTER_MODEL

    provider = OpenRouterProvider()
    captured = {}

    def fake_execute_request(url, headers, data, parser_func):
        captured["data"] = data
        return {"text": "ok", "prompt_tokens": 0, "completion_tokens": 0, "finish_reason": "STOP"}

    with patch("self_governance.providers._execute_request", side_effect=fake_execute_request):
        provider.generate_content(prompt="hello", api_key="sk-or-v1-abc123", model=None)

    assert captured["data"]["model"] == DEFAULT_OPENROUTER_MODEL


def test_parse_openrouter_response_extracts_text_and_usage():
    res_data = {
        "choices": [{"message": {"content": "  hello there  "}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
    }

    result = parse_openrouter_response(res_data)

    assert result["text"] == "hello there"
    assert result["prompt_tokens"] == 12
    assert result["completion_tokens"] == 4
    assert result["finish_reason"] == "STOP"


def test_parse_openrouter_response_handles_empty_choices():
    result = parse_openrouter_response({"choices": [], "usage": {}})

    assert result["text"] == ""
    assert result["finish_reason"] == "STOP"


def test_gemini_provider_is_default_for_non_openrouter_keys():
    """Sanity check that the provider abstraction didn't silently break
    Gemini's existing response parsing while adding OpenRouter support."""
    provider = GeminiProvider()
    captured = {}

    def fake_execute_request(url, headers, data, parser_func):
        captured["url"] = url
        captured["parser_func"] = parser_func
        return {"text": "ok", "prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "STOP"}

    with patch("self_governance.providers._execute_request", side_effect=fake_execute_request):
        provider.generate_content(prompt="hello", api_key="AIzaSomeGeminiKey", model="gemini-2.5-flash")

    assert "generativelanguage" in captured["url"]
    assert captured["parser_func"] is parse_gemini_response

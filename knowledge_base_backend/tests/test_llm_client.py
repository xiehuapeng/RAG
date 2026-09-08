from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import OpenAI

from app.services import llm_client
from app.services import query_understanding as understanding


@pytest.mark.parametrize("status", [429, 500, 503])
def test_remote_errors_are_not_retried(status, monkeypatch):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "injected"}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with OpenAI(api_key="test-only", base_url="https://example.invalid/v1", http_client=http, max_retries=0) as client:
            monkeypatch.setattr(llm_client, "get_client", lambda: client)
            with pytest.raises(Exception):
                llm_client.chat_completion([], timeout_seconds=7)
    assert len(requests) == 1
    assert requests[0].extensions["timeout"]["read"] == 7
    assert requests[0].extensions["timeout"]["connect"] == 7


@pytest.mark.parametrize("content,reason", [("", "stop"), ("partial", "length")])
def test_empty_or_truncated_output_is_rejected(content, reason, monkeypatch):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=reason),
    ])
    monkeypatch.setattr(llm_client, "get_client", lambda: client)
    with pytest.raises(RuntimeError):
        llm_client.chat_completion([])


def test_cached_client_has_no_sdk_retries(monkeypatch):
    monkeypatch.setattr(llm_client, "OPENAI_API_KEY", "test-only")
    llm_client.get_client.cache_clear()
    client = llm_client.get_client()
    try:
        assert client.max_retries == 0
    finally:
        client.close()
        llm_client.get_client.cache_clear()


def inputs():
    return dict(query="test", history=[], session_summary={}, keyword_hits={}, follow_up={}, route_rules={})


def test_remote_provider_receives_configured_timeout(monkeypatch):
    service = understanding.QueryUnderstandingService()
    service.timeout_seconds = 13
    monkeypatch.setattr(service, "_build_prompt", lambda *args: "test")
    monkeypatch.setattr(service, "_result_from_model_payload", lambda *args, **kwargs: "parsed")
    completion = Mock(return_value='{"route":"kb_qa"}')
    monkeypatch.setattr(understanding, "chat_completion", completion)
    assert service.understand_with_remote(**inputs(), model_name="test", fallback_used=False) == "parsed"
    assert completion.call_args.kwargs["timeout_seconds"] == 13


@pytest.mark.parametrize("elapsed,expected_remaining", [(45, 15), (61, None)])
def test_fallback_shares_remaining_budget(elapsed, expected_remaining, monkeypatch):
    service = understanding.QueryUnderstandingService()
    service.provider, service.fallback_provider = "remote", "ollama"
    service.timeout_seconds, service.total_timeout_seconds = 45, 60
    monkeypatch.setattr(understanding, "monotonic", Mock(side_effect=[0, 0, elapsed]))
    primary = Mock(side_effect=httpx.ReadTimeout("injected timeout"))
    fallback = Mock(return_value="fallback result")
    monkeypatch.setattr(service, "understand_with_remote", primary)
    monkeypatch.setattr(service, "understand_with_ollama", fallback)
    result = service.understand_with_fallback(**inputs())
    assert primary.call_args.kwargs["timeout_seconds"] == 45
    if expected_remaining is None:
        assert result is None
        fallback.assert_not_called()
    else:
        assert result == "fallback result"
        assert fallback.call_args.kwargs["timeout_seconds"] == expected_remaining
        assert fallback.call_args.kwargs["fallback_used"] is True

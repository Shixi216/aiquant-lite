from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from router.providers.openai_compatible import ProviderOutputError, invoke_openai_compatible_chat
from router.schemas import RouterInvokeResponse
from trading.research.policy_news.extractor import PolicyNewsExtractor
from trading.research.sentiment.event_extractor import SentimentEventExtractor
from trading.research.sentiment.schemas import SchemaValidationStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("content,finish", [(None, "length"), ("  ", "stop"), ('{"incomplete":', "length")])
async def test_unusable_http_completions_retain_usage_without_exposing_reasoning(monkeypatch, content, finish):
    async_client = httpx.AsyncClient

    def respond(request):
        return httpx.Response(200, json={
            "model": "fixture-deepseek", "choices": [{"finish_reason": finish, "message": {
                "content": content, "reasoning_content": "private reasoning fixture",
            }}], "usage": {"prompt_tokens": 11, "completion_tokens": 800},
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    with pytest.raises(ProviderOutputError) as failure:
        await invoke_openai_compatible_chat(
            provider_name="deepseek", provider_label="DeepSeek", secret=SecretStr("fixture-key"),
            base_url="https://example.invalid", model="fixture", role="risk_controller",
            prompt="fixture", system_prompt="fixture", temperature=0, max_tokens=800,
        )
    error = failure.value
    assert error.response.model == "fixture-deepseek"
    assert error.response.usage == {"prompt_tokens": 11, "completion_tokens": 800}
    assert error.response.finish_reason == finish and error.response.latency_ms >= 0
    assert "private reasoning fixture" not in str(error)
    assert "truncated output" in str(error) if finish == "length" else "empty content" in str(error)


@pytest.mark.asyncio
async def test_complete_http_answer_ignores_reasoning_and_preserves_usage(monkeypatch):
    async_client = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "fixture-deepseek", "choices": [{"finish_reason": "stop", "message": {
                "content": "  final answer  ", "reasoning_content": "private reasoning fixture",
            }}], "usage": {"prompt_tokens": 11, "completion_tokens": 42, "ignored_bool": True},
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    result = await invoke_openai_compatible_chat(
        provider_name="deepseek", provider_label="DeepSeek", secret=SecretStr("fixture-key"),
        base_url="https://example.invalid", model="fixture", role="risk_controller",
        prompt="fixture", system_prompt="fixture", temperature=0, max_tokens=512,
    )
    assert result.content == "final answer" and result.finish_reason == "stop"
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 42}
    assert len(calls) == 1 and calls[0]["max_tokens"] == 512


class MemoryAudit:
    def __init__(self):
        self.calls = []

    def record_model_call(self, **call):
        self.calls.append(call)
        return f"fixture-call-{len(self.calls)}"


def truncated_response():
    return RouterInvokeResponse(
        role="risk_controller", provider="deepseek", model="fixture-deepseek",
        content="", latency_ms=17, finish_reason="length",
        usage={"prompt_tokens": 11, "completion_tokens": 800},
    )


@pytest.mark.parametrize("extractor_class", [SentimentEventExtractor, PolicyNewsExtractor])
def test_failed_completion_audit_preserves_known_model_and_tokens(extractor_class):
    audit = MemoryAudit()
    extractor = extractor_class(audit_store=audit)
    extractor._audit(
        role="risk_controller", provider="deepseek", response=None, input_hash="fixture",
        retry_count=0, validation=SchemaValidationStatus.INVALID,
        error=ProviderOutputError("DeepSeek", truncated_response()),
    )
    call = audit.calls[0]
    assert not call["success"] and call["model"] == "fixture-deepseek"
    assert call["usage"] == {"prompt_tokens": 11, "completion_tokens": 800}
    assert call["latency_ms"] == 17 and "finish_reason=length" in call["error_message"]


@pytest.mark.parametrize("extractor_class", [SentimentEventExtractor, PolicyNewsExtractor])
def test_network_failure_audit_retains_underlying_error_class(extractor_class):
    audit = MemoryAudit()
    try:
        try:
            raise httpx.ConnectTimeout("fixture timeout")
        except httpx.HTTPError as cause:
            raise RuntimeError("provider network failure") from cause
    except RuntimeError as error:
        extractor_class(audit_store=audit)._audit(
            role="news_processor", provider="qwen", response=None, input_hash="fixture",
            retry_count=0, validation=SchemaValidationStatus.INVALID, error=error,
        )
    assert audit.calls[0]["error_type"] == "ConnectTimeout"
    assert audit.calls[0]["usage"] == {} and audit.calls[0]["latency_ms"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("extractor_class,primary_budget,module", [
    (SentimentEventExtractor, 800, "trading.research.sentiment.event_extractor"),
    (PolicyNewsExtractor, 1200, "trading.research.policy_news.extractor"),
])
@pytest.mark.parametrize("provider_name", ["qwen", "deepseek"])
async def test_automated_review_budget_and_retries_stay_bounded(
    monkeypatch, extractor_class, primary_budget, module, provider_name,
):
    requests = []

    class TruncatedProvider:
        async def invoke(self, **request):
            requests.append(request)
            raise ProviderOutputError("Fixture", truncated_response())

    monkeypatch.setattr(module + "._provider_ready", lambda _: True)
    audit = MemoryAudit()
    extractor = extractor_class(provider_factory=lambda _: TruncatedProvider(), audit_store=audit)
    extraction, call_ids, status = await extractor._invoke_validated(
        provider_name=provider_name, role="risk_controller", prompt=json.dumps({"fixture": True}),
    )
    assert extraction is None and status.value == "INVALID"
    assert len(requests) == len(call_ids) == len(audit.calls) == 2
    assert {request["max_tokens"] for request in requests} == {
        8192 if provider_name == "deepseek" else primary_budget,
    }
    assert all(not call["success"] for call in audit.calls)

import json

import pytest

from router.schemas import RouterInvokeRequest, RouterInvokeResponse
from router.services.research_synthesizer_handler import ResearchSynthesizerHandler


@pytest.mark.asyncio
async def test_synthesis_sends_exact_symbol_and_preserves_input_score_contract():
    calls = []
    payload = {
        "symbol": "600519.SH",
        "summary": "四维评分 30/30/25/15；保持来源。",
        "factor_observations": [],
        "missing_data": ["两融"],
        "risk_flags": ["行情不是实时"],
        "confidence": 0.5,
        "not_trade_recommendation": True,
    }

    async def invoke_model(**kwargs):
        calls.append(kwargs)
        return RouterInvokeResponse(
            role="research_synthesizer", provider="qwen", model="test-model",
            content=json.dumps(payload), latency_ms=1,
        ), "call-test"

    result = await ResearchSynthesizerHandler().run(
        request=RouterInvokeRequest(
            role="research_synthesizer", symbol="600519.SH",
            prompt="四维评分来源：30/30/25/15；两融缺失。",
        ),
        task_id="task-test", invoke_model=invoke_model,
    )

    # The API symbol previously never reached the model, allowing 600519
    # instead of 600519.SH and causing a real request to be rejected.
    assert "600519.SH" in calls[0]["prompt"]
    assert "30/30/25/15" in calls[0]["prompt"]
    assert "60%" not in calls[0]["system_prompt"]
    assert "40%" not in calls[0]["system_prompt"]
    assert result.response.validated is True
    assert result.result_payload["symbol"] == "600519.SH"
    assert result.result_payload["missing_data"] == ["两融"]

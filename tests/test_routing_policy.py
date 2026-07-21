from __future__ import annotations

from typing import Any

import pytest

from router.api.app import app, routing_policy as routing_policy_endpoint
from router.registry import RoleSpec
from router.schemas import BudgetTier, RiskLevel, RouterInvokeRequest, RouterInvokeResponse
from router.services.invocation import RouterInvocationService
from router.services.role_handler import RoleHandlerResult
from router.services.routing_policy import (
    ModelCallBudgetExceeded,
    RoutingPolicyService,
    RoutingPolicyViolation,
    routing_policy_catalog,
)


def enabled_role() -> RoleSpec:
    return RoleSpec(
        role="news_processor",
        display_name="News processor",
        provider="longcat",
        preferred_model="LongCat-2.0",
        task_type="bulk_text",
        description="Test role",
        enabled=True,
    )


def test_low_risk_economy_budget_has_small_deterministic_limits():
    decision = RoutingPolicyService().decide(
        role=enabled_role(),
        risk_level=RiskLevel.LOW,
        budget_tier=BudgetTier.ECONOMY,
    )

    assert decision.max_output_tokens_per_call == 512
    assert decision.max_physical_calls == 1
    assert decision.temperature_cap == 0.8
    assert decision.human_review_required is False
    assert decision.escalation_role is None


def test_high_risk_rejects_economy_budget_before_model_call():
    with pytest.raises(RoutingPolicyViolation, match="requires budget_tier>=standard"):
        RoutingPolicyService().decide(
            role=enabled_role(),
            risk_level=RiskLevel.HIGH,
            budget_tier=BudgetTier.ECONOMY,
        )


def test_high_risk_requires_review_and_requests_risk_controller():
    decision = RoutingPolicyService().decide(
        role=enabled_role(),
        risk_level=RiskLevel.HIGH,
        budget_tier=BudgetTier.STANDARD,
    )

    assert decision.temperature_cap == 0.2
    assert decision.human_review_required is True
    assert decision.escalation_role == "risk_controller"


def test_critical_risk_requires_premium_budget():
    with pytest.raises(RoutingPolicyViolation, match="requires budget_tier>=premium"):
        RoutingPolicyService().decide(
            role=enabled_role(),
            risk_level=RiskLevel.CRITICAL,
            budget_tier=BudgetTier.STANDARD,
        )


def test_policy_catalog_contains_only_public_limits():
    catalog = routing_policy_catalog()

    assert catalog["budgets"]["premium"]["max_physical_calls"] == 8
    assert catalog["risks"]["critical"]["human_review_required"] is True
    assert "api_key" not in str(catalog).lower()


def test_policy_catalog_api_is_public_and_stable():
    payload = routing_policy_endpoint()

    assert any(route.path == "/v1/routing/policy" for route in app.routes)
    assert payload["budgets"]["standard"]["max_output_tokens_per_call"] == 2048
    assert payload["risks"]["high"]["minimum_budget_tier"] == "standard"


class FakeAuditStore:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def create_task(self, **_: Any) -> str:
        return "task-1"

    def record_model_call(self, **_: Any) -> str:
        return "call-1"

    def record_agent_result(self, **payload: Any) -> str:
        self.results.append(payload)
        return "result-1"

    def update_task_status(self, *_: Any) -> None:
        return None


class FakeProvider:
    provider_name = "longcat"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **payload: Any) -> RouterInvokeResponse:
        self.calls.append(payload)
        return RouterInvokeResponse(
            role=payload["role"],
            provider=self.provider_name,
            model="LongCat-2.0",
            content='{"ok": true}',
            latency_ms=5,
        )


class OneCallHandler:
    async def run(self, *, request: RouterInvokeRequest, task_id: str, invoke_model: Any):
        response, _ = await invoke_model(
            prompt=request.prompt,
            system_prompt=request.system_prompt or "system",
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        return RoleHandlerResult(
            response=response.model_copy(update={"task_id": task_id}),
            result_payload={"ok": True},
            confidence=0.9,
        )


@pytest.mark.asyncio
async def test_invocation_applies_risk_caps_and_records_decision(monkeypatch: pytest.MonkeyPatch):
    provider = FakeProvider()
    service = RouterInvocationService()
    service.audit_store = FakeAuditStore()

    monkeypatch.setattr("router.services.invocation.get_role", lambda _: enabled_role())
    monkeypatch.setattr("router.services.invocation.get_model_provider", lambda _: provider)
    monkeypatch.setattr("router.services.invocation.get_role_handler", lambda _: OneCallHandler())

    response = await service.invoke(
        RouterInvokeRequest(
            role="news_processor",
            prompt="test",
            temperature=1.5,
            max_tokens=8192,
            risk_level=RiskLevel.HIGH,
            budget_tier=BudgetTier.STANDARD,
        )
    )

    assert provider.calls[0]["temperature"] == 0.2
    assert provider.calls[0]["max_tokens"] == 2048
    assert response.routing is not None
    assert response.routing.human_review_required is True
    assert service.audit_store.results[0]["result_payload"]["routing"]["risk_level"] == "high"


class TwoCallHandler:
    async def run(self, *, request: RouterInvokeRequest, task_id: str, invoke_model: Any):
        await invoke_model(
            prompt=request.prompt,
            system_prompt="system",
            temperature=0,
            max_tokens=128,
        )
        await invoke_model(
            prompt="second",
            system_prompt="system",
            temperature=0,
            max_tokens=128,
        )
        raise AssertionError("second call should not execute")


@pytest.mark.asyncio
async def test_economy_tier_blocks_second_physical_call(monkeypatch: pytest.MonkeyPatch):
    provider = FakeProvider()
    service = RouterInvocationService()
    service.audit_store = FakeAuditStore()

    monkeypatch.setattr("router.services.invocation.get_role", lambda _: enabled_role())
    monkeypatch.setattr("router.services.invocation.get_model_provider", lambda _: provider)
    monkeypatch.setattr("router.services.invocation.get_role_handler", lambda _: TwoCallHandler())

    with pytest.raises(ModelCallBudgetExceeded):
        await service.invoke(
            RouterInvokeRequest(
                role="news_processor",
                prompt="test",
                risk_level=RiskLevel.LOW,
                budget_tier=BudgetTier.ECONOMY,
            )
        )

    assert len(provider.calls) == 1

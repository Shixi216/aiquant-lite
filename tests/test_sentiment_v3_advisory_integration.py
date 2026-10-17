"""Tests for Sentiment V3 Advisory sidecar integration"""
from __future__ import annotations


from trading.decision_support.action_resolver import (
    DecisionActionResolver, ResolverInput, VetoResult,
)
from trading.decision_support.action import Action
from trading.decision_support.sentiment_v3_advisor import (
    SentimentAdvisoryResult, SentimentV3Advisor,
)
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.portfolio_context import PortfolioContext
from trading.decision_support.task_context import TaskContext
from trading.decision_support.permission_service import PermissionService


def test_advisor_safety_invariants():
    advisor = SentimentV3Advisor()
    res = advisor.evaluate(
        dragon_tiger_net_amount=100.0,
        stock_turnover=0.1,
        gap_and_follow_through=0.05,
        cross_section_medians={"dragon_tiger_net_amount": 50.0, "stock_turnover": 0.05, "gap_and_follow_through": 0.02}
    )
    assert res.formal_weight == 0
    assert res.research_only is True
    assert res.creates_trade is False


def test_advisor_unavailable_returns_safe_default():
    advisor = SentimentV3Advisor()
    res = advisor.evaluate()
    assert res.risk_reminder is False
    assert res.data_available is False
    assert res.confidence_penalty == 0.0


def test_risk_reminder_all_conditions_met():
    advisor = SentimentV3Advisor()
    # dragon tiger net < median, turnover > median, gap < median
    res = advisor.evaluate(
        dragon_tiger_net_amount=10.0,
        stock_turnover=0.1,
        gap_and_follow_through=0.01,
        cross_section_medians={"dragon_tiger_net_amount": 50.0, "stock_turnover": 0.05, "gap_and_follow_through": 0.05}
    )
    assert res.risk_reminder is True
    assert res.data_available is True
    assert res.confidence_penalty == 0.15


def test_risk_reminder_partial_conditions():
    advisor = SentimentV3Advisor()
    # turnover < median (not met)
    res = advisor.evaluate(
        dragon_tiger_net_amount=10.0,
        stock_turnover=0.01,
        gap_and_follow_through=0.01,
        cross_section_medians={"dragon_tiger_net_amount": 50.0, "stock_turnover": 0.05, "gap_and_follow_through": 0.05}
    )
    assert res.risk_reminder is False


def test_risk_reminder_no_dragon_tiger_data():
    advisor = SentimentV3Advisor()
    res = advisor.evaluate(
        stock_turnover=0.1,
        gap_and_follow_through=0.01,
        cross_section_medians={"stock_turnover": 0.05, "gap_and_follow_through": 0.05}
    )
    assert res.risk_reminder is True


def test_risk_reminder_missing_turnover():
    advisor = SentimentV3Advisor()
    res = advisor.evaluate(
        dragon_tiger_net_amount=10.0,
        gap_and_follow_through=0.01,
        cross_section_medians={"dragon_tiger_net_amount": 50.0, "gap_and_follow_through": 0.05}
    )
    assert res.risk_reminder is False
    assert res.data_available is False


def test_risk_reminder_missing_gap():
    advisor = SentimentV3Advisor()
    res = advisor.evaluate(
        dragon_tiger_net_amount=10.0,
        stock_turnover=0.1,
        cross_section_medians={"dragon_tiger_net_amount": 50.0, "stock_turnover": 0.05}
    )
    assert res.risk_reminder is False
    assert res.data_available is False


def test_resolver_buy_downgrade_on_risk_reminder():
    resolver = DecisionActionResolver()
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.5,
        confidence=0.8,
        sentiment_risk_reminder=True
    )
    out = resolver.resolve(inp)
    assert out.action == Action.SMALL_BUY
    assert "SENTIMENT_V3_RISK_REMINDER_DOWNGRADE" in out.action_reason_codes


def test_resolver_strong_buy_downgrade_on_risk_reminder():
    resolver = DecisionActionResolver()
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.8,
        confidence=0.9,
        sentiment_risk_reminder=True
    )
    out = resolver.resolve(inp)
    assert out.action == Action.SMALL_BUY


def test_resolver_confidence_reduced_on_risk_reminder():
    resolver = DecisionActionResolver()
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.8,
        confidence=0.9,
        sentiment_risk_reminder=True
    )
    out = resolver.resolve(inp)
    assert abs(out.confidence - 0.75) < 1e-5


def test_resolver_hold_not_affected_by_risk_reminder():
    resolver = DecisionActionResolver()
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.3,
        confidence=0.8,
        has_position=True,
        sentiment_risk_reminder=True
    )
    out = resolver.resolve(inp)
    assert out.action == Action.HOLD


def test_resolver_veto_overrides_risk_reminder():
    resolver = DecisionActionResolver()
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.8,
        confidence=0.9,
        veto=VetoResult(veto_triggered=True, veto_type="TEST"),
        sentiment_risk_reminder=True
    )
    out = resolver.resolve(inp)
    assert out.action == Action.AVOID
    assert out.veto_override is True


def test_resolver_no_risk_reminder_no_change():
    resolver = DecisionActionResolver()
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.8,
        confidence=0.9,
        sentiment_risk_reminder=False
    )
    out = resolver.resolve(inp)
    assert out.action == Action.STRONG_BUY
    assert abs(out.confidence - 0.9) < 1e-5


class MockPermissionService(PermissionService):
    def can_live_trade(self, user_id):
        return False

def test_decision_engine_accepts_advisory():
    engine = DecisionEngine(permission_service=MockPermissionService())
    inp = DecisionInput(
        task=TaskContext(local_user_id="user1", external_user_id="user1", symbol="000001", snapshot_id="snap1"),
        formal_score=0.8,
        technical_score=0.0,
        fundamental_score=0.0,
        point_in_time_ok=True,
        account_context=PortfolioContext(local_user_id="user1", total_assets=10000, available_cash=10000),
        confidence=0.9,
        sentiment_v3_advisory=SentimentAdvisoryResult(
            risk_reminder=True,
            confidence_penalty=0.15,
            composite_score=-0.5
        )
    )
    resp = engine.decide(inp)
    assert resp.action == Action.SMALL_BUY
    assert abs(resp.confidence - 0.75) < 1e-5


def test_decision_engine_no_advisory_default():
    engine = DecisionEngine(permission_service=MockPermissionService())
    inp = DecisionInput(
        task=TaskContext(local_user_id="user1", external_user_id="user1", symbol="000001", snapshot_id="snap1"),
        formal_score=0.8,
        technical_score=0.0,
        fundamental_score=0.0,
        point_in_time_ok=True,
        account_context=PortfolioContext(local_user_id="user1", total_assets=10000, available_cash=10000),
        confidence=0.9,
    )
    resp = engine.decide(inp)
    assert resp.action == Action.STRONG_BUY


def test_composite_score_calculation():
    advisor = SentimentV3Advisor()
    res = advisor.evaluate(
        dragon_tiger_net_amount=0.5,
        stock_turnover=0.3,
        gap_and_follow_through=0.2,
        cross_section_medians={"dragon_tiger_net_amount": 0.1, "stock_turnover": 0.1, "gap_and_follow_through": 0.1}
    )
    expected_composite = (0.5 - 0.3 - 0.2) / 3.0
    assert abs(res.composite_score - expected_composite) < 1e-5


def test_formal_score_unchanged_with_advisory():
    engine = DecisionEngine(permission_service=MockPermissionService())
    inp = DecisionInput(
        task=TaskContext(local_user_id="user1", external_user_id="user1", symbol="000001", snapshot_id="snap1"),
        formal_score=0.8,
        technical_score=0.0,
        fundamental_score=0.0,
        point_in_time_ok=True,
        account_context=PortfolioContext(local_user_id="user1", total_assets=10000, available_cash=10000),
        confidence=0.9,
        sentiment_v3_advisory=SentimentAdvisoryResult(risk_reminder=True)
    )
    resp = engine.decide(inp)
    assert resp.formal_score == 0.8

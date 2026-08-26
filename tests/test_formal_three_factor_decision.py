"""Tests for 3-factor Formal Scoring & Risk Reminder Safety Net Integration (Phase S4B)"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from trading.decision_support.action import Action
from trading.decision_support.action_resolver import (
    DecisionActionResolver,
    ResolverInput,
)
from trading.research.orchestration.decision import formal_result
from trading.research.orchestration.models import (
    FORMAL_STRATEGY_VERSION,
    FORMAL_THREE_FACTOR_STRATEGY_VERSION,
)
from trading.research.orchestration.schemas import DecisionShadowRequest
from trading.schemas import Action as OrchestrationAction

TZ = timezone(timedelta(hours=8))


def test_formal_result_two_factor_fallback_when_sentiment_none():
    req = DecisionShadowRequest(
        symbol="000001",
        data_cutoff=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
        technical_score=0.8,
        technical_confidence=0.9,
        fundamental_score=0.4,
        fundamental_confidence=0.8,
        sentiment_score=None,
    )
    res = formal_result(req)
    assert res.strategy_version == FORMAL_STRATEGY_VERSION
    assert res.formal_weights == {"TECHNICAL": 0.6, "FUNDAMENTAL": 0.4}
    expected_score = 0.8 * 0.6 + 0.4 * 0.4  # 0.64
    assert abs(res.score - expected_score) < 1e-4
    assert res.proposal_action == OrchestrationAction.BUY


def test_formal_result_three_factor_scoring():
    req = DecisionShadowRequest(
        symbol="000001",
        data_cutoff=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
        technical_score=0.8,
        technical_confidence=0.9,
        fundamental_score=0.4,
        fundamental_confidence=0.8,
        sentiment_score=0.6,
        sentiment_confidence=0.7,
    )
    res = formal_result(req)
    assert res.strategy_version == FORMAL_THREE_FACTOR_STRATEGY_VERSION
    assert res.formal_weights == {
        "TECHNICAL": 0.50,
        "FUNDAMENTAL": 0.35,
        "SENTIMENT": 0.15,
    }
    expected_score = 0.8 * 0.50 + 0.4 * 0.35 + 0.6 * 0.15  # 0.40 + 0.14 + 0.09 = 0.63
    assert abs(res.score - expected_score) < 1e-4
    assert res.proposal_action == OrchestrationAction.BUY


def test_formal_result_with_custom_dynamic_weights():
    req = DecisionShadowRequest(
        symbol="000001",
        data_cutoff=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
        technical_score=0.8,
        technical_confidence=0.9,
        fundamental_score=0.4,
        fundamental_confidence=0.8,
        sentiment_score=0.6,
        sentiment_confidence=0.7,
        custom_formal_weights={
            "technical": 0.45,
            "fundamental": 0.30,
            "sentiment": 0.25,
        },
    )
    res = formal_result(req)
    assert res.strategy_version == FORMAL_THREE_FACTOR_STRATEGY_VERSION
    assert res.formal_weights == {
        "TECHNICAL": 0.45,
        "FUNDAMENTAL": 0.30,
        "SENTIMENT": 0.25,
    }
    expected_score = 0.8 * 0.45 + 0.4 * 0.30 + 0.6 * 0.25  # 0.36 + 0.12 + 0.15 = 0.63
    assert abs(res.score - expected_score) < 1e-4


def test_hard_veto_overrides_three_factor_score():
    req = DecisionShadowRequest(
        symbol="000001",
        data_cutoff=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
        technical_score=0.9,
        technical_confidence=0.9,
        fundamental_score=0.9,
        fundamental_confidence=0.9,
        sentiment_score=0.9,
        hard_veto=True,
    )
    res = formal_result(req)
    assert res.proposal_action == OrchestrationAction.BUY
    assert res.final_action == OrchestrationAction.VETO


def test_three_factor_with_risk_reminder_safety_net():
    # 模拟 3 因子高分但命中 S4A 风险提醒（安全网踩刹车）
    resolver = DecisionActionResolver()
    
    # 假设 3 因子计算出的 formal_score 为 0.65（本应 STRONG_BUY）
    # 但触发了情绪风险提醒（机构净流出 + 高换手 + 缺口不跟随）
    inp = ResolverInput(
        point_in_time_ok=True,
        formal_score=0.65,
        confidence=0.85,
        sentiment_risk_reminder=True,
    )
    out = resolver.resolve(inp)
    
    # 安全网必须生效：动作从 STRONG_BUY/BUY 降级至 SMALL_BUY，置信度扣减 0.15
    assert out.action == Action.SMALL_BUY
    assert abs(out.confidence - 0.70) < 1e-4
    assert "SENTIMENT_V3_RISK_REMINDER_DOWNGRADE" in out.action_reason_codes
    assert "SENTIMENT_V3_RISK_REMINDER_CONFIDENCE_PENALTY" in out.action_reason_codes

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
)
from trading.research.orchestration.schemas import DecisionShadowRequest
from trading.schemas import Action as OrchestrationAction

TZ = timezone(timedelta(hours=8))


def test_request_scores_and_custom_weights_cannot_replace_four_dimensional_snapshot(monkeypatch):
    monkeypatch.setattr(
        "trading.decision_support.formal_score_resolver.FormalScoreResolver.read_cross_section",
        lambda self, symbol, **kwargs: (0.31, None),
    )
    for overrides in ({}, {"sentiment_score": 0.9}, {"custom_formal_weights": {"technical": 1.0}}):
        req = DecisionShadowRequest(
            symbol="000001", data_cutoff=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
            technical_score=-1, technical_confidence=0.9,
            fundamental_score=-1, fundamental_confidence=0.8, **overrides,
        )
        res = formal_result(req)
        assert res.strategy_version == FORMAL_STRATEGY_VERSION
        assert res.score == 0.31
        assert res.score_source == "cross_section_4d"
        assert res.formal_weights == {"TECHNICAL": .30, "FUNDAMENTAL": .30, "SENTIMENT": .25, "MARGIN": .15}
        assert res.proposal_action == OrchestrationAction.BUY


def test_no_four_dimensional_snapshot_does_not_fall_back_to_two_or_three_factors(monkeypatch):
    monkeypatch.setattr(
        "trading.decision_support.formal_score_resolver.FormalScoreResolver.read_cross_section",
        lambda self, symbol, **kwargs: None,
    )
    req = DecisionShadowRequest(
        symbol="000001", data_cutoff=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
        technical_score=1, technical_confidence=1,
        fundamental_score=1, fundamental_confidence=1, sentiment_score=1,
    )
    res = formal_result(req)
    assert res.status == "UNAVAILABLE"
    assert res.score == 0
    assert res.confidence == 0
    assert res.proposal_action == OrchestrationAction.HOLD
    assert formal_result(req.model_copy(update={"hard_veto": True})).final_action == OrchestrationAction.VETO


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

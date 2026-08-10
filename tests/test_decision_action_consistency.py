"""正式动作与执行状态一致性测试

核心验证：
1. DecisionEngine.decide() 是唯一正式决策来源
2. formal_score=0.656 必须输出 BUY，不是 WAIT
3. 报告层不得改写正式动作
4. VETO优先级高于一切价格区间
5. 优选建仓区间计算正确
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from trading.decision_support.action import Action
from trading.decision_support.conversation_formatter import (
    assert_action_consistency,
    format_decision_response,
)
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.task_context import TaskContext
from trading.decision_support.trade_plan_repository import TradePlanRepository

TZ = timezone(timedelta(hours=8))


def make_bars():
    from trading.decision_support.price_zones import Bar
    bars = []
    base = datetime(2026, 7, 1, tzinfo=TZ)
    prices = [
        (15.92, 16.57, 15.75, 16.46), (16.41, 16.91, 16.29, 16.49),
        (16.60, 17.27, 16.50, 16.89), (16.66, 17.40, 16.66, 17.16),
        (17.25, 18.21, 16.95, 17.96), (18.20, 19.67, 17.85, 19.41),
        (19.08, 20.30, 18.98, 19.63), (19.38, 19.70, 18.93, 19.38),
        (19.40, 20.30, 19.19, 20.00), (19.86, 21.00, 19.86, 20.52),
        (20.01, 20.79, 20.01, 20.52), (20.26, 22.42, 20.25, 22.11),
        (22.55, 23.11, 21.47, 21.61), (21.17, 22.00, 21.03, 21.47),
        (21.33, 21.73, 20.89, 21.42), (21.07, 21.63, 20.80, 21.51),
        (21.29, 21.40, 20.80, 21.01), (20.81, 21.10, 20.43, 20.43),
        (20.58, 21.45, 20.11, 21.32), (21.55, 21.76, 21.03, 21.53),
        (21.39, 23.29, 21.30, 22.78), (22.51, 23.76, 22.31, 22.65),
        (21.36, 23.69, 21.36, 23.03), (23.00, 23.09, 22.30, 22.42),
        (22.38, 22.65, 22.00, 22.28), (22.24, 22.51, 21.88, 22.49),
        (22.35, 22.48, 21.62, 22.18),
    ]
    for i, (o, h, l, c) in enumerate(prices):
        bars.append(Bar(trade_date=base + timedelta(days=i),
                        open=o, high=h, low=l, close=c, volume=100000))
    return bars


@pytest.fixture
def temp_db(tmp_path):
    db = tmp_path / "test.duckdb"
    db.touch()
    return str(db)


def _input(temp_db, price=22.18, score=0.656, conf=0.67, veto=None, bars=None):
    ctx = TaskContext(local_user_id="test", external_user_id="wx",
                      symbol="600882.SH", snapshot_id="s",
                      data_cutoff=datetime.now(tz=TZ))
    return DecisionInput(
        task=ctx, formal_score=score, technical_score=0.98,
        fundamental_score=0.17, sentiment_score=-0.07,
        policy_score=0.30, capital_score=-0.15, confidence=conf,
        data_status=DataStatus.FRESH, veto=veto,
        current_price=price, bars=bars or make_bars(),
        coverage_ratio=1.0,
    )


# ═══════════════════════════════════════════════
# 核心断言：formal_score=0.656 必须输出 BUY
# ═══════════════════════════════════════════════
class TestCoreAssertion:
    def test_0656_must_be_buy(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        assert resp.action == Action.BUY, f"0.656应输出BUY，实际{resp.action}"
        assert resp.action_zh == "建仓"
        assert resp.allow_new_position is True

    def test_0656_not_wait(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        assert resp.action != Action.WAIT

    def test_low_score_is_wait(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db, score=0.30))
        assert resp.action == Action.WAIT
        assert resp.execution_status == "等待确认"


# ═══════════════════════════════════════════════
# 一致性断言
# ═══════════════════════════════════════════════
class TestConsistency:
    def test_action_matches_self(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        assert_action_consistency(resp.action_zh, resp.action_zh, "DecisionEngine")

    def test_report_cannot_override(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        with pytest.raises(AssertionError, match="不一致"):
            assert_action_consistency("等待", resp.action_zh, "stock_report")

    def test_output_has_fields(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        output = format_decision_response(resp)
        assert "正式动作" in output
        assert "执行状态" in output
        assert "首批仓位" in output
        assert "优选建仓区间" in output

    def test_dict_has_fields(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        d = resp.to_dict()
        assert "execution_status" in d
        assert "preferred_zone" in d["price_zones"]


# ═══════════════════════════════════════════════
# 优选区间计算
# ═══════════════════════════════════════════════
class TestPreferredZone:
    def test_calculation(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        pz = resp.price_zones
        assert pz.preferred_zone and pz.entry_zone
        assert pz.preferred_zone[0] == pz.entry_zone[0]
        width = pz.entry_zone[1] - pz.entry_zone[0]
        expected = pz.entry_zone[0] + 0.40 * width
        assert abs(pz.preferred_zone[1] - expected) < 0.01

    def test_empty_when_not_buy(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db, score=0.30))
        assert resp.price_zones.preferred_zone == []


# ═══════════════════════════════════════════════
# VETO优先级（不依赖动态区间）
# ═══════════════════════════════════════════════
class TestVETOPriority:
    def test_veto_over一切(self, temp_db):
        """VETO触发 → 已失效，无论评分多高"""
        from trading.decision_support.action_resolver import VetoResult
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        veto = VetoResult(veto_triggered=True, veto_type="DELISTING_RISK")
        resp = engine.decide(_input(temp_db, score=0.90, conf=0.95, veto=veto))
        assert resp.action == Action.AVOID
        assert resp.execution_status == "已失效"

    def test_no_veto_normal_flow(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        assert resp.veto_triggered is False
        assert resp.execution_status != "已失效"


# ═══════════════════════════════════════════════
# 执行状态（动态区间场景）
# ═══════════════════════════════════════════════
class TestExecutionStatus:
    def test_buy_has_valid_status(self, temp_db):
        """BUY动作的执行状态必须是4种之一"""
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        valid = {"立即执行", "等待回调", "等待止跌确认", "禁止追高"}
        assert resp.execution_status in valid

    def test_target_position_and_batches(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(_input(temp_db))
        assert resp.target_position_ratio == 0.12
        assert resp.recommended_batches == 3

    def test_veto_gives_avoid(self, temp_db):
        from trading.decision_support.action_resolver import VetoResult
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        veto = VetoResult(veto_triggered=True, veto_type="RISK")
        resp = engine.decide(_input(temp_db, veto=veto))
        assert resp.action == Action.AVOID
        assert resp.execution_status == "已失效"

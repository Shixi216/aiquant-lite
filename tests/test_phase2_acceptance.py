"""阶段2 验收测试：TradePlan/价格区间/持仓/Paper/决策链

运行：python -m pytest tests/test_phase2_acceptance.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.decision_support.action import Action
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
from trading.decision_support.manual_position_manager import ManualPositionManager
from trading.decision_support.paper_trading import PaperTradingService
from trading.decision_support.price_zones import Bar, PriceZoneCalculator
from trading.decision_support.task_context import TaskContext
from trading.decision_support.trade_plan import TradePlan, TradePlanStatus
from trading.decision_support.trade_plan_repository import TradePlanRepository

TZ = timezone(timedelta(hours=8))


def make_bars(n=30, base=10.0) -> list[Bar]:
    """生成测试K线"""
    bars = []
    price = base
    for i in range(n):
        price = base + i * 0.1
        bars.append(Bar(
            trade_date=f"2026-07-{i%28+1:02d}",
            open=price - 0.05, high=price + 0.15, low=price - 0.15,
            close=price, volume=1_000_000 + i * 1000,
        ))
    return bars


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "test_phase2.duckdb"


# =====================================================================
# T1 TradePlan 测试
# =====================================================================
class TestTradePlan:
    def test_plan_30_fields(self, temp_db):
        """TradePlan 至少30字段"""
        plan = TradePlan(
            plan_id="plan_test1", local_user_id="u1", symbol="600509",
            stock_name="天富能源", action="BUY",
        )
        d = plan.to_dict()
        assert len(d) >= 30

    def test_repo_crud(self, temp_db):
        repo = TradePlanRepository(temp_db)
        plan = TradePlan(
            plan_id="plan_crud", local_user_id="u1", symbol="600509",
            stock_name="天富能源", action="BUY", confidence=0.7,
        )
        repo.create_plan(plan)
        got = repo.get_plan("plan_crud")
        assert got is not None
        assert got.local_user_id == "u1"
        assert got.action == "BUY"

    def test_repo_user_isolation(self, temp_db):
        repo = TradePlanRepository(temp_db)
        repo.create_plan(TradePlan(plan_id="p_a", local_user_id="user_a", symbol="A", action="BUY"))
        repo.create_plan(TradePlan(plan_id="p_b", local_user_id="user_b", symbol="B", action="HOLD"))
        plans_a = repo.list_plans("user_a")
        assert len(plans_a) == 1
        assert plans_a[0].symbol == "A"

    def test_status_lifecycle(self, temp_db):
        repo = TradePlanRepository(temp_db)
        plan = TradePlan(plan_id="p_lc", local_user_id="u1", symbol="X", action="BUY")
        repo.create_plan(plan)
        repo.update_status("p_lc", TradePlanStatus.CONFIRMED)
        got = repo.get_plan("p_lc")
        assert got.status == TradePlanStatus.CONFIRMED


# =====================================================================
# T2 价格区间测试
# =====================================================================
class TestPriceZones:
    def test_atr(self):
        bars = make_bars()
        atr = PriceZoneCalculator.compute_atr(bars)
        assert atr > 0

    def test_buy_zones(self):
        calc = PriceZoneCalculator()
        bars = make_bars()
        zones = calc.build_zones(bars, current_price=10.5, action=Action.BUY)
        assert len(zones["entry_zone"]) == 2
        assert zones["stop_loss_price"] is not None
        assert zones["stop_loss_condition"]
        assert zones["take_profit_zone"]
        assert zones["risk_reward_ratio"] > 0

    def test_stop_loss_not_fixed(self):
        """止损不固定5%/10%（基于ATR/前低）"""
        calc = PriceZoneCalculator()
        bars = make_bars()
        zones = calc.build_zones(bars, current_price=10.5, action=Action.BUY)
        stop = zones["stop_loss_price"]
        pct = (10.5 - stop) / 10.5
        # 基于ATR结构，幅度应在合理区间且非精确固定值
        assert 0.01 < pct < 0.20
        # 用不同波动率的K线验证止损不同（证明非固定值）
        volatile_bars = [Bar(trade_date=f"d{i}", open=10, high=10+i*0.5, low=10-i*0.5, close=10, volume=1e6) for i in range(1, 20)]
        zones2 = calc.build_zones(volatile_bars, current_price=10.5, action=Action.BUY)
        stop2 = zones2["stop_loss_price"]
        assert stop != stop2


# =====================================================================
# T3 人工持仓测试
# =====================================================================
class TestManualPosition:
    def test_buy_updates_holding(self, temp_db):
        mgr = ManualPositionManager(temp_db)
        mgr.record_trade("u1", "600509", "BUY", 1000, 10.0, stock_name="天富能源")
        h = mgr.get_holding("u1", "600509")
        assert h is not None
        assert h.quantity == 1000
        assert h.average_cost == 10.0

    def test_buy_avg_cost(self, temp_db):
        mgr = ManualPositionManager(temp_db)
        mgr.record_trade("u1", "600509", "BUY", 1000, 10.0)
        mgr.record_trade("u1", "600509", "BUY", 1000, 12.0)
        h = mgr.get_holding("u1", "600509")
        assert abs(h.average_cost - 11.0) < 1e-9

    def test_sell_reduces(self, temp_db):
        mgr = ManualPositionManager(temp_db)
        mgr.record_trade("u1", "600509", "BUY", 1000, 10.0)
        mgr.record_trade("u1", "600509", "SELL", 400, 11.0)
        h = mgr.get_holding("u1", "600509")
        assert h.quantity == 600

    def test_user_isolation(self, temp_db):
        mgr = ManualPositionManager(temp_db)
        mgr.record_trade("user_a", "600509", "BUY", 1000, 10.0)
        mgr.record_trade("user_b", "600509", "BUY", 500, 10.0)
        ha = mgr.get_holding("user_a", "600509")
        hb = mgr.get_holding("user_b", "600509")
        assert ha.quantity == 1000
        assert hb.quantity == 500

    def test_close_position(self, temp_db):
        mgr = ManualPositionManager(temp_db)
        mgr.record_trade("u1", "600509", "BUY", 1000, 10.0)
        mgr.close_position("u1", "600509")
        h = mgr.get_holding("u1", "600509")
        assert h.quantity == 0


# =====================================================================
# T4 Paper 交易测试
# =====================================================================
class TestPaperTrading:
    def test_buy(self, temp_db):
        svc = PaperTradingService(temp_db)
        order = svc.buy("u1", "600509", 1000, 10.0)
        assert order.side == "BUY"
        acct = svc.get_account("u1")
        assert acct.positions["600509"]["quantity"] == 1000
        assert acct.cash < 1_000_000  # 扣款

    def test_sell_pnl(self, temp_db):
        svc = PaperTradingService(temp_db)
        svc.buy("u1", "600509", 1000, 10.0)
        svc.sell("u1", "600509", 1000, 12.0)
        acct = svc.get_account("u1")
        assert acct.realized_pnl > 0  # 盈利
        assert "600509" not in acct.positions

    def test_insufficient_cash(self, temp_db):
        svc = PaperTradingService(temp_db)
        with pytest.raises(ValueError):
            svc.buy("u1", "600509", 10_000_000, 10.0)

    def test_user_isolation(self, temp_db):
        svc = PaperTradingService(temp_db)
        svc.buy("user_a", "600509", 1000, 10.0)
        svc.buy("user_b", "600509", 2000, 10.0)
        a = svc.get_account("user_a")
        b = svc.get_account("user_b")
        assert a.positions["600509"]["quantity"] == 1000
        assert b.positions["600509"]["quantity"] == 2000

    def test_orders_recorded(self, temp_db):
        svc = PaperTradingService(temp_db)
        svc.buy("u1", "600509", 1000, 10.0)
        orders = svc.list_orders("u1")
        assert len(orders) == 1


# =====================================================================
# T5 决策链测试（DecisionEngine 端到端）
# =====================================================================
class TestDecisionEngine:
    def _engine_input(self, user="u1", symbol="600509", score=0.60, conf=0.70,
                      status=DataStatus.FRESH, veto=None):
        ctx = TaskContext(
            local_user_id=user, external_user_id="wx1", symbol=symbol,
            snapshot_id="snap1", data_cutoff=datetime.now(tz=TZ),
        )
        return DecisionInput(
            task=ctx, formal_score=score, technical_score=0.7,
            fundamental_score=0.5, sentiment_score=0.1, policy_score=0.2,
            capital_score=0.3, confidence=conf, data_status=status,
            veto=veto, current_price=10.5, bars=make_bars(),
            coverage_ratio=1.0, major_risks=["测试风险"],
        )

    def test_full_decision(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(self._engine_input())
        assert resp.action == Action.BUY
        assert resp.action_zh == "建仓"
        assert resp.target_position_ratio > 0
        assert resp.price_zones.stop_loss_price is not None

    def test_veto_blocks(self, temp_db):
        from trading.decision_support.action_resolver import VetoResult
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(self._engine_input(
            score=0.90, conf=0.95,
            veto=VetoResult(veto_triggered=True, veto_type="DELISTING_RISK")))
        assert resp.veto_triggered
        assert resp.action == Action.AVOID
        assert not resp.allow_new_position

    def test_stale_no_buy(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(self._engine_input(status=DataStatus.STALE, score=0.8))
        assert not resp.allow_new_position

    def test_create_trade_plan(self, temp_db):
        engine = DecisionEngine(plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(self._engine_input())
        plan = engine.create_trade_plan(resp, stock_name="天富能源")
        assert plan.status == TradePlanStatus.PENDING_CONFIRMATION
        assert plan.entry_zone
        assert plan.stop_loss_price is not None
        got = engine.plan_repo.get_plan(plan.plan_id)
        assert got is not None

    def test_no_permission_user(self, temp_db):
        """分析权限：LEVEL2 默认允许（任何有效调用者可分析）"""
        from trading.decision_support.user_repository import UserRepository
        from trading.decision_support.permission_service import PermissionService
        repo = UserRepository(temp_db)
        perms = PermissionService(repo)
        engine = DecisionEngine(permission_service=perms, plan_repo=TradePlanRepository(temp_db))
        resp = engine.decide(self._engine_input(user="ghost_user"))
        # 分析是只读的，默认允许；ghost_user 无持仓 → 正常出动作
        assert resp.action in (Action.BUY, Action.SMALL_BUY)

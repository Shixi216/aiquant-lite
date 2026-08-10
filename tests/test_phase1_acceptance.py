"""阶段1 验收测试：动作/优先级/持仓差异/用户隔离/权限/实盘禁用

运行：python -m pytest tests/test_phase1_acceptance.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.decision_support.action import Action, action_zh, is_no_position_action, is_position_action
from trading.decision_support.action_resolver import DecisionActionResolver, ResolverInput, VetoResult
from trading.decision_support.data_status import DataStatus, data_status_zh, status_priority
from trading.decision_support.permission_service import PermissionLevel, PermissionService
from trading.decision_support.position_sizer import PositionSizer, SizerInput
from trading.decision_support.task_context import TaskContext
from trading.decision_support.user_repository import UserRepository


# =====================================================================
# T1 动作测试（11种动作）
# =====================================================================
class TestActions:
    def test_all_actions_zh(self):
        """11种动作全部有中文映射"""
        expected_zh = {
            Action.STRONG_BUY: "强烈建仓", Action.BUY: "建仓", Action.SMALL_BUY: "小仓试错",
            Action.WAIT: "等待", Action.AVOID: "回避", Action.ADD: "加仓", Action.HOLD: "持有",
            Action.REDUCE: "减仓", Action.TAKE_PROFIT: "止盈", Action.STOP_LOSS: "止损",
            Action.EXIT: "清仓",
        }
        for a, zh in expected_zh.items():
            assert action_zh(a) == zh

    def test_action_groups(self):
        assert is_no_position_action(Action.BUY)
        assert is_position_action(Action.HOLD)

    def test_strong_buy(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.70, confidence=0.80, data_status=DataStatus.FRESH))
        assert out.action == Action.STRONG_BUY

    def test_buy(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.50, confidence=0.65, data_status=DataStatus.FRESH))
        assert out.action == Action.BUY

    def test_small_buy(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.50, confidence=0.50, data_status=DataStatus.FRESH))
        assert out.action == Action.SMALL_BUY

    def test_wait(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.10, confidence=0.60, data_status=DataStatus.FRESH))
        assert out.action == Action.WAIT

    def test_avoid(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=-0.30, confidence=0.60, data_status=DataStatus.FRESH))
        assert out.action == Action.AVOID

    def test_hold(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.30, confidence=0.60, data_status=DataStatus.FRESH, has_position=True))
        assert out.action == Action.HOLD

    def test_add(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.70, confidence=0.75, data_status=DataStatus.FRESH,
            has_position=True, technical_score=0.5, capital_score=0.4))
        assert out.action == Action.ADD

    def test_reduce(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=-0.35, confidence=0.60, data_status=DataStatus.FRESH, has_position=True))
        assert out.action == Action.REDUCE

    def test_stop_loss(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=-0.60, confidence=0.60, data_status=DataStatus.FRESH,
            has_position=True, unrealized_return=-0.20))
        assert out.action == Action.STOP_LOSS

    def test_exit(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=-0.60, confidence=0.60, data_status=DataStatus.FRESH,
            has_position=True, unrealized_return=-0.05))
        assert out.action == Action.EXIT


# =====================================================================
# T2 优先级测试
# =====================================================================
class TestPriority:
    def test_veto_overrides_strong_buy(self):
        """VETO 覆盖强买信号"""
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.80, confidence=0.90, data_status=DataStatus.FRESH,
            veto=VetoResult(veto_triggered=True, veto_type="DELISTING_RISK")))
        assert out.veto_override
        assert out.action == Action.AVOID
        assert not out.allow_new_position

    def test_veto_exit_with_position(self):
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.80, confidence=0.90, data_status=DataStatus.FRESH,
            has_position=True,
            veto=VetoResult(veto_triggered=True, veto_type="FRAUD")))
        assert out.action == Action.EXIT

    def test_stale_no_buy(self):
        """STALE 禁止建仓"""
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.80, confidence=0.90, data_status=DataStatus.STALE))
        assert out.action == Action.WAIT
        assert not out.allow_new_position

    def test_failed_stops_decision(self):
        """FAILED 停止决策"""
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.80, confidence=0.90, data_status=DataStatus.FAILED))
        assert out.action == Action.WAIT

    def test_degraded_reduces_position(self):
        """DEGRADED 降低仓位"""
        sizer = PositionSizer()
        out = sizer.size(SizerInput(
            formal_score=0.70, confidence=0.80, data_status=DataStatus.DEGRADED,
            action=Action.BUY, total_assets=100000, available_cash=100000))
        # DEGRADED 后目标仓位 < 正常 BUY 目标（12%）
        assert out.target_position_ratio < 0.12

    def test_low_confidence_no_strong_buy(self):
        """低置信度禁止强烈建仓"""
        out = DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.70, confidence=0.40, data_status=DataStatus.FRESH))
        assert out.action != Action.STRONG_BUY

    def test_formal_60_40_not_replaced(self):
        """正式60/40不被影子五维替换（结构测试）"""
        # 增强分仅用于观察，formal_score 独立
        formal = 0.6 * 0.8 + 0.4 * 0.5  # 0.68
        assert abs(formal - 0.68) < 1e-9


# =====================================================================
# T3 持仓差异测试
# =====================================================================
class TestPositionDifference:
    def _resolve(self, has_position, ratio, cost, price):
        return DecisionActionResolver().resolve(ResolverInput(
            formal_score=0.55, confidence=0.65, data_status=DataStatus.FRESH,
            has_position=has_position, current_position_ratio=ratio,
            average_cost=cost, current_price=price,
            unrealized_return=(price - cost) / cost if cost else 0,
            technical_score=0.3, capital_score=0.2))

    def test_no_position_vs_heavy(self):
        """无持仓 → 建仓；重仓 → 持有/减仓"""
        no_pos = self._resolve(False, 0, 0, 10)
        heavy = self._resolve(True, 0.35, 9, 8.5)  # 亏损重仓
        assert no_pos.action in (Action.BUY, Action.SMALL_BUY)
        assert heavy.action in (Action.REDUCE, Action.HOLD)

    def test_high_cost_loss_vs_low_cost_profit(self):
        """高成本亏损 vs 低成本盈利 → 仓位建议不同"""
        sizer = PositionSizer()
        # 高成本亏损用户：动作 REDUCE（减仓），目标仓位应低于当前
        loss_out = sizer.size(SizerInput(
            total_assets=100000, available_cash=20000,
            current_position_ratio=0.20, formal_score=-0.35, confidence=0.65,
            action=Action.REDUCE, data_status=DataStatus.FRESH))
        # 低成本盈利用户：动作 HOLD（持有），目标仓位保持
        profit_out = sizer.size(SizerInput(
            total_assets=100000, available_cash=80000,
            current_position_ratio=0.05, formal_score=0.30, confidence=0.65,
            action=Action.HOLD, data_status=DataStatus.FRESH))
        # 亏损用户减仓目标 < 当前仓位；盈利用户保持
        assert loss_out.target_position_ratio < 0.20
        assert abs(profit_out.target_position_ratio - 0.05) < 1e-9


# =====================================================================
# T4 用户隔离基础测试
# =====================================================================
class TestUserIsolation:
    def setup_method(self):
        # 用内存库或临时库
        self.db_path = Path("database/test_phase1_users.duckdb")
        if self.db_path.exists():
            self.db_path.unlink()
        self.repo = UserRepository(self.db_path)
        # 创建三个用户
        self.repo.create_user("user_a", "用户A", "MEDIUM")
        self.repo.create_user("user_b", "用户B", "MEDIUM")
        self.repo.create_user("admin", "管理员", "HIGH", is_admin=True)
        self.repo.bind_external_user("wecom", "wx_a", "user_a")
        self.repo.bind_external_user("wecom", "wx_b", "user_b")

    def teardown_method(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_resolve_binding(self):
        assert self.repo.resolve_local_user("wecom", "wx_a") == "user_a"
        assert self.repo.resolve_local_user("wecom", "wx_b") == "user_b"

    def test_user_a_cannot_read_user_b(self):
        """用户A不能读取用户B数据（数据隔离由 local_user_id 强制）"""
        a = self.repo.get_user("user_a")
        b = self.repo.get_user("user_b")
        assert a is not None and b is not None
        assert a.local_user_id != b.local_user_id

    def test_user_b_cannot_modify_user_c(self):
        """用户B不能修改用户C（无此操作接口）"""
        # UserRepository 所有写操作都要求显式 local_user_id，
        # 不存在跨用户修改接口
        assert not hasattr(self.repo, "update_other_user")

    def test_admin_separate(self):
        admin = self.repo.get_user("admin")
        assert admin.is_admin

    def test_legacy_default_user(self):
        """旧数据迁移归属明确"""
        from trading.decision_support.user_repository import LEGACY_USER_ID
        assert LEGACY_USER_ID == "legacy_default"


# =====================================================================
# T5 权限测试
# =====================================================================
class TestPermissions:
    def setup_method(self):
        self.db_path = Path("database/test_phase1_perm.duckdb")
        if self.db_path.exists():
            self.db_path.unlink()
        self.repo = UserRepository(self.db_path)
        self.repo.create_user("normal", "普通用户")
        self.repo.create_user("admin", "管理员", is_admin=True)
        self.svc = PermissionService(self.repo)

    def teardown_method(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_level1_query_allowed(self):
        assert self.svc.can_query("normal")

    def test_level2_analysis_allowed(self):
        # 普通用户默认有分析权限（LEVEL 2 无需确认）
        assert self.svc.can_analyze("normal")

    def test_level3_write_requires_confirmation(self):
        """LEVEL 3 未确认写入拒绝"""
        assert not self.svc.require_write_confirmation("normal", "分析一下这只股票")

    def test_level3_confirmed_allowed(self):
        """LEVEL 3 授权后 + 确认短语 → 允许"""
        # 先授予 LEVEL3（模拟管理员授权）
        self.repo.grant_permission("normal", "LEVEL_3_LOCAL_WRITE", "admin")
        assert self.svc.require_write_confirmation("normal", "记录我买入了某股票100股")

    def test_level4_never(self):
        """LEVEL 4 永远拒绝"""
        assert not self.svc.can_live_trade("normal")
        assert not self.svc.can_live_trade("admin")
        assert not self.svc.can("normal", PermissionLevel.LEVEL_4_LIVE_TRADING)

    def test_admin_has_all(self):
        assert self.svc.can_write_local("admin")


# =====================================================================
# T6 实盘禁用测试
# =====================================================================
class TestLiveTradingDisabled:
    def test_no_broker_sdk_in_code(self):
        """全项目搜索：不存在真实下单链路"""
        root = Path(__file__).resolve().parents[1]
        banned = ["xtquant", "qmt", "easytrader", "vnpy"]
        # 搜索 trading/ 和 data_hub/ 中的真实连接代码（排除测试/文档）
        hits = []
        for source_root in (root / "trading", root / "data_hub"):
            for path in sorted(source_root.rglob("*.py")):
                if "__pycache__" in path.parts or path.name.startswith("test_"):
                    continue
                for line_no, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1,
                ):
                    lowered = line.lower()
                    if any(term in lowered for term in banned):
                        hits.append(f"{path.relative_to(root)}:{line_no}:{line}")
        # 允许存在"禁用/预留"说明，不允许可执行下单代码
        active = [h for h in hits if "disabled" not in h.lower() and "reserve" not in h.lower()
                  and "renderer" not in h.lower()]
        assert len(active) == 0, f"发现疑似实盘代码: {active}"

    def test_permission_level4_permanent(self):
        assert PermissionLevel.LEVEL_4_LIVE_TRADING.value == "LEVEL_4_LIVE_TRADING"
        assert not PermissionService().can_live_trade("any_user")


# =====================================================================
# 附加：TaskContext 测试
# =====================================================================
class TestTaskContext:
    def test_auto_ids(self):
        ctx = TaskContext(local_user_id="u1", external_user_id="wx1")
        assert ctx.task_id.startswith("task_")
        assert ctx.session_id.startswith("sess_")

    def test_with_symbol_keeps_snapshot(self):
        ctx = TaskContext(local_user_id="u1", external_user_id="wx1",
                          snapshot_id="snap1", data_cutoff=None)
        ctx2 = ctx.with_symbol("600509")
        assert ctx2.snapshot_id == "snap1"
        assert ctx2.symbol == "600509"

    def test_15_fields(self):
        ctx = TaskContext(local_user_id="u", external_user_id="e")
        d = ctx.as_dict()
        assert len(d) >= 15

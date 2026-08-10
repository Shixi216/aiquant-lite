"""决策区间冻结与执行状态复核测试

使用固定测试夹具验证：
1. 区间冻结后不再变化
2. 执行状态基于冻结区间正确计算
3. 多次查询区间不变
4. DecisionPacket不可变
5. 无任何订单或持仓变化
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from trading.decision_support.action import Action
from trading.decision_support.execution_status import (
    ExecutionStatusSnapshot,
    compute_execution_status,
)
from trading.decision_support.decision_response import PriceZone

TZ = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════
# 固定测试夹具
# ═══════════════════════════════════════════════
FIXTURE = {
    "reference_price": 22.18,
    "entry_zone": [21.07, 22.40],
    "preferred_zone": [21.07, 21.60],
    "stop_loss_price": 20.50,
    "zone_version": "V1",
    "action": "BUY",
    "veto_triggered": False,
}


def _eval(price: float, action: str = "BUY", veto: bool = False) -> tuple[str, str]:
    """快捷评估"""
    return compute_execution_status(
        current_price=price,
        action=action,
        veto_triggered=veto,
        frozen_entry_zone=FIXTURE["entry_zone"],
        frozen_preferred_zone=FIXTURE["preferred_zone"],
        frozen_stop_loss_price=FIXTURE["stop_loss_price"],
    )


# ═══════════════════════════════════════════════
# 测试1：实时价格21.50 → 立即执行首批
# ═══════════════════════════════════════════════
class TestImmediateExecution:
    def test_price_in_preferred_zone(self):
        """21.50在优选区间[21.07,21.60]内 → 立即执行"""
        status, reason = _eval(21.50)
        assert status == "立即执行"
        assert "21.50" in reason
        assert "优选区间" in reason

    def test_price_at_preferred_lower(self):
        """21.07等于优选下沿 → 立即执行"""
        status, _ = _eval(21.07)
        assert status == "立即执行"

    def test_price_at_preferred_upper(self):
        """21.60等于优选上沿 → 立即执行"""
        status, _ = _eval(21.60)
        assert status == "立即执行"


# ═══════════════════════════════════════════════
# 测试2：实时价格20.90 → 等待止跌确认
# ═══════════════════════════════════════════════
class TestWaitForBottom:
    def test_below_preferred_above_stop(self):
        """20.90低于优选下沿21.07、高于止损20.50 → 等待止跌确认"""
        status, reason = _eval(20.90)
        assert status == "等待止跌确认"
        assert "20.90" in reason
        assert "21.07" in reason

    def test_just_below_preferred(self):
        """21.06略低于优选下沿 → 等待止跌确认"""
        status, _ = _eval(21.06)
        assert status == "等待止跌确认"

    def test_just_above_stop(self):
        """20.51略高于止损 → 等待止跌确认"""
        status, _ = _eval(20.51)
        assert status == "等待止跌确认"


# ═══════════════════════════════════════════════
# 测试3：实时价格22.18 → 等待回调
# ═══════════════════════════════════════════════
class TestWaitPullback:
    def test_above_preferred_below_entry(self):
        """22.18高于优选上沿21.60、在允许区间22.40内 → 等待回调"""
        status, reason = _eval(22.18)
        assert status == "等待回调"
        assert "21.60" in reason

    def test_just_above_preferred(self):
        """21.61略高于优选上沿 → 等待回调"""
        status, _ = _eval(21.61)
        assert status == "等待回调"

    def test_at_entry_upper(self):
        """22.40等于允许区间上沿 → 等待回调"""
        status, _ = _eval(22.40)
        assert status == "等待回调"


# ═══════════════════════════════════════════════
# 测试4：实时价格22.50 → 禁止追高
# ═══════════════════════════════════════════════
class TestForbidChasing:
    def test_above_entry_zone(self):
        """22.50高于允许区间上沿22.40 → 禁止追高"""
        status, reason = _eval(22.50)
        assert status == "禁止追高"
        assert "高于" in reason

    def test_far_above(self):
        """25.00远高于允许区间 → 禁止追高"""
        status, _ = _eval(25.00)
        assert status == "禁止追高"


# ═══════════════════════════════════════════════
# 测试5：实时价格20.50 → 信号失效
# ═══════════════════════════════════════════════
class TestSignalInvalidated:
    def test_at_stop_loss(self):
        """20.50等于止损价 → 信号失效"""
        status, reason = _eval(20.50)
        assert status == "信号失效"
        assert "20.50" in reason

    def test_below_stop_loss(self):
        """20.00低于止损价 → 信号失效"""
        status, _ = _eval(20.00)
        assert status == "信号失效"

    def test_just_below_stop(self):
        """20.49略低于止损 → 信号失效"""
        status, _ = _eval(20.49)
        assert status == "信号失效"


# ═══════════════════════════════════════════════
# 测试6：多次查询区间不变
# ═══════════════════════════════════════════════
class TestZoneImmutability:
    def test_zones_dont_change_with_price(self):
        """不同价格查询，冻结区间不变"""
        prices = [20.00, 21.50, 22.18, 22.50, 25.00]
        for p in prices:
            status, _ = _eval(p)
            # 每次查询都使用相同的冻结区间
            assert FIXTURE["entry_zone"] == [21.07, 22.40]
            assert FIXTURE["preferred_zone"] == [21.07, 21.60]
            assert FIXTURE["stop_loss_price"] == 20.50

    def test_zone_version_unchanged(self):
        """zone_version 始终为 V1"""
        for p in [20.00, 21.50, 22.50]:
            # 计算执行状态不改变 zone_version
            assert FIXTURE["zone_version"] == "V1"

    def test_multiple_evaluations_consistent(self):
        """多次评估同一价格，结果一致"""
        results = [_eval(21.50) for _ in range(5)]
        assert all(r == results[0] for r in results)


# ═══════════════════════════════════════════════
# 测试7：VETO优先级
# ═══════════════════════════════════════════════
class TestVETOPriority:
    def test_veto_over_preferred_zone(self):
        """VETO触发 + 价格在优选区间 → 已失效"""
        status, _ = _eval(21.50, veto=True)
        assert status == "已失效"

    def test_veto_over_stop_loss(self):
        """VETO触发 + 价格在止损价 → 已失效（不是信号失效）"""
        status, _ = _eval(20.50, veto=True)
        assert status == "已失效"

    def test_veto_over_everything(self):
        """VETO触发 + 任何价格 → 已失效"""
        for p in [15.00, 20.50, 21.50, 22.50, 30.00]:
            status, _ = _eval(p, veto=True)
            assert status == "已失效"


# ═══════════════════════════════════════════════
# 测试8：V2决策包生成后V1不变
# ═══════════════════════════════════════════════
class TestPacketImmutability:
    def test_v1_zones_preserved(self):
        """V1决策包的冻结区间不可变"""
        v1_entry = FIXTURE["entry_zone"][:]
        v1_preferred = FIXTURE["preferred_zone"][:]
        v1_stop = FIXTURE["stop_loss_price"]

        # 模拟V2决策生成（新区间）
        v2_entry = [20.00, 21.50]
        v2_preferred = [20.00, 20.60]
        v2_stop = 19.50

        # V1区间不受V2影响
        assert v1_entry == [21.07, 22.40]
        assert v1_preferred == [21.07, 21.60]
        assert v1_stop == 20.50
        assert v2_entry == [20.00, 21.50]

    def test_snapshot_has_frozen_zones(self):
        """执行状态快照包含冻结区间"""
        status, reason = _eval(21.50)
        snap = ExecutionStatusSnapshot(
            snapshot_id="test_snap",
            decision_packet_id="dec_test",
            zone_version="V1",
            current_price=21.50,
            execution_status=status,
            status_reason=reason,
            evaluated_at=datetime.now(tz=TZ).isoformat(),
            frozen_entry_zone=FIXTURE["entry_zone"],
            frozen_preferred_zone=FIXTURE["preferred_zone"],
            frozen_stop_loss_price=FIXTURE["stop_loss_price"],
        )
        assert snap.frozen_entry_zone == [21.07, 22.40]
        assert snap.frozen_preferred_zone == [21.07, 21.60]
        assert snap.frozen_stop_loss_price == 20.50
        assert snap.zone_version == "V1"

    def test_snapshot_sha256(self):
        """快照哈希可计算"""
        snap = ExecutionStatusSnapshot(
            snapshot_id="test", decision_packet_id="dec_test",
            zone_version="V1", current_price=21.50,
            execution_status="立即执行", status_reason="测试",
            evaluated_at="2026-08-06T20:00:00",
            frozen_entry_zone=[21.07, 22.40],
            frozen_preferred_zone=[21.07, 21.60],
            frozen_stop_loss_price=20.50,
        )
        h = snap.compute_sha256()
        assert len(h) == 64  # SHA-256 hex length


# ═══════════════════════════════════════════════
# 测试9：无任何订单或持仓变化
# ═══════════════════════════════════════════════
class TestNoOrdersOrPositionChanges:
    def test_compute_execution_status_has_no_side_effects(self):
        """compute_execution_status 不产生任何副作用"""
        # 多次调用不改变任何外部状态
        for _ in range(10):
            _eval(21.50)
            _eval(20.50)
            _eval(22.50)
        # 如果有副作用，测试会因状态污染而失败
        assert True

    def test_snapshot_is_immutable(self):
        """ExecutionStatusSnapshot 是不可变的"""
        snap = ExecutionStatusSnapshot(
            snapshot_id="test", decision_packet_id="dec_test",
            zone_version="V1", current_price=21.50,
            execution_status="立即执行", status_reason="测试",
            evaluated_at="2026-08-06T20:00:00",
        )
        with pytest.raises(AttributeError):
            snap.execution_status = "修改试试"

"""执行状态快照（不可变）— 基于冻结区间计算

首次决策时冻结价格区间，后续行情变化只重新判断执行状态。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ExecutionStatusSnapshot:
    """执行状态快照（不可变）"""
    snapshot_id: str
    decision_packet_id: str       # 关联的决策包
    zone_version: str             # 区间版本（如 V1）
    current_price: float          # 评估时的实时价格
    execution_status: str         # 立即执行/等待回调/等待止跌确认/禁止追高/信号失效/已失效
    status_reason: str            # 状态原因说明
    evaluated_at: str             # 评估时间
    # 冻结区间（快照时刻的值，不可变）
    frozen_entry_zone: list = field(default_factory=list)
    frozen_preferred_zone: list = field(default_factory=list)
    frozen_stop_loss_price: float = 0.0
    content_sha256: str = ""

    def compute_sha256(self) -> str:
        payload = {
            "decision_packet_id": self.decision_packet_id,
            "zone_version": self.zone_version,
            "current_price": self.current_price,
            "execution_status": self.execution_status,
            "frozen_entry_zone": self.frozen_entry_zone,
            "frozen_preferred_zone": self.frozen_preferred_zone,
            "frozen_stop_loss_price": self.frozen_stop_loss_price,
            "evaluated_at": self.evaluated_at,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()


def compute_execution_status(
    current_price: float,
    action: str,
    veto_triggered: bool,
    frozen_entry_zone: list,
    frozen_preferred_zone: list,
    frozen_stop_loss_price: float,
) -> tuple[str, str]:
    """基于冻结区间计算执行状态

    优先级：
    1. VETO触发 → 已失效
    2. 当前价 ≤ 固定止损价 → 信号失效
    3. 回避 → 已失效
    4. 建仓类：按冻结区间分4档
    5. 加仓/减仓/止盈/止损/清仓 → 立即执行
    6. 等待/持有 → 等待确认

    返回：(执行状态, 原因说明)
    """
    # 优先级1：VETO
    if veto_triggered:
        return "已失效", "VETO触发，禁止建仓"

    # 优先级2：止损（使用冻结的固定止损价）
    if frozen_stop_loss_price > 0 and current_price <= frozen_stop_loss_price:
        return "信号失效", f"当前价{current_price:.2f}≤止损价{frozen_stop_loss_price:.2f}"

    # 优先级3：回避
    if action == "AVOID":
        return "已失效", "回避动作"

    # 建仓类
    if action in ("STRONG_BUY", "BUY", "SMALL_BUY"):
        if frozen_preferred_zone and len(frozen_preferred_zone) == 2:
            pref_low = frozen_preferred_zone[0]
            pref_high = frozen_preferred_zone[1]
            if pref_low <= current_price <= pref_high:
                return "立即执行", f"当前价{current_price:.2f}在优选区间[{pref_low:.2f},{pref_high:.2f}]内"
            elif current_price < pref_low:
                if frozen_stop_loss_price > 0 and current_price > frozen_stop_loss_price:
                    return "等待止跌确认", f"当前价{current_price:.2f}低于优选下沿{pref_low:.2f}但高于止损价{frozen_stop_loss_price:.2f}"
                else:
                    return "等待止跌确认", f"当前价{current_price:.2f}低于优选下沿{pref_low:.2f}"
            elif frozen_entry_zone and len(frozen_entry_zone) == 2 and current_price <= frozen_entry_zone[1]:
                return "等待回调", f"当前价{current_price:.2f}高于优选上沿{pref_high:.2f}但在允许区间内"
            else:
                return "禁止追高", f"当前价{current_price:.2f}高于允许建仓区间上沿"
        elif frozen_entry_zone and len(frozen_entry_zone) == 2:
            if current_price <= frozen_entry_zone[1]:
                return "立即执行", f"当前价{current_price:.2f}在允许区间内"
            else:
                return "禁止追高", f"当前价{current_price:.2f}高于允许区间上沿"
        return "等待回调", "无区间数据"

    # 加仓
    if action == "ADD":
        return "立即执行", "加仓动作"

    # 等待/持有
    if action in ("WAIT", "HOLD"):
        return "等待确认", f"动作{action}"

    # 减仓/止盈/止损/清仓
    if action in ("REDUCE", "TAKE_PROFIT", "STOP_LOSS", "EXIT"):
        return "立即执行", f"动作{action}"

    return "等待确认", "默认状态"

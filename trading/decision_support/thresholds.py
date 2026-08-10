"""动作阈值配置（阶段1.7）— 集中配置、版本化、可审计

所有阈值不得散落在代码中。本文件为唯一配置源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

THRESHOLD_VERSION = "action-thresholds-v1"


@dataclass(frozen=True)
class ActionThresholds:
    version: str = THRESHOLD_VERSION

    # ---- 无持仓动作阈值 ----
    strong_buy_min: float = 0.65       # formal_score ≥ 0.65 → 强烈建仓/建仓
    strong_buy_conf: float = 0.70      # 且置信度 ≥ 0.70
    buy_min: float = 0.40              # 0.40~0.65 → 小仓试错/分批建仓
    buy_conf: float = 0.60             # 且置信度 ≥ 0.60
    wait_min: float = -0.20            # -0.20~0.40 → 等待
    avoid_max: float = -0.20           # < -0.20 → 回避

    # ---- 已持仓动作阈值 ----
    hold_add_min: float = 0.65         # ≥ 0.65 趋势资金确认 → 持有/加仓
    hold_min: float = 0.20             # 0.20~0.65 → 持有
    reduce_watch_max: float = 0.20     # -0.20~0.20 → 减仓观察/持有不新增
    reduce_min: float = -0.50          # -0.50~-0.20 → 减仓
    exit_max: float = -0.50            # ≤ -0.50 → 清仓/止损

    # ---- 低置信度限制 ----
    low_confidence: float = 0.55       # confidence < 0.55 时限制

    # ---- 数据状态限制 ----
    # FRESH: 正常 / DEGRADED: 降仓 / STALE: 停止推荐 / FAILED: 停止决策

    # ---- 风险等级影响 ----
    risk_high_max_position: float = 0.05   # 高风险股票最大仓位 5%

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

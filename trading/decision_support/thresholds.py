"""动作阈值配置 — 集中配置、版本化、可审计

所有阈值不得散落在代码中。本文件为唯一配置源。

v2（rank 分位数锚定）：四维横截面 composite_score 是 rank 分位数语义
（pct_rank*2-1，均值 0、对称分布），无持仓/持仓阈值锚定全市场分位数
（strong_buy = P90 = 全市场前 10%，buy = P75 = 前 25%）。
每个截面生成日由 scripts.compute_cross_section_snapshot.py 把当日分位数
写入 cross_section_thresholds 表；本配置的数值仅为表读不到时的回退常量。
reduce_min / exit_max 是绝对亏损保护线，与分布分位数无关，保持绝对值。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

THRESHOLD_VERSION = "action-thresholds-v2"


@dataclass(frozen=True)
class ActionThresholdsV2:
    version: str = THRESHOLD_VERSION

    # ---- 无持仓动作阈值（rank 分位数锚定；回退常量 = 2026-09-01 实测 P90/P75）----
    strong_buy_min: float = 0.42       # formal_score ≥ P90 → 强烈建仓（全市场前 ~10%）
    buy_min: float = 0.22              # P75~P90 → 建仓/小仓试错（前 ~25%）
    wait_min: float = -0.20            # -0.20~P75 → 等待
    avoid_max: float = -0.20           # < -0.20 → 回避

    # ---- 已持仓动作阈值 ----
    hold_add_min: float = 0.42         # ≥ P90 趋势资金确认 → 持有/加仓
    hold_min: float = 0.22             # P75~P90 → 持有
    reduce_watch_max: float = 0.22     # -0.20~P75 → 持有不新增
    reduce_min: float = -0.50          # -0.50~-0.20 → 减仓
    exit_max: float = -0.50            # ≤ -0.50 → 清仓/止损

    # ---- 数据状态限制 ----
    # FRESH: 正常 / DEGRADED: 降仓 / STALE: 停止推荐 / FAILED: 停止决策

    # ---- 风险等级影响 ----
    risk_high_max_position: float = 0.05   # 高风险股票最大仓位 5%

    @classmethod
    def from_table(cls) -> "ActionThresholdsV2":
        """读最新截面分位数阈值；表为空或不可用时静默回退常量。"""
        try:
            from database.db import get_connection
            with get_connection(read_only=True) as connection:
                row = connection.execute(
                    "SELECT strong_buy_q, buy_q FROM cross_section_thresholds "
                    "ORDER BY data_cutoff DESC LIMIT 1"
                ).fetchone()
        except Exception:
            return cls()
        if row is None or row[0] is None or row[1] is None:
            return cls()
        strong_buy_q = float(row[0])
        buy_q = float(row[1])
        return replace(
            cls(),
            strong_buy_min=strong_buy_q,
            buy_min=buy_q,
            hold_add_min=strong_buy_q,
            hold_min=buy_q,
            reduce_watch_max=buy_q,
        )

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


__all__ = ["ActionThresholdsV2", "THRESHOLD_VERSION"]

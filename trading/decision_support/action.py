"""统一动作枚举（阶段1.5）

未持仓动作：
- STRONG_BUY：强烈建仓
- BUY：建仓
- SMALL_BUY：小仓试错
- WAIT：等待
- AVOID：回避

已持仓动作：
- ADD：加仓
- HOLD：持有
- REDUCE：减仓
- TAKE_PROFIT：止盈
- STOP_LOSS：止损
- EXIT：清仓

输出使用中文，内部保存稳定英文枚举。
禁止模糊词作为最终动作：建议关注/值得留意/可以看看/后续观察/自行判断/逢低关注
"""
from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    # 未持仓
    STRONG_BUY = "STRONG_BUY"    # 强烈建仓
    BUY = "BUY"                  # 建仓
    SMALL_BUY = "SMALL_BUY"      # 小仓试错
    WAIT = "WAIT"                # 等待
    AVOID = "AVOID"              # 回避
    # 已持仓
    ADD = "ADD"                  # 加仓
    HOLD = "HOLD"                # 持有
    REDUCE = "REDUCE"            # 减仓
    TAKE_PROFIT = "TAKE_PROFIT"  # 止盈
    STOP_LOSS = "STOP_LOSS"      # 止损
    EXIT = "EXIT"                # 清仓


# 中文展示映射
ACTION_ZH = {
    Action.STRONG_BUY: "强烈建仓",
    Action.BUY: "建仓",
    Action.SMALL_BUY: "小仓试错",
    Action.WAIT: "等待",
    Action.AVOID: "回避",
    Action.ADD: "加仓",
    Action.HOLD: "持有",
    Action.REDUCE: "减仓",
    Action.TAKE_PROFIT: "止盈",
    Action.STOP_LOSS: "止损",
    Action.EXIT: "清仓",
}

# 未持仓动作集合
NO_POSITION_ACTIONS = {Action.STRONG_BUY, Action.BUY, Action.SMALL_BUY, Action.WAIT, Action.AVOID}
# 已持仓动作集合
POSITION_ACTIONS = {Action.ADD, Action.HOLD, Action.REDUCE, Action.TAKE_PROFIT, Action.STOP_LOSS, Action.EXIT}

# 禁止使用的模糊词
FORBIDDEN_VAGUE_TERMS = [
    "建议关注", "值得留意", "可以看看", "后续观察", "自行判断", "逢低关注",
]


def action_zh(action: Action | str) -> str:
    """动作中文输出"""
    if isinstance(action, str):
        try:
            action = Action(action)
        except ValueError:
            return str(action)
    return ACTION_ZH.get(action, str(action))


def is_no_position_action(action: Action) -> bool:
    return action in NO_POSITION_ACTIONS


def is_position_action(action: Action) -> bool:
    return action in POSITION_ACTIONS

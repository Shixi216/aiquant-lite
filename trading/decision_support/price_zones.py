"""价格区间计算器 PriceZoneCalculator（阶段2.2）

输入：K线数据（计算ATR/前低/支撑/压力）+ 动作 + 当前价
输出：建仓/加仓/减仓/止盈区间 + 止损价/止损条件 + 失效条件

规则：
- 止损参考 ATR/前低/关键支撑/成本区/波动率，不得无依据固定5%或10%
- 目标位参考 前高/压力位/估值区间/风险收益比/情景分析
- 必须注明：价格区间是交易计划，不是自动委托
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trading.decision_support.action import Action
from trading.decision_support.trade_plan import TradePlan


@dataclass
class Bar:
    trade_date: object
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceZoneCalculator:
    """确定性价格区间计算"""

    @staticmethod
    def compute_atr(bars: list[Bar], period: int = 14) -> float:
        """ATR（平均真实波幅）"""
        if len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, min(period + 1, len(bars))):
            h, l, pc = bars[-i].high, bars[-i].low, bars[-i - 1].close
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0.0

    @staticmethod
    def support_resistance(bars: list[Bar], lookback: int = 20) -> tuple[float, float]:
        """近期支撑/压力（前低/前高）"""
        recent = bars[-lookback:]
        if not recent:
            return 0.0, 0.0
        support = min(b.low for b in recent)
        resistance = max(b.high for b in recent)
        return support, resistance

    def build_zones(
        self,
        bars: list[Bar],
        current_price: float,
        action: Action,
        *,
        confidence: float = 0.5,
        high_risk: bool = False,
        average_cost: Optional[float] = None,
    ) -> dict:
        """生成价格区间（返回 dict，含止损/止盈/失效条件）"""
        atr = self.compute_atr(bars)
        support, resistance = self.support_resistance(bars)
        zones = {
            "entry_zone": [], "add_zone": [], "reduce_zone": [],
            "take_profit_zone": [], "stop_loss_price": None,
            "stop_loss_condition": "", "invalidation_condition": "",
            "expected_holding_period": "", "risk_reward_ratio": 0.0,
        }

        if current_price <= 0:
            return zones

        # 止损价：ATR 或前低（取较严格者）
        atr_stop = current_price - 2.0 * atr if atr > 0 else None
        low_stop = support if support > 0 else None
        candidates = [s for s in (atr_stop, low_stop) if s is not None]
        stop = min(candidates) if candidates else current_price * 0.92

        # 无持仓：建仓区间 = 回调至支撑附近
        if action in (Action.STRONG_BUY, Action.BUY, Action.SMALL_BUY):
            entry_low = max(support, current_price * 0.95) if support > 0 else current_price * 0.95
            entry_high = current_price * 1.01
            # 防止 support > current_price 导致 low > high
            if entry_low > entry_high:
                entry_low = current_price * 0.95
            zones["entry_zone"] = [round(entry_low, 2), round(entry_high, 2)]
            # 优选建仓区间：下沿=允许区间下沿，上沿=下沿+40%×宽度
            width = entry_high - entry_low
            if width > 0:
                preferred_high = entry_low + 0.40 * width
                zones["preferred_zone"] = [round(entry_low, 2), round(preferred_high, 2)]
            zones["add_zone"] = [round(entry_high * 1.02, 2), round(entry_high * 1.08, 2)]
            # 止盈：取压力位与当前价的较高者（保证高于建仓区）
            tp_low = max(resistance, entry_high * 1.05) if resistance > 0 else entry_high * 1.08
            zones["take_profit_zone"] = [round(tp_low, 2), round(tp_low * 1.06, 2)]
            zones["stop_loss_price"] = round(stop, 2)
            zones["stop_loss_condition"] = f"收盘跌破 {stop:.2f} 元或重大风险公告出现"
            zones["invalidation_condition"] = f"收盘跌破 {support * 0.97:.2f} 元（支撑失效）" if support > 0 else ""
            zones["expected_holding_period"] = "2至8周"
            # 风险收益比
            if zones["take_profit_zone"] and zones["stop_loss_price"]:
                upside = max(zones["take_profit_zone"]) - current_price
                downside = current_price - zones["stop_loss_price"]
                if downside > 0:
                    zones["risk_reward_ratio"] = round(upside / downside, 2)

        # 已持仓：加仓/减仓/止盈区间
        elif action == Action.ADD:
            zones["add_zone"] = [round(current_price * 0.97, 2), round(current_price * 1.02, 2)]
            zones["take_profit_zone"] = [round(resistance, 2), round(resistance * 1.05, 2)]
            zones["stop_loss_price"] = round(stop, 2)
            zones["invalidation_condition"] = f"收盘跌破 {stop:.2f} 元"
        elif action in (Action.REDUCE, Action.TAKE_PROFIT):
            zones["reduce_zone"] = [round(current_price * 0.98, 2), round(current_price * 1.05, 2)]
            zones["invalidation_condition"] = f"收盘跌破 {stop:.2f} 元则加速减仓"
        elif action in (Action.HOLD, Action.WAIT, Action.AVOID):
            zones["invalidation_condition"] = f"收盘跌破 {stop:.2f} 元（止损参考）"
            zones["stop_loss_price"] = round(stop, 2)

        return zones

    def apply_to_plan(self, plan: TradePlan, bars: list[Bar], action: Action,
                      confidence: float = 0.5, average_cost: Optional[float] = None) -> TradePlan:
        """把价格区间应用到 TradePlan"""
        zones = self.build_zones(bars, plan.current_price, action,
                                 confidence=confidence, average_cost=average_cost)
        plan.entry_zone = zones["entry_zone"]
        plan.add_zone = zones["add_zone"]
        plan.reduce_zone = zones["reduce_zone"]
        plan.take_profit_zone = zones["take_profit_zone"]
        plan.stop_loss_price = zones["stop_loss_price"]
        plan.stop_loss_condition = zones["stop_loss_condition"]
        plan.invalidation_condition = zones["invalidation_condition"]
        plan.expected_holding_period = zones["expected_holding_period"]
        plan.risk_reward_ratio = zones["risk_reward_ratio"]
        return plan

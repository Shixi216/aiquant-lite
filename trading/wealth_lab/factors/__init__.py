"""ETF 多因子研究评分（动量/趋势/波动/回撤/流动性 → 综合分位）。

仅用于研究排序，不修改原股票系统正式评分。
"""
from trading.wealth_lab.factors.score import score_factors

__all__ = ["score_factors"]

"""组合优化（适配器：pypfopt 可选，纯 numpy 自实现兜底）。

中国市场适配：输入为人民币计价的场内基金后复权收益；输出标准化目标权重
（长仓、权重和=1、单资产上限可配）。不引入海外行情或交易假设。
"""
from trading.wealth_lab.optimization.adapter import optimize_portfolio

__all__ = ["optimize_portfolio"]

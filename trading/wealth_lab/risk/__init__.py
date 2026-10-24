"""Wealth Lab 独立风险策略与引擎（不复制股票风险阈值）。

四类检查：DATA / ASSET / PORTFOLIO / AI。输出 PASS / WARN / VETO /
INSUFFICIENT_DATA。硬规则由确定性程序执行；AI 仅提供解释。
被削减的权重保留为现金，不自动重新分配。
阈值可配置且有来源说明；研究演示不冒充正式适当性认定。
"""
from trading.wealth_lab.risk.engine import WealthRiskEngine, WealthRiskPolicy

__all__ = ["WealthRiskEngine", "WealthRiskPolicy"]

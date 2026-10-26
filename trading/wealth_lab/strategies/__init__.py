"""Wealth Lab 策略工厂（v2）：统一 Strategy 接口与注册机制。"""
from trading.wealth_lab.strategies.base import (
    Strategy,
    StrategyMeta,
    register_strategy,
    list_strategies,
    get_strategy,
)
from trading.wealth_lab.strategies import builtin, optimized  # noqa: F401  # 注册副作用

__all__ = ["Strategy", "StrategyMeta", "register_strategy", "list_strategies", "get_strategy"]

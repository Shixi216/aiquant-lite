"""PIT-safe, research-only strategy discovery for AIQUANT-LITE.

This package intentionally has no dependency on execution, broker, or order code.
"""

from trading.research.strategy_lab.dataset_builder import StrategyLabDatasetBuilder

__all__ = ["StrategyLabDatasetBuilder"]

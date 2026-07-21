"""Broker-specific adapters. No live adapter is enabled in this release."""

from trading.adapters.qmt_xtquant import CiticQmtXtquantPlaceholder

__all__ = ["CiticQmtXtquantPlaceholder"]

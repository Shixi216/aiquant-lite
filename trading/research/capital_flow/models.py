from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketBar:
    canonical_record_id: str
    symbol: str
    event_time: datetime
    data_cutoff: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    turnover_rate: float | None = None
    float_shares: float | None = None
    sector: str | None = None
    unit_risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinancingRecord:
    record_id: str
    symbol: str
    event_time: datetime
    data_cutoff: datetime
    financing_balance: float | None
    financing_buy_amount: float | None
    securities_lending_balance: float | None
    source: str


@dataclass(frozen=True)
class EstimatedFlow:
    value: float
    provider: str
    methodology: str
    confidence: float
    evidence_ids: tuple[str, ...]


__all__ = ["EstimatedFlow", "FinancingRecord", "MarketBar"]

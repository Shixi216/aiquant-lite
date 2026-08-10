from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading.research.capital_flow.models import FinancingRecord
from trading.research.capital_flow.schemas import (
    CapitalFlowRiskFlag,
    FinancingTrend,
)


class FinancingDataSource(Protocol):
    def records(self, symbol: str, *, data_cutoff: object) -> list[FinancingRecord]: ...


@dataclass(frozen=True)
class FinancingFeatures:
    financing_balance: float | None
    financing_balance_change_1d: float | None
    financing_balance_change_5d: float | None
    financing_buy_amount: float | None
    securities_lending_balance: float | None
    trend: FinancingTrend
    evidence_ids: tuple[str, ...]
    risk_flags: tuple[CapitalFlowRiskFlag, ...]


def calculate_financing_features(records: list[FinancingRecord]) -> FinancingFeatures:
    ordered = sorted(records, key=lambda item: item.event_time)
    balances = [
        record.financing_balance
        for record in ordered
        if record.financing_balance is not None
    ]
    if not ordered or not balances:
        return FinancingFeatures(
            None,
            None,
            None,
            None,
            None,
            FinancingTrend.MISSING,
            (),
            (CapitalFlowRiskFlag.FINANCING_DATA_MISSING,),
        )
    current = balances[-1]
    change1 = current - balances[-2] if len(balances) >= 2 else None
    change5 = current - balances[-6] if len(balances) >= 6 else None
    recent_balances = balances[-6:]
    recent_changes = [
        b - a
        for a, b in zip(recent_balances[:-1], recent_balances[1:])
    ]
    if recent_changes and all(value > 0 for value in recent_changes):
        trend = FinancingTrend.RISING
    elif recent_changes and all(value < 0 for value in recent_changes):
        trend = FinancingTrend.FALLING
    elif recent_changes and max(recent_changes) > 0 > min(recent_changes):
        trend = FinancingTrend.VOLATILE
    else:
        trend = FinancingTrend.STABLE
    latest = ordered[-1]
    return FinancingFeatures(
        current,
        change1,
        change5,
        latest.financing_buy_amount,
        latest.securities_lending_balance,
        trend,
        tuple(record.record_id for record in ordered),
        (),
    )


__all__ = [
    "FinancingDataSource",
    "FinancingFeatures",
    "calculate_financing_features",
]
